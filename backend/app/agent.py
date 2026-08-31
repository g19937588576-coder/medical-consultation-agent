"""LangGraph Agent：安全护栏 → 意图理解 → 多轮追问 → 资料查询 → 分级回答。"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig

from . import safety
from .config import settings
from .llm import chat, chat_stream, parse_json
from .mcp_tools import extract_citations, mcp_client

SYSTEM_PROMPT = """你是「健康咨询助手」，面向中文用户提供基于权威医学资料的健康信息咨询。

必须遵守的边界：
1. 你不是医生，不提供诊断，不开处方。永远不要给出明确诊断结论或具体用药方案。
2. 只能基于「资料」字段中提供的检索结果进行回答，回答中的每条关键信息必须标注引用编号 [1][2]…，对应「参考资料列表」。严禁编造来源。
3. 仅当用户咨询的是症状（描述身体不适）时，回答结尾必须单独一行输出分诊建议，格式严格为：
   分诊等级：🟢（可自行观察）/ 🟡（建议尽快就医）/ 🔴（需立即急诊）——简短理由
   药品、文献、一般健康话题的问题不要输出分诊等级行。
4. 回答使用简体中文，通俗易懂，长度适中（300 字以内），先给结论再给解释。
5. 结尾固定附加一句：以上内容仅为健康信息参考，不能替代医生面诊；如有不适请及时就医。
6. 如果检索结果不足以回答，请如实说明，并给出就医建议，不要猜测。
7. 引用编号只能引用「参考资料列表」中实际存在的条目。如果参考资料列表为空（本次没有检索到资料），禁止使用 [1][2] 这类引用标注，应直接说明资料不足。
"""


class AgentState(TypedDict, total=False):
    session_id: int
    user_input: str
    transcript: list[dict]      # 本会话历史
    intent: str                 # symptom / drug / literature / other
    facts: dict                 # 已收集的问诊信息
    question_rounds: int
    citations: list[dict]
    triage_level: str | None
    output: str
    reason: str                 # emergency/refusal/diagnosis/question/answer
    done: bool


# 会话级记忆（进程内，重启即清空；演示足够）
_session_store: dict[int, dict[str, Any]] = {}


def get_session_memory(session_id: int) -> dict[str, Any]:
    mem = _session_store.get(session_id)
    if mem is None:
        mem = {"facts": {}, "rounds": 0, "intent": None}
        _session_store[session_id] = mem
    return mem


def append_transcript(session_id: int, role: str, content: str) -> None:
    mem = get_session_memory(session_id)
    mem.setdefault("transcript", []).append({"role": role, "content": content})


def reset_session_memory(session_id: int) -> None:
    _session_store.pop(session_id, None)


def _queue(config: RunnableConfig | None) -> asyncio.Queue | None:
    return config.get("configurable", {}).get("event_queue")


def _push(config: RunnableConfig | None, event: dict) -> None:
    q = _queue(config)
    if q is not None:
        try:
            q.put_nowait(event)
        except Exception:
            pass


INTENT_PROMPT = """你是问诊信息理解助手。请分析用户最新一条消息（结合上文），输出 JSON：
{"intent": "symptom" 或 "drug" 或 "literature" 或 "other", "facts": {...}}
- intent: symptom=描述症状身体不适；drug=询问药品；literature=想了解某主题的研究/文献；other=其他健康话题
- facts 尽量从对话中提取：{"age":"年龄","duration":"持续多久","severity":"严重程度","allergies":"过敏史","medications":"正在服用的药","symptom":"主要症状"}，未知的填空字符串。
只输出 JSON。"""


def _missing_facts(facts: dict) -> list[str]:
    order = ["duration", "severity", "age"]
    return [k for k in order if not str(facts.get(k) or "").strip()]


ASK_TEMPLATES = {
    "duration": "这种情况持续多久了？是刚出现，还是已经有一段时间了？",
    "severity": "症状有多严重？是否影响正常生活、睡眠或工作？有没有越来越重？",
    "age": "方便告诉我您的年龄吗？不同年龄段需要考虑的方向差别很大。",
}


async def safety_node(state: AgentState, config: RunnableConfig) -> dict:
    text = state.get("user_input", "")
    if safety.check_emergency(text):
        _push(config, {"type": "guardrail", "kind": "emergency"})
        return {"output": safety.EMERGENCY_RESPONSE, "reason": "emergency", "done": True}
    if safety.check_prescription(text):
        _push(config, {"type": "guardrail", "kind": "prescription"})
        return {"output": safety.REFUSAL_RESPONSE, "reason": "refusal", "done": True}
    if safety.check_diagnosis(text):
        _push(config, {"type": "guardrail", "kind": "diagnosis"})
        return {"output": safety.DIAGNOSIS_RESPONSE, "reason": "diagnosis", "done": True}
    return {"done": False}


async def understand_node(state: AgentState) -> dict:
    session_id = state.get("session_id", 0)
    transcript = state.get("transcript") or []
    recent = transcript[-6:]
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        *recent,
    ]
    try:
        raw = await chat(messages, temperature=0, json_mode=True, max_tokens=500)
        parsed = parse_json(raw)
    except Exception:
        parsed = {}
    intent = parsed.get("intent", "other")
    if intent not in {"symptom", "drug", "literature", "other"}:
        intent = "other"
    new_facts = parsed.get("facts") or {}
    mem = get_session_memory(session_id)
    merged = {**mem.get("facts", {}), **{k: v for k, v in new_facts.items() if str(v or "").strip()}}
    mem["facts"] = merged
    mem["intent"] = intent
    return {
        "intent": intent,
        "facts": merged,
        "question_rounds": mem.get("rounds", 0),
    }


async def ask_node(state: AgentState, config: RunnableConfig) -> dict:
    session_id = state.get("session_id", 0)
    mem = get_session_memory(session_id)
    facts = mem.get("facts", {})
    missing = _missing_facts(facts)
    key = missing[0] if missing else "duration"
    question = ASK_TEMPLATES.get(key, "能否再多描述一些症状细节？")
    mem["rounds"] = mem.get("rounds", 0) + 1
    return {
        "output": question,
        "reason": "question",
        "done": False,
        "question_rounds": mem.get("rounds", 0),
    }


async def _extract_drugs(text: str) -> list[str]:
    """提取药品名并翻译为英文通用名。"""
    prompt = (
        "你是药学检索助手。从用户问题中提取提到的药品名，并给出对应的英文通用名。"
        '只输出 JSON：{"drugs": ["英文药品名1", "英文药品名2"]}\n'
        f"输入：{text}"
    )
    try:
        raw = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0,
            json_mode=True,
            max_tokens=200,
        )
        parsed = parse_json(raw)
        drugs = parsed.get("drugs") or []
        return [str(d).strip() for d in drugs if str(d).strip()][:2]
    except Exception:
        return []


async def _to_english(text: str, kind: str) -> str:
    """把中文描述转成适合 PubMed 检索的英文关键词。"""
    prompt = (
        f"你是医学检索助手。把下面的{kind}描述转成适合在 PubMed 检索的英文关键词，"
        "只输出英文关键词本身（可含空格），不要解释，不要加引号。\n"
        f"输入：{text}"
    )
    try:
        out = await chat(
            [{"role": "user", "content": prompt}], temperature=0, max_tokens=120
        )
        return out.strip().strip('"').strip()
    except Exception:
        return text


async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    intent = state.get("intent", "other")
    citations: list[dict] = []
    user_input = state.get("user_input", "")
    facts = state.get("facts") or {}
    symptom = str(facts.get("symptom") or "").strip() or user_input

    if intent == "drug":
        drugs = await _extract_drugs(user_input)
        if not drugs:
            drugs = [await _to_english(user_input, "药品")]
        for drug in drugs[:2]:
            _push(config, {"type": "tool_call", "tool": "search-drugs", "label": f"正在查询 FDA 药品数据库：{drug}…"})
            result = await mcp_client.call_tool("search-drugs", {"query": drug, "limit": 3})
            citations.extend(extract_citations("search-drugs", result.get("data") or {}))
    else:
        if intent == "literature":
            en = await _to_english(user_input, "研究主题")
        elif intent == "symptom":
            en = await _to_english(symptom, "症状")
        else:
            en = await _to_english(user_input, "健康问题")
        _push(config, {"type": "tool_call", "tool": "search-medical-literature", "label": "正在检索 PubMed 医学文献…"})
        result = await mcp_client.call_tool("search-medical-literature", {"query": en, "max_results": 5})
        citations.extend(extract_citations("search-medical-literature", result.get("data") or {}))

    if not citations:
        mem = get_session_memory(state.get("session_id", 0))
        citations = mem.get("last_citations", [])
    else:
        mem = get_session_memory(state.get("session_id", 0))
        mem["last_citations"] = citations

    return {"citations": citations[:5]}

def _tool_summary(state: AgentState) -> str:
    citations = state.get("citations") or []
    if not citations:
        return "（本次没有检索到可用资料）"
    lines = []
    for i, c in enumerate(citations, 1):
        line = f"[{i}] {c.get('title', '')} — 来源：{c.get('source', '')}（{c.get('url', '')}）"
        snippet = (c.get("snippet") or "").strip()
        if snippet:
            line += f"\n    摘要：{snippet[:400]}"
        lines.append(line)
    return "\n".join(lines)


async def synthesize_node(state: AgentState, config: RunnableConfig) -> dict:
    intent = state.get("intent", "other")
    facts = state.get("facts") or {}
    transcript = state.get("transcript") or []
    citations = state.get("citations") or []

    fact_lines = []
    for k, label in [("symptom", "主要症状"), ("duration", "持续时间"), ("severity", "严重程度"), ("age", "年龄"), ("allergies", "过敏史"), ("medications", "正在服药")]:
        if str(facts.get(k) or "").strip():
            fact_lines.append(f"{label}：{facts[k]}")

    user_q = state.get("user_input", "")
    content = f"用户问题：{user_q}"
    if fact_lines:
        content += "\n已收集信息：" + "；".join(fact_lines)
    content += f"\n\n资料：\n{_tool_summary(state)}\n\n参考资料列表：\n" + "\n".join(
        f"{i}. {c.get('title')}（{c.get('source')}）{c.get('url')}" for i, c in enumerate(citations, 1)
    ) if citations else "参考资料列表：（无）"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *transcript[-6:],
        {"role": "user", "content": content},
    ]

    full = ""
    try:
        async for token in chat_stream(messages, temperature=0.4):
            full += token
            _push(config, {"type": "token", "text": token})
    except Exception as exc:  # noqa: BLE001
        _push(config, {"type": "error", "detail": str(exc)})
        full = full or "抱歉，我暂时无法生成回答，请稍后重试。"

    triage = None
    if intent == "symptom":
        m = re.search(r"分诊等级：\s*(🟢|🟡|🔴)", full)
        if m:
            triage = {"🟢": "green", "🟡": "yellow", "🔴": "red"}[m.group(1)]

    return {"output": full, "triage_level": triage, "reason": "answer", "done": True}


def _route_after_safety(state: AgentState) -> str:
    return "end" if state.get("done") else "understand"


def _route_after_understand(state: AgentState) -> str:
    mem_intent = state.get("intent")
    rounds = state.get("question_rounds", 0)
    if (
        mem_intent == "symptom"
        and _missing_facts(state.get("facts") or {})
        and rounds < settings.max_question_rounds
    ):
        return "ask"
    return "tools"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("safety", safety_node)
    g.add_node("understand", understand_node)
    g.add_node("ask", ask_node)
    g.add_node("tools", tools_node)
    g.add_node("synthesize", synthesize_node)
    g.add_edge(START, "safety")
    g.add_conditional_edges("safety", _route_after_safety, {"end": END, "understand": "understand"})
    g.add_conditional_edges("understand", _route_after_understand, {"ask": "ask", "tools": "tools"})
    g.add_edge("ask", END)
    g.add_edge("tools", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


agent_graph = build_graph()






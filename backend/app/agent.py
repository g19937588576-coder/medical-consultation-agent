"""LangGraph Agent：安全护栏 → 命令处理 → 同意征询 → 意图理解 → 多轮追问 → 资料查询 → 分级回答。"""
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
8. 必须围绕用户最初描述的症状（「主要症状」字段）作答，即使用户最新一条消息很短。如果检索资料与主要症状不相关，请明确说明"检索到的资料与您描述的症状不直接相关"，并基于常见健康常识给出针对该症状的保守建议（例如牙痛→保持口腔清洁、避免过冷过热刺激、尽快就诊口腔科），严禁给出与用户症状无关的部位建议（如腹部、胸部、腰部等）。
"""


class AgentState(TypedDict, total=False):
    session_id: int
    user_input: str
    transcript: list[dict]      # 本会话历史
    intent: str                 # symptom / drug / literature / other
    facts: dict                 # 已收集的健康档案（profile 的别名）
    question_rounds: int
    citations: list[dict]
    triage_level: str | None
    output: str
    reason: str                 # forgot/view/emergency/refusal/diagnosis/question/answer
    done: bool


# 会话级记忆（进程内，重启即清空；演示足够）
_session_store: dict[int, dict[str, Any]] = {}

# 健康档案字段：稳定属性先到先得；动态属性后到覆盖
STABLE_FIELDS = {
    "age", "gender", "height", "weight", "allergies", "conditions",
    "family_history", "medications", "lifestyle", "symptom", "pain_type",
}
DYNAMIC_FIELDS = {"duration", "severity"}

PROFILE_LABELS = {
    "age": "年龄", "gender": "性别", "height": "身高", "weight": "体重",
    "allergies": "过敏史", "conditions": "既往病史", "family_history": "家族史",
    "medications": "在服药物/保健品", "lifestyle": "生活习惯",
    "symptom": "主要症状", "duration": "持续时间", "severity": "严重程度",
    "pain_type": "疼痛性质",
}

CONSENT_QUESTION = (
    "在继续之前，我想先确认一件事：为了方便给您更准确的建议，我会记录您"
    "**本次对话中提到的健康信息**（如年龄、过敏史、病史、用药等）。可以吗？"
    "您也可以随时说「忘记我的信息」来清除。回复“可以”即可。"
)

FORGET_PATTERNS = [
    "忘记我的信息", "清除我的信息", "清除档案", "删除记录",
    "删除我的信息", "忘掉我的信息", "清除我的记录",
]
VIEW_PATTERNS = [
    "查看我的信息", "看看我的信息", "我提供了什么", "我的档案",
    "查看档案", "查一下我的信息", "我有什么信息",
]
CONSENT_YES = ["可以", "同意", "愿意", "没问题", "当然", "好的", "好呀", "嗯", "行", "记吧", "ok", "okay"]
CONSENT_NO = ["不用", "不要", "算了", "不必", "拒绝", "不需要", "不了", "no", "不记"]

FORGET_RESPONSE = "好的，我已经清除本次对话中记录的您的健康信息（档案已清空）。之后您可以重新告诉我需要记录的内容。"
VIEW_EMPTY_RESPONSE = "本次对话中还没有记录任何健康信息。"
VIEW_HEADER = "您本次对话中已记录的健康信息："


def get_session_memory(session_id: int) -> dict[str, Any]:
    mem = _session_store.get(session_id)
    if mem is None:
        mem = {"profile": {}, "consent": None, "rounds": 0, "intent": None}
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


def _merge_facts(mem: dict, new_facts: dict) -> dict:
    """合并健康档案：稳定属性先到先得，动态属性后到覆盖。"""
    profile = mem.get("profile", {})
    for k, v in new_facts.items():
        val = str(v or "").strip()
        if not val:
            continue
        if k in STABLE_FIELDS and str(profile.get(k) or "").strip():
            continue
        profile[k] = val
    mem["profile"] = profile
    return profile


def _consent_reply(text: str) -> bool | None:
    """识别用户对同意征询的回复：True=同意，False=拒绝，None=未明确。"""
    t = text.lower().strip()
    if any(k in t for k in CONSENT_NO):
        return False
    if any(k in t for k in CONSENT_YES):
        return True
    return None


def _missing_profile(profile: dict) -> list[str]:
    order = ["age", "allergies", "conditions", "medications", "duration", "severity"]
    return [k for k in order if not str(profile.get(k) or "").strip()]


def _profile_summary(mem: dict) -> str:
    profile = mem.get("profile") or {}
    if not profile:
        return VIEW_EMPTY_RESPONSE
    lines = []
    for k, label in PROFILE_LABELS.items():
        if str(profile.get(k) or "").strip():
            lines.append(f"· {label}：{profile[k]}")
    if not lines:
        return VIEW_EMPTY_RESPONSE
    return VIEW_HEADER + "\n" + "\n".join(lines) + "\n（如不需要，可随时说「忘记我的信息」清除）"


INTENT_PROMPT = """你是问诊信息理解助手。请分析用户最新一条消息（结合上文），输出 JSON：
{"intent": "symptom" 或 "drug" 或 "literature" 或 "other", "facts": {...}}
- intent: symptom=描述症状身体不适；drug=询问药品；literature=想了解某主题的研究/文献；other=其他健康话题
- facts 尽量从对话中提取，未知的填空字符串：
{"age":"年龄","gender":"性别","height":"身高","weight":"体重","allergies":"过敏史","conditions":"慢性病/既往病史","family_history":"家族史","medications":"正在服用的药或保健品","lifestyle":"生活习惯(吸烟/饮酒/作息等)","symptom":"主要症状——必须是用户最初描述症状的完整原文","duration":"持续多久","severity":"严重程度","pain_type":"疼痛性质(阵痛/钝痛/刺痛/胀痛等)"}
只输出 JSON。"""


ASK_TEMPLATES = {
    "age": "方便告诉我您的年龄吗？不同年龄段需要考虑的方向差别很大。",
    "allergies": "您有没有药物或食物过敏史？",
    "conditions": "您是否有已知的慢性病或既往病史（如高血压、糖尿病、哮喘等）？",
    "medications": "您目前有在服用什么药物或保健品吗？",
    "duration": "这种情况持续多久了？是刚出现，还是已经有一段时间了？",
    "severity": "症状有多严重？是否影响正常生活、睡眠或工作？有没有越来越重？",
}


async def safety_node(state: AgentState, config: RunnableConfig) -> dict:
    text = state.get("user_input", "")
    sid = state.get("session_id", 0)
    mem = get_session_memory(sid)
    # 命令优先：清除 / 查看档案
    if any(k in text for k in FORGET_PATTERNS):
        mem["profile"] = {}
        mem["consent"] = None
        mem["rounds"] = 0
        _push(config, {"type": "guardrail", "kind": "forget"})
        return {"output": FORGET_RESPONSE, "reason": "forgot", "done": True}
    if any(k in text for k in VIEW_PATTERNS):
        _push(config, {"type": "guardrail", "kind": "view"})
        return {"output": _profile_summary(mem), "reason": "view", "done": True}
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
    text = state.get("user_input", "")
    transcript = state.get("transcript") or []
    recent = transcript[-6:]
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        *recent,
    ]
    try:
        raw = await chat(messages, temperature=0, json_mode=True, max_tokens=600)
        parsed = parse_json(raw)
    except Exception:
        parsed = {}
    intent = parsed.get("intent", "other")
    if intent not in {"symptom", "drug", "literature", "other"}:
        intent = "other"

    mem = get_session_memory(session_id)
    # 同意征询结果：仅在尚未确定且用户在回答征询时判定
    if mem.get("consent") is None:
        reply = _consent_reply(text)
        if reply is True:
            mem["consent"] = True
        elif reply is False:
            mem["consent"] = False
        # 未明确回复则保持 None，等待再次征询

    new_facts = parsed.get("facts") or {}
    profile = _merge_facts(mem, new_facts)
    mem["intent"] = intent
    return {
        "intent": intent,
        "facts": profile,
        "question_rounds": mem.get("rounds", 0),
    }


async def ask_node(state: AgentState, config: RunnableConfig) -> dict:
    session_id = state.get("session_id", 0)
    mem = get_session_memory(session_id)
    if mem.get("consent") is None:
        question = CONSENT_QUESTION
    else:
        profile = mem.get("profile", {})
        missing = _missing_profile(profile)
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
    """把中文描述转成适合 PubMed 检索的英文关键词（短关键词，非整句）。"""
    prompt = (
        f"你是医学检索助手。把下面的{kind}描述转成适合在 PubMed 检索的简短英文关键词，"
        "只输出 3-8 个英文关键词（如 headache afternoon adult），不要完整句子、不要标点、不要解释。\n"
        f"输入：{text}"
    )
    try:
        out = await chat(
            [{"role": "user", "content": prompt}], temperature=0, max_tokens=120
        )
        return out.strip().strip('"').strip()
    except Exception:
        return text


def _literature_query(state: AgentState) -> str:
    """基于原始症状 + 关键事实组装检索描述，避免用最后一条短消息检索。"""
    facts = state.get("facts") or {}
    parts = []
    symptom = str(facts.get("symptom") or "").strip()
    if symptom:
        parts.append(symptom)
    if str(facts.get("duration") or "").strip():
        parts.append("持续" + facts["duration"])
    if str(facts.get("age") or "").strip():
        parts.append(facts["age"] + "岁")
    if str(facts.get("pain_type") or "").strip():
        parts.append(facts["pain_type"])
    if parts:
        return "，".join(parts)[:150]
    return (state.get("user_input") or "")[:120]


async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    intent = state.get("intent", "other")
    citations: list[dict] = []
    user_input = state.get("user_input", "")
    facts = state.get("facts") or {}

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
            retry_src = user_input
            retry_kind = "检索词"
        elif intent == "symptom":
            en = await _to_english(_literature_query(state), "症状")
            retry_src = str(facts.get("symptom") or "").strip() or user_input
            retry_kind = "症状"
        else:
            en = await _to_english(user_input, "健康问题")
            retry_src = user_input
            retry_kind = "检索词"
        _push(config, {"type": "tool_call", "tool": "search-medical-literature", "label": "正在检索 PubMed 医学文献…"})
        result = await mcp_client.call_tool("search-medical-literature", {"query": en, "max_results": 5})
        citations.extend(extract_citations("search-medical-literature", result.get("data") or {}))
        if not citations and retry_src:
            en2 = await _to_english(retry_src, retry_kind)
            if en2 and en2 != en:
                _push(config, {"type": "tool_call", "tool": "search-medical-literature", "label": "正在用更简短的关键词重新检索…"})
                result2 = await mcp_client.call_tool("search-medical-literature", {"query": en2, "max_results": 5})
                citations.extend(extract_citations("search-medical-literature", result2.get("data") or {}))

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
    for k, label in PROFILE_LABELS.items():
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
    if state.get("intent") != "symptom":
        return "tools"
    rounds = state.get("question_rounds", 0)
    if rounds >= settings.max_question_rounds:
        return "tools"
    mem = get_session_memory(state.get("session_id", 0))
    if mem.get("consent") is None:
        return "ask"  # 先征得同意
    if mem.get("consent") is False:
        return "tools"  # 无记忆模式：仅基于当前消息作答
    if _missing_profile(state.get("facts") or {}):
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


"""LangGraph Agent：安全护栏 → 命令处理 → 同意征询 → 对症追问 → 意图理解 → 资料查询 → 分级回答。"""
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
from .knowledge_base import kb_citations
from .mcp_tools import extract_citations, mcp_client

SYSTEM_PROMPT = """你是「健康咨询助手」，面向中文用户提供基于权威医学资料的健康信息咨询。

必须遵守的边界：
1. 你不是医生，不提供诊断，不开处方。永远不要给出明确诊断结论或具体用药方案。
2. 只能基于「资料」字段中提供的检索结果进行回答，回答中的每条关键信息必须标注引用编号 [1][2]…，对应「参考资料列表」。严禁编造来源。
3. 仅当用户咨询的是症状（描述身体不适）时，回答结尾**必须且只能**单独一行输出分诊建议（不要遗漏），格式严格为：
   分诊等级：🟢（可自行观察）/ 🟡（建议尽快就医）/ 🔴（需立即急诊）——简短理由
   药品、文献、一般健康话题的问题不要输出分诊等级行。
4. 回答使用简体中文，通俗易懂，长度适中（300 字以内），先给结论再给解释。
5. 结尾固定附加一句：以上内容仅为健康信息参考，不能替代医生面诊；如有不适请及时就医。
6. 如果检索结果不足以回答，请如实说明，并给出就医建议，不要猜测。
7. 引用编号只能引用「参考资料列表」中实际存在的条目。如果参考资料列表为空（本次没有检索到资料），禁止使用 [1][2] 这类引用标注，应直接说明资料不足。
8. 引用纪律：只有当某篇文献与用户的问题/症状直接相关时，才允许引用它。参考资料列表中不相关的文献一律不得引用、不得提及。宁可完全没有引用，也不要引用不相关文献。
9. 必须围绕用户最初描述的症状（「主要症状」与「补充症状细节」字段）作答，即使用户最新一条消息很短。如果检索资料与主要症状不相关，请明确说明"检索到的资料与您描述的症状不直接相关"，并基于常见健康常识给出针对该症状的保守建议（例如牙痛→保持口腔清洁、避免过冷过热刺激、尽快就诊口腔科），严禁给出与用户症状无关的部位建议（如腹部、胸部、腰部等）。
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
    "symptom": "主要症状", "symptom_details": "补充症状细节",
    "duration": "持续时间", "severity": "严重程度", "pain_type": "疼痛性质",
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

# ===== 对症追问知识库：症状关键词 → 针对性问题 =====
# 顺序即优先级（更具体的类别在前）
SYMPTOM_QUESTIONS: list[tuple[str, list[str], list[str]]] = [
    ("dental", ["牙", "齿", "牙龈", "蛀"], [
        "牙齿上有没有看到黑点、蛀洞或变色的地方？",
        "牙龈有没有红肿、出血或鼓包？",
        "疼痛是遇冷、热、甜食才疼，还是不吃东西也自发地疼？",
        "有没有夜间加重，或牵扯到脸颊、耳朵的疼痛？",
    ]),
    ("nausea", ["恶心", "呕吐", "反胃", "想吐", "干呕"], [
        "恶心和吃饭有关系吗？是空腹明显还是饭后明显？",
        "是干呕还是真的吐出来了？吐出来的是什么？",
        "有没有伴随腹痛、腹泻、发热或头晕？",
    ]),
    ("back", ["腰", "背"], [
        "疼痛是酸胀还是刺痛？持续多久了？",
        "弯腰、久坐或负重的时候会不会加重？",
        "有没有放射到腿部、或者腿麻、无力？",
    ]),
    ("abdominal", ["肚子", "腹", "胃", "肚脐", "肠"], [
        "疼痛的具体位置在哪（上腹、下腹还是肚脐周围）？",
        "疼痛和吃饭、排便有没有关系？",
        "有没有伴随腹泻、便秘、恶心、呕吐或发热？",
    ]),
    ("cough", ["咳", "痰"], [
        "是干咳还是有痰？痰是什么颜色？",
        "咳嗽多久了？白天还是晚上更明显？",
        "有没有伴随发热、胸痛或气短？",
    ]),
    ("fever", ["发烧", "发热", "烧"], [
        "体温大概多少度？最高到过多少？",
        "发热几天了？",
        "有没有伴随咳嗽、咽痛、皮疹或其他不舒服？",
    ]),
    ("joint", ["关节", "膝盖", "腰", "肩", "腿"], [
        "疼痛的部位有没有红肿、发热？",
        "休息后好转，还是活动后加重？早上起来有没有僵硬？",
        "近期有没有受过伤或过度运动？",
    ]),
    ("throat", ["咽", "喉", "嗓子"], [
        "吞咽的时候疼不疼？",
        "有没有声音嘶哑、咳嗽或发热？",
        "这种情况持续多久了？",
    ]),
    ("dizzy", ["晕", "眩"], [
        "是持续的头晕，还是一阵一阵的眩晕（感觉天旋地转）？",
        "头晕和起身、转头有没有关系？",
        "有没有伴随耳鸣、听力下降或恶心？",
    ]),
    ("rash", ["疹", "皮"], [
        "皮疹长在哪些部位？",
        "有没有瘙痒、疼痛或发热？",
        "最近有没有接触新东西、吃过新食物或用过新药？",
    ]),
    ("fatigue", ["乏", "累", "无力"], [
        "这种情况持续多久了？",
        "有没有伴随睡眠不好、食欲下降或体重变化？",
        "有没有贫血、甲状腺等已知问题？",
    ]),
    ("chest", ["胸", "心口"], [
        "疼痛是压榨感、闷痛还是刺痛？持续多久？",
        "有没有放射到左臂、后背或下巴？有没有气短、出冷汗？",
    ]),
    ("headache", ["头", "偏头痛"], [
        "疼痛是偏一侧，还是整个头都疼？",
        "有没有伴随恶心、呕吐、怕光或怕吵？",
        "疼痛是一跳一跳的搏动感，还是像紧箍一样的压迫感？",
    ]),
]
GENERIC_SYMPTOM_QUESTION = "能不能再多描述一些症状细节？比如具体位置、感觉和什么情况下会加重。"
SYMPTOM_Q_MAX = 3  # 每类最多追问的对症问题数


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
        if k == "symptom_details":
            old = str(profile.get("symptom_details") or "").strip()
            if val not in old:
                profile["symptom_details"] = "；".join(x for x in (old, val) if x)
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


def _detect_symptom_category(profile: dict, user_input: str) -> str | None:
    """按原始症状（profile.symptom）的关键词匹配出对症类别；仅在原始症状缺失时回退到当前消息。"""
    text = str(profile.get("symptom") or "").strip()
    if not text:
        text = str(user_input or "")
    text = text.lower()
    for cat, keywords, _ in SYMPTOM_QUESTIONS:
        for kw in keywords:
            if kw in text:
                return cat
    return None


def _symptom_questions_for(cat: str) -> list[str]:
    for c, _, qs in SYMPTOM_QUESTIONS:
        if c == cat:
            return qs
    return []


PROFILE_ASK_ORDER = ["age", "allergies", "duration", "severity", "conditions", "medications"]


def _missing_profile(profile: dict, asked: list | None = None) -> list[str]:
    asked = asked or []
    return [k for k in PROFILE_ASK_ORDER if not str(profile.get(k) or "").strip() and k not in asked]


def _needs_ask(mem: dict, state: AgentState, rounds: int) -> bool:
    if state.get("intent") != "symptom":
        return False
    if rounds >= settings.max_question_rounds:
        return False
    if mem.get("consent") is None:
        return True  # 先征得同意
    profile = mem.get("profile", {})
    cat = _detect_symptom_category(profile, state.get("user_input", ""))
    asked = mem.get("asked_symptom_qs", [])
    if mem.get("consent") is False:
        # 无记忆模式：仍基于当前消息做一次对症追问
        if cat and not asked:
            return True
        return False
    if cat:
        qs = _symptom_questions_for(cat)
        if len(asked) < min(SYMPTOM_Q_MAX, len(qs)):
            return True
    if _missing_profile(profile, mem.get("asked_profile", [])):
        return True
    return False


def _next_ask_question(mem: dict, state: AgentState) -> str:
    profile = mem.get("profile", {})
    if mem.get("consent") is None:
        return CONSENT_QUESTION
    cat = _detect_symptom_category(profile, state.get("user_input", ""))
    asked = mem.get("asked_symptom_qs", [])
    if cat:
        qs = _symptom_questions_for(cat)
        remaining = [i for i in range(len(qs)) if i not in asked]
        if remaining and len(asked) < min(SYMPTOM_Q_MAX, len(qs)):
            idx = remaining[0]
            mem["asked_symptom_qs"] = asked + [idx]
            return qs[idx]
    if mem.get("consent") is False:
        return GENERIC_SYMPTOM_QUESTION
    missing = _missing_profile(profile, mem.get("asked_profile", []))
    if missing:
        key = missing[0]
        mem["asked_profile"] = mem.get("asked_profile", []) + [key]
        return ASK_TEMPLATES.get(key, GENERIC_SYMPTOM_QUESTION)
    return GENERIC_SYMPTOM_QUESTION


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
{"age":"年龄","gender":"性别","height":"身高","weight":"体重","allergies":"过敏史","conditions":"慢性病/既往病史","family_history":"家族史","medications":"正在服用的药或保健品","lifestyle":"生活习惯(吸烟/饮酒/作息等)","symptom":"主要症状——必须是用户最初描述症状的完整原文","symptom_details":"追问或补充得到的症状细节（如牙上有黑点、遇冷疼、牙龈肿），可多句","duration":"持续多久","severity":"严重程度","pain_type":"疼痛性质(阵痛/钝痛/刺痛/胀痛等)"}
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
    if any(k in text for k in FORGET_PATTERNS):
        mem["profile"] = {}
        mem["consent"] = None
        mem["rounds"] = 0
        mem["asked_symptom_qs"] = []
        mem["asked_profile"] = []
        mem.pop("last_citations", None)
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
    if mem.get("consent") is None:
        reply = _consent_reply(text)
        if reply is True:
            mem["consent"] = True
        elif reply is False:
            mem["consent"] = False

    new_facts = parsed.get("facts") or {}
    profile = _merge_facts(mem, new_facts)
    if intent == "symptom" and not str(profile.get("symptom") or "").strip():
        first_user = next((t["content"] for t in (transcript or []) if t["role"] == "user"), text)
        profile["symptom"] = str(first_user).strip()[:120]
        mem["profile"] = profile
    mem["intent"] = intent
    return {
        "intent": intent,
        "facts": profile,
        "question_rounds": mem.get("rounds", 0),
    }


async def ask_node(state: AgentState, config: RunnableConfig) -> dict:
    session_id = state.get("session_id", 0)
    mem = get_session_memory(session_id)
    question = _next_ask_question(mem, state)
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
    """把中文转成 1-2 个核心英文医学概念短语，用 / 分隔（用于 PubMed 检索）。"""
    prompt = (
        f"你是医学检索助手。把下面的{kind}描述提炼成 1-2 个核心英文医学概念短语，"
        "用 / 分隔（例如：toothache / dental pain；或 hypertension treatment）。"
        "不要年龄、时长、程度等限定词，不要完整句子，不要标点。\n"
        f"输入：{text}"
    )
    try:
        out = await chat(
            [{"role": "user", "content": prompt}], temperature=0, max_tokens=120
        )
        return out.strip().strip('"').strip()
    except Exception:
        return text


async def _build_pubmed_query(text: str, kind: str) -> str:
    """构造 PubMed 查询：核心概念加引号与字段标签，避免过度 AND 与词义映射跑偏。"""
    concepts = await _to_english(text, kind)
    parts = [p.strip() for p in re.split(r"[/／,，;；]+", concepts) if p.strip()]
    if not parts:
        return text
    clauses = [f'"{p}"[Title/Abstract]' for p in parts[:2]]
    return " OR ".join(clauses)


def _literature_query(state: AgentState) -> str:
    """基于原始症状组装检索描述（不加年龄/时长等限定词，避免过度 AND）。"""
    facts = state.get("facts") or {}
    symptom = str(facts.get("symptom") or "").strip()
    if symptom:
        return symptom[:120]
    return (state.get("user_input") or "")[:120]


async def _rerank_citations(question: str, citations: list[dict]) -> list[dict]:
    """LLM 相关性重排：按主题相关度打分（1-5），保留 ≥3 分的文献（最多 3 条）。"""
    if not citations:
        return []
    items = "\n".join(
        f"{i + 1}. {c.get('title', '')} —— 摘要：{(c.get('snippet') or '')[:300]}"
        for i, c in enumerate(citations)
    )
    prompt = (
        "你是医学文献相关性评审员。判断每篇文献与用户问题的相关性："
        "只要与用户问题属于同一疾病/症状领域（例如都是高血压、都是头痛、都是恶心呕吐），且能提供有用信息，就给 4-5 分；"
        "不要因为缺少『最新/研究进展』等修饰词而降低科普条目的评分。"
        "仅泛泛相关（如同部位但完全不同的问题）给 3 分；无关给 1-2 分。"
        "对每篇文献输出一个 1-5 分的相关度评分。"
        '只输出 JSON：{"scores": [按顺序对应每篇文献的分数]}，不要输出其他内容。\n'
        f"用户问题：{question}\n文献列表：\n{items}"
    )
    try:
        raw = await chat(
            [{"role": "user", "content": prompt}],
            temperature=0,
            json_mode=True,
            max_tokens=200,
        )
        parsed = parse_json(raw)
        scores = parsed.get("scores") or []
        kept = []
        for i, score in enumerate(scores):
            try:
                s = int(score)
            except Exception:
                continue
            if s >= 4 and i < len(citations):
                kept.append(citations[i])
        return kept[:3]
    except Exception:
        return citations[:3]


async def tools_node(state: AgentState, config: RunnableConfig) -> dict:
    intent = state.get("intent", "other")
    intent_eff = _effective_intent(state)
    citations: list[dict] = []
    user_input = state.get("user_input", "")
    facts = state.get("facts") or {}

    # 知识库检索用「原始症状 + 追问细节」作为查询（以档案症状为锚，不受最新一条消息影响）
    kb_query = user_input
    if intent_eff == "symptom":
        kb_query = str(facts.get("symptom") or "") or user_input
        details = str(facts.get("symptom_details") or "").strip()
        if details:
            # 过滤否定句（没有…/无…/不…），避免污染检索语义
            parts = [
                p.strip()
                for p in re.split(r"[；;，,。]", details)
                if p.strip() and not re.match(r"^(没有|无|没有其他|不|未)", p.strip())
            ]
            if parts:
                kb_query = f"{kb_query}；{'；'.join(parts)}"

    if intent == "drug":
        # FDA 为主，知识库为补充（不做重排，避免误删权威药典）
        drugs = await _extract_drugs(user_input)
        if not drugs:
            drugs = [await _to_english(user_input, "药品")]
        fda_cits: list[dict] = []
        for drug in drugs[:2]:
            _push(config, {"type": "tool_call", "tool": "search-drugs", "label": f"正在查询 FDA 药品数据库：{drug}…"})
            result = await mcp_client.call_tool("search-drugs", {"query": drug, "limit": 3})
            fda_cits.extend(extract_citations("search-drugs", result.get("data") or {}))
        _push(config, {"type": "tool_call", "tool": "knowledge-base", "label": "正在检索本地健康知识库…"})
        citations = fda_cits + kb_citations(kb_query)
    else:
        # 混合检索：本地知识库（向量语义，信任向量相关性，直接取前 2 条）+ PubMed（关键词，LLM 严格重排）
        _push(config, {"type": "tool_call", "tool": "knowledge-base", "label": "正在检索本地健康知识库…"})
        kb_cits = kb_citations(kb_query)[:2]
        if intent == "literature":
            query_text, kind = user_input, "研究主题"
        elif intent_eff == "symptom":
            query_text, kind = kb_query, "症状"
        else:
            query_text, kind = user_input, "健康问题"
        pubmed_q = await _build_pubmed_query(query_text, kind)
        _push(config, {"type": "tool_call", "tool": "search-medical-literature", "label": "正在检索 PubMed 医学文献…"})
        result = await mcp_client.call_tool("search-medical-literature", {"query": pubmed_q, "max_results": 5})
        pubmed_cits = extract_citations("search-medical-literature", result.get("data") or {})
        rerank_q = kb_query if intent_eff == "symptom" else user_input
        pubmed_kept = await _rerank_citations(rerank_q, pubmed_cits) if pubmed_cits else []
        citations = kb_cits + pubmed_kept

    return {"citations": citations[:3]}

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
        m = re.search(r"(🟢|🟡|🔴)", full)
        if m:
            triage = {"🟢": "green", "🟡": "yellow", "🔴": "red"}[m.group(1)]
        elif "可自行观察" in full:
            triage = "green"
        elif "尽快就医" in full or "建议就医" in full or "及时就医" in full:
            triage = "yellow"
        elif "立即就医" in full or "急诊" in full or "立即急诊" in full:
            triage = "red"

    return {"output": full, "triage_level": triage, "reason": "answer", "done": True}


def _effective_intent(state: AgentState) -> str:
    """只要档案里有原始症状，且当前意图不是明确的药品/文献类，就按症状问诊处理，
    避免"持续一个多月了"这类追问回复被误判为其他意图后丢失症状锚点。"""
    intent = state.get("intent", "other")
    facts = state.get("facts") or {}
    if intent in ("symptom", "other") and str(facts.get("symptom") or "").strip():
        return "symptom"
    return intent


def _route_after_safety(state: AgentState) -> str:
    return "end" if state.get("done") else "understand"


def _route_after_understand(state: AgentState) -> str:
    if _effective_intent(state) != "symptom":
        return "tools"
    rounds = state.get("question_rounds", 0)
    mem = get_session_memory(state.get("session_id", 0))
    if _needs_ask(mem, state, rounds):
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













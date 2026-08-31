"""安全护栏：紧急信号检测 + 拒绝越界请求（不开药、不诊断）。"""
from __future__ import annotations

# 紧急情况关键词（命中即引导立即就医）
EMERGENCY_PATTERNS = [
    "胸痛", "胸口剧痛", "胸闷压榨", "呼吸困难", "喘不上气", "不能呼吸", "窒息",
    "昏迷", "意识不清", "叫不醒", "大出血", "流血不止", "止不住血", "剧烈头痛",
    "突然头痛", "中风", "偏瘫", "口角歪斜", "说不出话", "半边身体麻木",
    "自杀", "想死", "轻生", "割腕", "跳楼", "过量服药",
    "过敏性休克", "全身红肿", "呼吸困难伴皮疹", "抽搐不止", "癫痫发作",
    "高烧惊厥", "意识丧失", "心脏骤停", "心跳停止",
    "chest pain", "shortness of breath", "difficulty breathing", "cannot breathe",
    "unconscious", "severe bleeding", "stroke", "suicidal", "suicide",
    "seizure", "anaphylaxis", "heart attack", "cardiac arrest",
]

# 越界请求（开药 / 处方）
PRESCRIPTION_PATTERNS = [
    "开药", "开个药", "开处方", "给我开", "开点药", "处方药", "帮我开",
    "prescription", "prescribe", "给我开处方",
]

# 越界请求（要求确诊）
DIAGNOSIS_PATTERNS = [
    "确诊", "诊断一下", "我得了什么病", "告诉我什么病", "我是不是得了",
    "diagnose", "diagnosis",
]

EMERGENCY_RESPONSE = (
    "⚠️ 您描述的情况可能属于紧急医疗状况。我无法通过文字评估您的病情，"
    "请您立即停止当前活动，拨打 120 急救电话或前往最近的急诊室，"
    "必要时请身边人陪同。请不要依赖网络信息自行处理。"
)

REFUSAL_RESPONSE = (
    "我理解您的需求，但作为健康信息咨询助手，我不能开具处方或替代医生用药决策。"
    "用药需要医生结合您的具体情况评估，请前往医院或在线问诊平台咨询执业医生。"
    "如果您只是想了解某类药物的通用信息（如适应症、常见副作用），我可以帮您检索权威资料。"
)

DIAGNOSIS_RESPONSE = (
    "我无法代替医生作出诊断。医学诊断需要结合面诊、检查与病史，"
    "建议您到正规医疗机构就诊。我可以帮您检索该症状的科普资料、"
    "常见原因以及就医建议，供您参考并与医生沟通。"
)


def check_emergency(text: str) -> str | None:
    for kw in EMERGENCY_PATTERNS:
        if kw.lower() in text.lower():
            return kw
    return None


def check_prescription(text: str) -> str | None:
    for kw in PRESCRIPTION_PATTERNS:
        if kw.lower() in text.lower():
            return kw
    return None


def check_diagnosis(text: str) -> str | None:
    for kw in DIAGNOSIS_PATTERNS:
        if kw.lower() in text.lower():
            return kw
    return None

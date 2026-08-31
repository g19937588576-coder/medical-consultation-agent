---
name: medical-consultation-agent
description: 构建或迭代「医疗问诊 AI Agent」网页应用时的操作手册：分诊流程、医学资料检索、安全护栏与引用规则。适用于维护本项目（症状分诊、药品查询、文献检索），也适用于复用同样的人机边界与检索链路。不是医疗建议来源，不用于回答医学问题本身。
metadata:
  short-description: 医疗问诊 Agent 的分诊流程、检索工具与安全护栏手册
---

# 医疗问诊 Agent 操作手册

本技能服务于项目 `C:\Users\28777\Documents\ChatGPT\医疗问诊ai`（后端 FastAPI + LangGraph，前端 React；医学资料来自本地 `medical-mcp` 进程）。

## 核心定位与红线
- 产品定位：**健康信息咨询助手**，不是医生。永远不提供明确诊断、不开处方、不给具体用药方案。
- 三道安全护栏（先于一切执行，命中即停止并返回固定话术）：
  1. 紧急信号（胸痛、呼吸困难、昏迷、大出血、自杀倾向等）→ 立即引导拨打 120/急诊；
  2. 开药/处方请求 → 拒绝并建议就诊；
  3. 要求确诊 → 拒绝并建议就诊（可提供科普与就医建议）。
- 所有回答结尾固定附带「仅供健康信息参考，不能替代医生面诊」。

## 问诊工作流（LangGraph 状态机）
1. `safety` 安全检测 + 命令处理：先检查「忘记我的信息/清除档案」（清空会话档案）与「查看我的信息」（列出已记录档案），再检查紧急/开药/确诊拦截。
2. `understand` 意图识别：`symptom`（症状）/ `drug`（药品）/ `literature`（文献）/ `other`，并抽取健康档案字段（年龄、性别、身高、体重、过敏史、病史、家族史、用药、生活习惯、主要症状、持续时间、严重程度、疼痛性质）。合并规则：稳定属性先到先得，动态属性（持续时间/严重程度）后到覆盖。
3. `ask` 对症追问：仅症状类。先征得「同意记录健康信息」；然后按「对症问题库」（牙疼→黑点/牙龈/冷热刺激，头疼→偏侧/恶心/搏动性，腹痛→位置/进食/腹泻，恶心呕吐→与进食关系/干呕/伴随症状，腰背→弯腰加重/腿麻，每类最多 3 问）追问，再补 年龄→过敏史→持续时间→严重程度→病史/用药，最多 12 轮。拒绝同意则进入无记忆模式（仍做一次对症追问，但不累积档案）。
4. `tools` 检索与内部参考：① 本地中文健康知识库（`knowledge_base.py`，fastembed bge-small-zh 向量 + SQLite 余弦 + 关键词加权）取 top-2 **仅作内部参考**（辅助建议性内容，不展示、不可引用、不出现编号）；② 药品→`search-drugs`（FDA 为主）；症状/文献/其他→`search-medical-literature`。**检索词始终以档案原始症状为锚**（最新一条回复可能被误判意图）；症状类合并「类别相似症状术语表」+「LLM 翻译」两路检索，经 LLM 相关性重排（≥3 分主题相关，最多 3 条）。**参考资料只展示论文**，知识库科普不展示；知识库不可用时自动降级。
5. `synthesize` 生成回答：中文、先结论后解释、引用编号标注、必须围绕原始症状作答（资料不相关时明确说明并给保守建议，禁止输出与症状无关的部位建议）、症状类结尾输出分诊等级 `🟢可自行观察 / 🟡建议尽快就医 / 🔴需立即急诊`。

## 检索与引用规则
- 工具名用**连字符**：`search-drugs`、`search-medical-literature`、`get-health-statistics`、`get-article-details`。
- 工具返回的是 Markdown 文本（`{"raw": "..."}`），必须用 `mcp_tools.extract_citations` 解析出 `{title, url, source, snippet}`。
- **引用只允许指向本次检索真实返回的条目**；资料为空时禁止输出 `[n]` 引用，应如实说明资料不足。
- 工具结果走 SQLite 缓存（同参数 1 小时），重复问题不重复查库。

## 环境与运行
- 后端：`cd backend && uv sync && uv run uvicorn app.main:app --port 8000`；MCP 由后端以子进程启动（`node vendor/medical-mcp/node_modules/medical-mcp/build/index.js`）。
- 前端：`cd frontend && npm install && npm run dev`（开发），`npm run build` 后产物并入 `backend/static`（单服务部署）。
- 注意：`medical-mcp` 有若干 `console.log` 会污染 MCP stdio 协议，已统一改为 `console.error`；升级该依赖后需重新打补丁。
- 评测：`GET /api/eval` 运行 15 题（症状/药品/文献/紧急/对抗）并输出评分面板。






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
1. `safety` 安全检测（见上）。
2. `understand` 意图识别：`symptom`（症状）/ `drug`（药品）/ `literature`（文献）/ `other`，并抽取问诊事实（年龄、持续时间、严重程度、过敏史、在服药物、主要症状）。
3. `ask` 多轮追问：仅症状类，缺「持续时间/严重程度/年龄」时按序追问，最多 5 轮；信息足够即停止。
4. `tools` 检索：药品→`search-drugs`；症状/文献/其他→`search-medical-literature`。**中文必须先经 LLM 翻译成英文关键词再检索**（FDA/PubMed 只认英文）。
5. `synthesize` 生成回答：中文、先结论后解释、引用编号标注、症状类结尾输出分诊等级 `🟢可自行观察 / 🟡建议尽快就医 / 🔴需立即急诊`。

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

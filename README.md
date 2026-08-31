# 🩺 医疗问诊 AI Agent（作品集 Demo）

一个基于 **LangGraph + FastAPI + React + medical-mcp** 的医疗健康咨询 AI Agent：
用户用自然语言提问，AI 像医生一样多轮追问后给出**分级分诊建议（🟢/🟡/🔴）**，支持药品查询与论文检索，回答**带可点击引用**，并内置安全护栏（紧急情况拦截、拒绝开药/诊断）。

> ⚠️ 本项目定位为**健康信息咨询助手**，不提供诊断、不开处方，仅用于学习与作品展示，不能替代医生面诊。

## 功能总览

| 功能 | 说明 |
|---|---|
| 💬 对症问诊 | 像医生一样对症追问（牙疼问黑点/牙龈/冷热刺激，头疼问偏侧/恶心等），再补年龄/过敏史/时长/严重程度，最多 12 轮 |
| 📚 文献引用 | 检索词提炼为核心医学概念 + 相关性过滤，只引用与问题直接相关的论文（最多 3 条） |
| 🧾 健康档案（同意制） | 先征求同意才记录本次对话的健康特征；可随时说「查看我的信息」查看、「忘记我的信息」清除 |
| 🩺 分级分诊 | 症状回答结尾输出 🟢可自行观察 / 🟡建议尽快就医 / 🔴需立即急诊 |
| 💊 药品查询 | 经 FDA 数据库查询药品信息（中文药品名自动转英文检索） |
| 📚 文献检索 | 检索 PubMed 3000 万+ 医学论文，回答带引用链接与摘要 |
| 🛡️ 安全护栏 | 紧急信号→引导拨打 120；开药/处方/确诊请求→拒绝并建议就诊 |
| 📄 导出 PDF | 一键把问诊记录导出成一页式 PDF（带引用与免责声明） |
| 📊 评测面板 | 内置 15 道题（症状/药品/文献/紧急/对抗），一键评分（通过率/引用覆盖率/相关性） |
| 🗂️ 会话历史 | 左侧历史会话列表，可回看 |

## 技术架构

```
用户 ──▶ React 前端 (Vite + Tailwind)
            │  SSE 流式 (token / tool_call / result)
            ▼
        FastAPI 后端 ── LangGraph Agent 状态机
            │            ├─ safety 安全护栏
            │            ├─ understand 意图识别 + 问诊事实抽取
            │            ├─ ask 多轮追问（症状类）
            │            ├─ tools 医学资料检索
            │            └─ synthesize 生成带引用/分级的回答
            │
            ├─ medical-mcp（stdio 子进程）──▶ FDA / WHO / PubMed / RxNorm
            ├─ SQLite（会话 / 消息 / 工具缓存）
            └─ DeepSeek API（OpenAI 兼容，流式）
```

## 目录结构

```
├── backend/            FastAPI + LangGraph 后端（uv 管理）
│   ├── app/
│   │   ├── main.py          API 入口（SSE 问诊 / 会话 / PDF / 评测）
│   │   ├── agent.py         LangGraph 状态机（问诊核心）
│   │   ├── mcp_tools.py     medical-mcp 客户端 + 引用解析
│   │   ├── safety.py        紧急信号 / 越界请求检测
│   │   ├── eval_suite.py    15 题评测套件
│   │   ├── pdf_export.py    PDF 导出（reportlab 中文）
│   │   ├── db.py            SQLite 数据层
│   │   └── llm.py           DeepSeek 调用封装
│   └── static/              前端构建产物（单服务部署）
├── frontend/           React + Vite + Tailwind 前端
├── skills/medical-consultation-agent/   医疗问诊技能手册（已安装到全局技能库）
├── docs/screenshots/   演示截图
├── Dockerfile          Render 部署用（前后端单容器）
└── render.yaml         Render 蓝图
```

## 本地运行

前置要求：Node.js ≥ 18、npm、uv（Python 3.11+ 由 uv 自动管理）。

```bash
# 1. 后端
cd backend
cp .env.example .env      # 填入 LLM_API_KEY（DeepSeek 密钥）
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2. 前端（另开终端）
cd frontend
npm install
npm run dev               # http://localhost:5173
```

打开 http://localhost:5173 即可使用。也可以直接访问 http://127.0.0.1:8000（后端同时托管前端）。

### medical-mcp 说明
- 医学资料查询工具（FDA / WHO / PubMed / RxNorm），免费、无需 API 密钥，本地运行。
- 依赖会随 `npm install` 装到 `vendor/medical-mcp/`；如缺失，执行：
  ```bash
  mkdir -p vendor/medical-mcp && cd vendor/medical-mcp
  npm init -y && npm install medical-mcp@1.0.8
  ```
- 已打补丁：把该包所有 `console.log` 改为 `console.error`（MCP stdio 协议只允许 JSON 走 stdout）。升级依赖后需重新打补丁。

## 部署到 Render（公网访问）

1. 把项目推送到 GitHub 仓库。
2. 在 [render.com](https://render.com) 新建 **Web Service**，选择该仓库，Runtime 选 **Docker**（或直接使用 `render.yaml` 蓝图）。
3. 环境变量：`LLM_API_KEY` 填 DeepSeek 密钥（其余见 `render.yaml` 默认值）。
4. 部署完成后打开 `https://<服务名>.onrender.com` 即可。

> 免费套餐注意：闲置 15 分钟会休眠（首次访问需等几十秒）；服务重启后对话记录会清空（`/tmp` 存储），属预期行为。

## 评测结果（内置评测面板）

`GET /api/eval` 运行 15 道题并评分；本仓库开发环境实测：**通过率 15/15，平均相关性 4.0/5，引用覆盖率 60%**（紧急/对抗类问题不产生引用，属设计行为）。

## 免责声明
本项目仅供学习与技术展示。AI 回答基于公开医学资料检索生成，可能存在错误或时效性问题，不能替代执业医师的面诊、诊断与治疗。如有身体不适请及时就医。



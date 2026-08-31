# ===== 阶段 1：构建前端 =====
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ===== 阶段 2：后端 + 运行 =====
FROM node:22-slim
# 安装 Python 与 uv（后端依赖管理）
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir uv

WORKDIR /app
# 后端依赖
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# 医学资料 MCP（npm 包，含 build/index.js）
ENV PUPPETEER_SKIP_DOWNLOAD=true
RUN npm install -g medical-mcp@1.0.8 --no-audit --no-fund

# 后端代码 + 前端构建产物
COPY backend/app ./app
COPY --from=frontend /app/frontend/dist ./static

# 打补丁：medical-mcp 的 console.log 会污染 MCP stdio 协议，全部改到 stderr
RUN node -e "const fs=require('fs');const p=require('child_process').execSync('npm root -g').toString().trim()+'/medical-mcp/build';for(const f of fs.readdirSync(p)){if(!f.endsWith('.js'))continue;const fp=p+'/'+f;let s=fs.readFileSync(fp,'utf8');s=s.replace(/console\\.log\\(/g,'console.error(');s=s.replace(/retmax: maxResults,/g,'retmax: maxResults, sort: "relevance",');fs.writeFileSync(fp,s);}"

ENV PORT=8000
ENV MCP_SERVER_COMMAND=medical-mcp
ENV MCP_SERVER_ARGS=[]
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]




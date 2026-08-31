"""应用配置：从环境变量 / .env 读取。"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # 大模型（OpenAI 兼容）
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # 医学资料 MCP 服务器（stdio）
    mcp_server_command: str = "node"
    mcp_server_args: str = '["../vendor/medical-mcp/build/index.js"]'

    # 数据与缓存
    db_path: str = "data/medical.db"
    tool_cache_ttl: int = 3600  # 秒

    # 问诊行为
    max_question_rounds: int = 5

    # 前端来源（CORS）
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = {"env_file": str(BASE_DIR / ".env"), "env_file_encoding": "utf-8"}

    @property
    def mcp_server_args_list(self) -> list[str]:
        try:
            raw = json.loads(self.mcp_server_args)
            return [str(x) for x in raw] if isinstance(raw, list) else []
        except Exception:
            return []

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

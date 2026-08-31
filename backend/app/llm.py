"""大模型调用封装：OpenAI 兼容接口，支持流式输出。"""
from __future__ import annotations

import json
import re

import httpx

from .config import settings


def _endpoint() -> str:
    return settings.llm_base_url.rstrip("/") + "/chat/completions"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.llm_api_key}"}


async def chat(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    json_mode: bool = False,
) -> str:
    payload: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(_endpoint(), json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


async def chat_stream(
    messages: list[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 2400,
):
    """异步生成器：逐个产出文本 token。"""
    payload: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", _endpoint(), json=payload, headers=_headers()
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    token = delta.get("content")
                    if token:
                        yield token
                except Exception:
                    continue


def parse_json(text: str) -> dict:
    """从模型输出中稳健地解析 JSON 对象。"""
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {}

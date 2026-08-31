"""medical-mcp 客户端：stdio 启动、工具调用、结果缓存。"""
from __future__ import annotations

import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import settings
from .db import get_cached_tool, set_cached_tool

ALLOWED_TOOLS = {
    "search-drugs",
    "get-health-statistics",
    "search-medical-literature",
    "get-article-details",
}


class MedicalMCPClient:
    def __init__(self) -> None:
        self._ctx = None
        self._session: ClientSession | None = None
        self._tool_names: set[str] = set()
        self.available = False
        self.error: str | None = None

    async def start(self) -> "MedicalMCPClient":
        params = StdioServerParameters(
            command=settings.mcp_server_command,
            args=settings.mcp_server_args_list,
        )
        self._ctx = stdio_client(params)
        try:
            read, write = await self._ctx.__aenter__()
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
            tools = await self._session.list_tools()
            self._tool_names = {t.name for t in tools.tools}
            self.available = True
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.available = False
        return self

    async def stop(self) -> None:
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx = None

    def has_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def call_tool(self, name: str, args: dict) -> dict:
        if not self.available or self._session is None:
            return {"ok": False, "tool": name, "error": self.error or "MCP 未就绪", "data": None}
        if name not in self._tool_names:
            return {"ok": False, "tool": name, "error": f"工具 {name} 不可用", "data": None}
        cached = get_cached_tool(name, args)
        if cached is not None:
            return cached
        result = await self._session.call_tool(name, arguments=args)
        payload = self._extract(result)
        out = {"ok": True, "tool": name, "data": payload}
        set_cached_tool(name, args, out)
        return out

    @staticmethod
    def _extract(result) -> dict:
        structured = getattr(result, "structuredContent", None)
        if structured:
            return structured
        texts: list[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        raw = "\n".join(texts)
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}


mcp_client = MedicalMCPClient()


def _split_items(raw: str):
    """按 '1. **Title**' 结构切分条目。"""
    import re
    parts = re.split(r"\n(\d+)\.\s+\*\*(.+?)\*\*\s*\n", "\n" + (raw or ""))
    items = []
    i = 1
    while i < len(parts) - 1:
        num, title, body = parts[i], parts[i + 1], parts[i + 2]
        items.append({"num": num, "title": title.strip(), "body": body})
        i += 3
    return items


def _field(body: str, name: str) -> str:
    import re
    m = re.search(rf"^\s*{name}:\s*(.+)$", body, re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_literature(raw: str) -> list[dict]:
    """解析 PubMed 检索结果 markdown。"""
    import html
    out = []
    for it in _split_items(raw):
        pmid = _field(it["body"], "PMID")
        url = _field(it["body"], "URL")
        abstract = _field(it["body"], "Abstract")
        out.append(
            {
                "title": html.unescape(it["title"]),
                "pmid": pmid,
                "url": url or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""),
                "journal": html.unescape(_field(it["body"], "Journal")),
                "date": _field(it["body"], "Publication Date"),
                "abstract": html.unescape(abstract),
            }
        )
    return out


def parse_drugs(raw: str) -> list[dict]:
    """解析 FDA 药品检索结果 markdown。"""
    import html
    out = []
    for it in _split_items(raw):
        out.append(
            {
                "brand": html.unescape(it["title"]),
                "generic": html.unescape(_field(it["body"], "Generic Name")),
                "route": _field(it["body"], "Route"),
                "manufacturer": html.unescape(_field(it["body"], "Manufacturer")),
                "purpose": html.unescape(_field(it["body"], "Purpose")),
                "dosage_form": _field(it["body"], "Dosage Form"),
            }
        )
    return out


def extract_citations(tool_name: str, payload: dict, limit: int = 5) -> list[dict]:
    """从工具返回中提取可展示的引用条目。"""
    raw = (payload or {}).get("raw", "") if isinstance(payload, dict) else ""
    citations: list[dict] = []
    if tool_name == "search-medical-literature":
        for item in parse_literature(raw)[:limit]:
            citations.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "source": "PubMed",
                    "snippet": item["abstract"][:600],
                }
            )
    elif tool_name == "search-drugs":
        for item in parse_drugs(raw)[:limit]:
            name = item["generic"] or item["brand"]
            purpose = item["purpose"] or ""
            snippet = f"通用名：{item['generic']}；剂型：{item['route']}；生产商：{item['manufacturer']}；用途：{purpose}"
            citations.append(
                {
                    "title": f"FDA 药品信息：{name}",
                    "url": "https://open.fda.gov/drugs/",
                    "source": "FDA (openFDA)",
                    "snippet": snippet[:600],
                }
            )
    return citations

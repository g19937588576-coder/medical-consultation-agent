"""FastAPI 入口：会话管理、SSE 流式问诊、PDF 导出、评测面板。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import db, eval_suite, pdf_export
from .agent import (
    agent_graph,
    append_transcript,
    get_session_memory,
    reset_session_memory,
)
from .config import settings
from .mcp_tools import mcp_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await mcp_client.start()
    yield
    await mcp_client.stop()


app = FastAPI(title="医疗问诊 AI Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NewSessionReq(BaseModel):
    title: str | None = None


class ChatReq(BaseModel):
    session_id: int
    message: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "mcp_available": mcp_client.available,
        "mcp_tools": sorted(mcp_client._tool_names) if mcp_client.available else [],
        "mcp_error": mcp_client.error,
        "model": settings.llm_model,
    }


@app.post("/api/sessions")
async def new_session(req: NewSessionReq | None = None):
    rec = db.create_session(title=(req.title if req else None) or "新问诊")
    reset_session_memory(rec.id)
    return {"id": rec.id, "title": rec.title, "created_at": rec.created_at.isoformat()}


@app.get("/api/sessions")
async def list_sessions():
    out = []
    for s in db.list_sessions():
        msgs = db.get_messages(s.id)
        last = msgs[-1] if msgs else None
        out.append(
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat(),
                "message_count": len(msgs),
                "last_preview": (last.content[:60] if last else ""),
            }
        )
    return out


@app.get("/api/sessions/{sid}/messages")
async def get_messages(sid: int):
    if not db.get_session(sid):
        raise HTTPException(404, "会话不存在")
    out = []
    for m in db.get_messages(sid):
        out.append(
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citations": json.loads(m.citations_json or "[]"),
                "triage_level": m.triage_level,
                "created_at": m.created_at.isoformat(),
            }
        )
    return out


@app.post("/api/chat")
async def chat(req: ChatReq):
    if not req.message or not req.message.strip():
        raise HTTPException(400, "消息不能为空")
    if not db.get_session(req.session_id):
        raise HTTPException(404, "会话不存在")

    db.add_message(req.session_id, "user", req.message)
    append_transcript(req.session_id, "user", req.message)
    mem = get_session_memory(req.session_id)
    first_user = next(
        (t["content"] for t in mem.get("transcript", []) if t["role"] == "user"), None
    )
    if first_user and db.get_session(req.session_id).title == "新问诊":
        db.update_session_title(req.session_id, first_user)

    queue: asyncio.Queue = asyncio.Queue()
    state = {
        "session_id": req.session_id,
        "user_input": req.message,
        "transcript": mem.get("transcript", []),
        "facts": mem.get("profile", {}),
        "question_rounds": mem.get("rounds", 0),
    }
    config = {"configurable": {"event_queue": queue}}

    async def run_agent():
        return await agent_graph.ainvoke(state, config)

    async def gen():
        task = asyncio.create_task(run_agent())
        yield _sse("start", {})
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            etype = ev.get("type")
            if etype == "token":
                yield _sse("token", {"text": ev.get("text", "")})
            elif etype == "tool_call":
                yield _sse("tool_call", {"tool": ev.get("tool"), "label": ev.get("label")})
            elif etype == "guardrail":
                yield _sse("guardrail", {"kind": ev.get("kind")})
            elif etype == "error":
                yield _sse("error", {"detail": ev.get("detail")})
        result = await task
        output = result.get("output") or ""
        reason = result.get("reason")
        citations = result.get("citations") or []
        triage = result.get("triage_level")
        if reason == "question":
            db.add_message(req.session_id, "assistant", output)
            append_transcript(req.session_id, "assistant", output)
            yield _sse("question", {"text": output})
        else:
            db.add_message(req.session_id, "assistant", output, citations=citations, triage_level=triage)
            append_transcript(req.session_id, "assistant", output)
            yield _sse(
                "result",
                {
                    "text": output,
                    "citations": citations,
                    "triage_level": triage,
                    "reason": reason,
                },
            )
        yield _sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sessions/{sid}/export")
async def export_pdf(sid: int):
    session = db.get_session(sid)
    if not session:
        raise HTTPException(404, "会话不存在")
    msgs = db.get_messages(sid)
    if not msgs:
        raise HTTPException(400, "会话还没有内容")
    pdf = pdf_export.build_pdf(session, msgs)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="medical-consultation-{sid}.pdf"'
        },
    )


@app.get("/api/eval")
async def run_eval():
    result = await eval_suite.run_eval()
    return result


# 静态前端（构建后挂载，需在 API 路由之后）
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


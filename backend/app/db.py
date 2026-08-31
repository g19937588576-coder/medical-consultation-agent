"""SQLite 数据层：会话、消息、工具结果缓存。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Field, Session as DBSession, SQLModel, create_engine, select

from .config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"
    id: int | None = Field(default=None, primary_key=True)
    title: str = "新问诊"
    created_at: datetime = Field(default_factory=_utcnow)


class MessageRecord(SQLModel, table=True):
    __tablename__ = "messages"
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    role: str = "assistant"  # user / assistant
    content: str = ""
    citations_json: str = "[]"
    triage_level: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ToolCacheRecord(SQLModel, table=True):
    __tablename__ = "tool_cache"
    id: int | None = Field(default=None, primary_key=True)
    tool_name: str = ""
    query_hash: str = Field(index=True)
    result_json: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


_db_path = Path(settings.db_path)
if not _db_path.is_absolute():
    _db_path = Path(__file__).resolve().parent.parent / _db_path
_db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{_db_path.as_posix()}",
    connect_args={"check_same_thread": False},
)
SQLModel.metadata.create_all(engine)


def create_session(title: str = "新问诊") -> SessionRecord:
    with DBSession(engine) as db:
        rec = SessionRecord(title=title or "新问诊")
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec


def list_sessions() -> list[SessionRecord]:
    with DBSession(engine) as db:
        return list(
            db.exec(
                select(SessionRecord).order_by(SessionRecord.created_at.desc())
            ).all()
        )


def get_session(session_id: int) -> SessionRecord | None:
    with DBSession(engine) as db:
        return db.get(SessionRecord, session_id)


def add_message(
    session_id: int,
    role: str,
    content: str,
    citations: list | None = None,
    triage_level: str | None = None,
) -> MessageRecord:
    with DBSession(engine) as db:
        rec = MessageRecord(
            session_id=session_id,
            role=role,
            content=content,
            citations_json=json.dumps(citations or [], ensure_ascii=False),
            triage_level=triage_level,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec


def get_messages(session_id: int) -> list[MessageRecord]:
    with DBSession(engine) as db:
        return list(
            db.exec(
                select(MessageRecord)
                .where(MessageRecord.session_id == session_id)
                .order_by(MessageRecord.created_at.asc())
            ).all()
        )


def update_session_title(session_id: int, title: str) -> None:
    with DBSession(engine) as db:
        rec = db.get(SessionRecord, session_id)
        if rec and len(title) > 40:
            rec.title = title[:40]
        elif rec:
            rec.title = title
        db.add(rec)
        db.commit()


def hash_args(tool_name: str, args: dict) -> str:
    raw = json.dumps({"t": tool_name, "a": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_tool(tool_name: str, args: dict) -> dict | None:
    key = hash_args(tool_name, args)
    with DBSession(engine) as db:
        row = db.exec(
            select(ToolCacheRecord).where(ToolCacheRecord.query_hash == key)
        ).first()
        if not row:
            return None
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age > settings.tool_cache_ttl:
            db.delete(row)
            db.commit()
            return None
        try:
            return json.loads(row.result_json)
        except Exception:
            return None


def set_cached_tool(tool_name: str, args: dict, result: dict) -> None:
    key = hash_args(tool_name, args)
    with DBSession(engine) as db:
        existing = db.exec(
            select(ToolCacheRecord).where(ToolCacheRecord.query_hash == key)
        ).first()
        if existing:
            existing.result_json = json.dumps(result, ensure_ascii=False)
            existing.created_at = _utcnow()
            db.add(existing)
        else:
            db.add(
                ToolCacheRecord(
                    tool_name=tool_name,
                    query_hash=key,
                    result_json=json.dumps(result, ensure_ascii=False),
                )
            )
        db.commit()


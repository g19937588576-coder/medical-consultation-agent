"""本地中文健康知识库：向量嵌入 + 余弦相似度检索（轻量，向量存 SQLite）。

模型不可用时自动降级（返回空结果），不影响 PubMed 检索主链路。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np
from sqlmodel import Session as DBSession, select

from .config import settings
from .db import KbMetaRecord, KbVectorRecord, engine

_embedder = None
_embedder_lock = threading.Lock()
_index_built = False


def _data_path() -> Path:
    p = Path(settings.kb_data_file)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def load_entries() -> list[dict]:
    p = _data_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _data_hash() -> str:
    p = _data_path()
    if not p.exists():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def get_embedder():
    """懒加载 fastembed 模型；默认 HF 失败则切镜像重试；仍失败返回 None（降级）。"""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder
        try:
            from fastembed import TextEmbedding
        except Exception:
            return None
        try:
            _embedder = TextEmbedding(model_name=settings.kb_model)
        except Exception:
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
            try:
                _embedder = TextEmbedding(model_name=settings.kb_model)
            except Exception:
                _embedder = None
    return _embedder


def _entry_text(entry: dict) -> str:
    parts = [str(entry.get("title") or "")]
    kws = entry.get("keywords") or []
    if kws:
        parts.append(" ".join(str(k) for k in kws))
    parts.append(str(entry.get("content") or ""))
    return " ".join(parts)


def ensure_index() -> None:
    """确保向量索引已构建；知识库数据变更时自动重建。"""
    global _index_built
    if _index_built:
        return
    entries = load_entries()
    digest = _data_hash()
    embedder = get_embedder()
    with DBSession(engine) as db:
        meta = db.exec(select(KbMetaRecord).where(KbMetaRecord.key == "kb_hash")).first()
        if meta and meta.value == digest:
            _index_built = True
            return
        if not entries or embedder is None:
            _index_built = True
            return
        texts = [_entry_text(e) for e in entries]
        vectors = list(embedder.embed(texts))
        ids = set()
        for e, v in zip(entries, vectors):
            vec = np.asarray(v, dtype="float32")
            vec = vec / (float(np.linalg.norm(vec)) + 1e-9)
            eid = str(e.get("id") or "")
            ids.add(eid)
            existing = db.get(KbVectorRecord, eid)
            payload = json.dumps(vec.tolist())
            if existing:
                existing.vector_json = payload
                db.add(existing)
            else:
                db.add(KbVectorRecord(entry_id=eid, vector_json=payload))
        for rec in db.exec(select(KbVectorRecord)).all():
            if rec.entry_id not in ids:
                db.delete(rec)
        if meta:
            meta.value = digest
            db.add(meta)
        else:
            db.add(KbMetaRecord(key="kb_hash", value=digest))
        db.commit()
    _index_built = True


def search_kb(query: str, top_k: int | None = None) -> list[dict]:
    """向量语义检索：返回与 query 语义最接近的知识库条目（含 score）。"""
    if not settings.kb_enabled:
        return []
    try:
        embedder = get_embedder()
        if embedder is None:
            return []
        ensure_index()
        entries_by_id = {str(e.get("id") or ""): e for e in load_entries()}
        if not entries_by_id:
            return []
        qv = np.asarray(list(embedder.embed([query]))[0], dtype="float32")
        qv = qv / (float(np.linalg.norm(qv)) + 1e-9)
        with DBSession(engine) as db:
            rows = db.exec(select(KbVectorRecord)).all()
        scored: list[tuple[float, dict]] = []
        ql = query.lower()
        for rec in rows:
            entry = entries_by_id.get(rec.entry_id)
            if entry is None:
                continue
            try:
                vec = np.asarray(json.loads(rec.vector_json), dtype="float32")
            except Exception:
                continue
            score = float(np.dot(qv, vec))
            # 关键词加权：语义相近时，关键词有重合的条目优先
            if any(str(k).lower() in ql for k in (entry.get("keywords") or [])):
                score += 0.2
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        k = top_k or settings.kb_top_k
        return [e for _, e in scored[:k]]
    except Exception:
        return []


def kb_citations(query: str, top_k: int | None = None) -> list[dict]:
    """把知识库检索结果转成引用条目（source 标注为科普）。"""
    out: list[dict] = []
    for e in search_kb(query, top_k):
        content = str(e.get("content") or "").strip()
        advice = str(e.get("advice") or "").strip()
        warning = str(e.get("warning") or "").strip()
        snippet = content
        if advice:
            snippet += "\n建议：" + advice
        if warning:
            snippet += "\n注意：" + warning
        out.append(
            {
                "title": str(e.get("title") or "健康知识科普"),
                "url": "#",
                "source": "健康知识库（科普）",
                "snippet": snippet[:700],
                "kb": True,
            }
        )
    return out


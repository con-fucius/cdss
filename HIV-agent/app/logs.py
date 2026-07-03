"""Structured logging infrastructure for CDSS.
Implements Phase 0.2 requirements using structlog.
"""

import atexit
import contextlib
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

try:
    from .config import get_audit_storage_backend, get_patient_salt
except ImportError:  # Deprecated top-level Streamlit imports.
    from config import get_audit_storage_backend, get_patient_salt

APP_DIR = Path(__file__).resolve().parent

# Setup SQLite DB for Audit logs
AUDIT_DB_PATH = Path(os.getenv("CDSS_AUDIT_DB_PATH", APP_DIR / "data" / "audit.db"))
_AUDIT_DB_RAW = os.getenv("CDSS_AUDIT_DB_PATH", str(APP_DIR / "data" / "audit.db"))
_MEMORY_AUDIT_CONN: sqlite3.Connection | None = None

if _AUDIT_DB_RAW != ":memory:":
    AUDIT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _close_memory_audit_db() -> None:
    if _MEMORY_AUDIT_CONN is not None:
        _MEMORY_AUDIT_CONN.close()


atexit.register(_close_memory_audit_db)


def _connect_audit_db() -> sqlite3.Connection:
    global _MEMORY_AUDIT_CONN
    if _AUDIT_DB_RAW == ":memory:":
        if _MEMORY_AUDIT_CONN is None:
            _MEMORY_AUDIT_CONN = sqlite3.connect(":memory:", check_same_thread=False)
        return _MEMORY_AUDIT_CONN
    return sqlite3.connect(AUDIT_DB_PATH)


def init_audit_db():
    conn = _connect_audit_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT,
            session_id TEXT,
            query_id TEXT,
            disease TEXT,
            feedback_type TEXT,
            log_data TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC)")
    conn.commit()
    if _AUDIT_DB_RAW != ":memory:":
        conn.close()


init_audit_db()


def _write_audit_log(
    event_type: str, session_id: str, query_id: str, disease: str, feedback_type: str, data: dict
):
    """DEPRECATED: SQLite compatibility path; use write_audit_log instead."""
    try:
        conn = _connect_audit_db()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO audit_logs (event_type, session_id, query_id, disease, feedback_type, log_data)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (event_type, session_id, query_id, disease, feedback_type, json.dumps(data)),
        )
        conn.commit()
        if _AUDIT_DB_RAW != ":memory:":
            conn.close()
    except Exception as e:
        print(f"Failed to write audit log: {e}")


async def write_audit_log(
    event_type: str,
    session_id: str,
    query_id: str,
    disease: str,
    feedback_type: str,
    data: dict,
) -> None:
    """Write audit logs to the configured backend.

    Postgres is the durable backend, but audit logging must not take down the
    chat path when a development workstation temporarily has Docker stopped.
    """
    if get_audit_storage_backend() == "postgres":
        from .repositories import write_audit_log_db

        try:
            await write_audit_log_db(
                event_type,
                session_id,
                query_id,
                disease,
                feedback_type,
                data,
            )
            return
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Postgres audit write failed; falling back to SQLite audit log "
                "for event_type=%s session_id=%s query_id=%s: %s",
                event_type,
                session_id,
                query_id,
                exc,
            )
            fallback_data = dict(data or {})
            fallback_data.setdefault("audit_backend_fallback", "sqlite")
            fallback_data.setdefault("audit_backend_error", str(exc))
            _write_audit_log(
                event_type,
                session_id,
                query_id,
                disease,
                feedback_type,
                fallback_data,
            )
            return
    _write_audit_log(event_type, session_id, query_id, disease, feedback_type, data)


# Configure structlog
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _normalise_patient_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    # Filter out defaults and empty values before hashing.
    filtered = {
        k: v for k, v in context.items() if v not in ("Select...", "None", "", None, [], {})
    }
    return filtered


def _hash_patient_ref(context: dict[str, Any] | None) -> str:
    """HMAC-SHA-256 patient reference hash for audit-safe context logging."""
    filtered = _normalise_patient_context(context)
    if not filtered:
        return "none"

    context_bytes = json.dumps(filtered, sort_keys=True).encode("utf-8")
    return hmac.new(
        get_patient_salt().encode("utf-8"),
        context_bytes,
        hashlib.sha256,
    ).hexdigest()


def _hash_context(context: dict[str, Any] | None) -> str:
    """DEPRECATED: use _hash_patient_ref."""
    return _hash_patient_ref(context)


def _read_audit_logs_sqlite(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    session_id: str | None = None,
    disease: str | None = None,
    feedback_type: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """Read audit logs through one shared path used by admin endpoints."""
    if _AUDIT_DB_RAW != ":memory:" and not AUDIT_DB_PATH.exists():
        return {"logs": [], "total": 0, "page": page, "limit": limit}

    conn = _connect_audit_db()
    conn.row_factory = sqlite3.Row

    q = "SELECT * FROM audit_logs WHERE 1=1"
    params: list = []

    if start_date:
        q += " AND timestamp >= ?"
        params.append(f"{start_date} 00:00:00")
    if end_date:
        q += " AND timestamp <= ?"
        params.append(f"{end_date} 23:59:59")
    if session_id:
        q += " AND session_id = ?"
        params.append(session_id)
    if disease:
        q += " AND disease LIKE ?"
        params.append(f"%{disease}%")
    if feedback_type:
        q += " AND feedback_type = ?"
        params.append(feedback_type)

    total = conn.execute(q.replace("SELECT *", "SELECT COUNT(*)"), params).fetchone()[0]

    q += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, (page - 1) * limit])
    rows = conn.execute(q, params).fetchall()
    if _AUDIT_DB_RAW != ":memory:":
        conn.close()

    logs = []
    for row in rows:
        item = dict(row)
        if item.get("log_data"):
            with contextlib.suppress(json.JSONDecodeError):
                item["log_data"] = json.loads(item["log_data"])
        logs.append(item)

    return {"logs": logs, "total": total, "page": page, "limit": limit}


def read_audit_logs(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    session_id: str | None = None,
    disease: str | None = None,
    feedback_type: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """DEPRECATED: sync SQLite reader; use read_audit_logs_async."""
    return _read_audit_logs_sqlite(
        start_date=start_date,
        end_date=end_date,
        session_id=session_id,
        disease=disease,
        feedback_type=feedback_type,
        page=page,
        limit=limit,
    )


async def read_audit_logs_async(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    session_id: str | None = None,
    disease: str | None = None,
    feedback_type: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """Read audit logs from the configured backend."""
    if get_audit_storage_backend() == "postgres":
        from .repositories import read_audit_logs_db

        try:
            result = await read_audit_logs_db(
                start_date=start_date,
                end_date=end_date,
                session_id=session_id,
                disease=disease,
                feedback_type=feedback_type,
                page=page,
                limit=limit,
            )
            result["storage_backend"] = "postgres"
            return result
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Postgres audit read failed; reading SQLite fallback audit log: %s",
                exc,
            )
            result = _read_audit_logs_sqlite(
                start_date=start_date,
                end_date=end_date,
                session_id=session_id,
                disease=disease,
                feedback_type=feedback_type,
                page=page,
                limit=limit,
            )
            result["storage_backend"] = "sqlite_fallback"
            result["backend_error"] = str(exc)
            return result
    result = _read_audit_logs_sqlite(
        start_date=start_date,
        end_date=end_date,
        session_id=session_id,
        disease=disease,
        feedback_type=feedback_type,
        page=page,
        limit=limit,
    )
    result["storage_backend"] = "sqlite"
    return result


async def log_query(
    session_id: str,
    query_text: str,
    disease_targets: list[str],
    patient_context: dict[str, Any] | None = None,
):
    """Log initial user query with anonymized patient context."""
    context_hash = _hash_patient_ref(patient_context)
    context_fields = list(patient_context.keys()) if patient_context else []

    await logger.ainfo(
        "QUERY_LOG",
        session_id=session_id,
        query_text=query_text,
        disease_targets=disease_targets,
        patient_context_hash=context_hash,
        context_fields_used=context_fields,
    )
    await write_audit_log(
        "QUERY_LOG",
        session_id,
        "",
        ",".join(disease_targets),
        "",
        {"query_text": query_text, "context_hash": context_hash, "context_fields": context_fields},
    )


async def log_retrieval(
    session_id: str,
    query_id: str,
    tool_name: str,
    search_query: str,
    chunks_returned: int,
    top_score: float,
    latency_ms: float,
    embed_ms: float | None = None,
    vector_search_ms: float | None = None,
    rerank_ms: float | None = None,
    expanded_query: str | None = None,
):
    """Log retrieval performance and results."""
    await logger.ainfo(
        "RETRIEVAL_LOG",
        session_id=session_id,
        query_id=query_id,
        tool_name=tool_name,
        search_query=search_query,
        chunks_returned=chunks_returned,
        top_score=top_score,
        latency_ms=latency_ms,
        embed_ms=embed_ms,
        vector_search_ms=vector_search_ms,
        rerank_ms=rerank_ms,
        expanded_query=expanded_query,
    )


async def log_tool(
    session_id: str,
    query_id: str,
    tool_call_index: int,
    tool_name: str,
    params: dict[str, Any],
    result_length: int,
):
    """Log tool invocation details."""
    await logger.ainfo(
        "TOOL_LOG",
        session_id=session_id,
        query_id=query_id,
        tool_call_index=tool_call_index,
        tool_name=tool_name,
        params=params,
        result_length=result_length,
    )


async def log_response(
    session_id: str,
    query_id: str,
    response_length: int,
    sources_cited: list[dict[str, Any]],
    total_latency_ms: float,
):
    """Log response delivery and quality metrics."""
    await logger.ainfo(
        "RESPONSE_LOG",
        session_id=session_id,
        query_id=query_id,
        response_length=response_length,
        sources_cited=sources_cited,
        total_latency_ms=total_latency_ms,
    )
    await write_audit_log(
        "RESPONSE_LOG",
        session_id,
        query_id,
        "",
        "",
        {
            "response_length": response_length,
            "sources_cited": sources_cited,
            "latency_ms": total_latency_ms,
        },
    )


async def log_feedback(session_id: str, message_id: str, feedback_type: str, note: str = ""):
    """Log explicit user feedback."""
    await logger.ainfo(
        "FEEDBACK_LOG",
        session_id=session_id,
        message_id=message_id,
        feedback_type=feedback_type,
        note=note,
        timestamp=datetime.utcnow().isoformat(),
    )
    await write_audit_log("FEEDBACK_LOG", session_id, message_id, "", feedback_type, {"note": note})


async def log_correction(
    session_id: str,
    message_id: str,
    feedback_type: str,
    correction: str,
    sources_used: list[str],
) -> None:
    """Log feedback corrections through the configured audit backend."""
    await write_audit_log(
        "CORRECTION_LOG",
        session_id,
        message_id,
        "",
        feedback_type,
        {"correction": correction, "sources_used": sources_used},
    )


async def log_init(
    event: str,
    disease: str,
    doc_name: str,
    chunk_count: int,
    latency_ms: float,
    extractor_used: str,
    quality_score: float,
):
    """Log system initialization and ingestion results."""
    await logger.ainfo(
        "INIT_LOG",
        init_event=event,
        disease=disease,
        doc_name=doc_name,
        chunk_count=chunk_count,
        latency_ms=latency_ms,
        extractor_used=extractor_used,
        quality_score=quality_score,
    )


async def log_error(
    session_id: str, query_id: str, error_type: str, traceback: str, recovery_action: str = ""
):
    """Log errors with context for debugging."""
    await logger.aerror(
        "ERROR_LOG",
        session_id=session_id,
        query_id=query_id,
        error_type=error_type,
        traceback=traceback,
        recovery_action=recovery_action,
    )


# --- Deprecated Interface (for compatibility during transition) ---


def log_interaction_to_file(agent, messages):
    """DEPRECATED: Use structured log_* helpers instead.
    Kept for backward compatibility with PoC code.
    """
    # Sync wrapper to avoid breaking older synchronous code
    logger.info("DEPRECATED_LOG_CALL", reason="log_interaction_to_file called")


def print_recent_logs(n: int = 5):
    """DEPRECATED: print recent audit rows for the legacy CLI."""
    conn = _connect_audit_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT timestamp, event_type, session_id, query_id, log_data "
        "FROM audit_logs ORDER BY timestamp DESC LIMIT ?",
        (n,),
    ).fetchall()
    if _AUDIT_DB_RAW != ":memory:":
        conn.close()
    for row in rows:
        print(dict(row))

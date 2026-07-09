"""Observability endpoints.

GET /observability/session/{session_id}
    Full trace for one investigation: every query (from the audit log) plus
    LLM usage and cost.

GET /observability/summary
    Global metrics across all investigations since the server started.

GET /observability/audit/{session_id}
    Just the raw audit records (every SQL query attempted, blocked or run).

This is what makes InsightForge look like a production system rather than a
demo: you can see exactly what the AI did, how long it took, how many queries
it ran, how many it repaired, how many it blocked, and what it cost.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.observability.usage_tracker import usage_tracker
from app.security.audit_log import audit_log

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/session/{session_id}")
async def session_trace(session_id: str) -> dict:
    audit_records = audit_log.get_session(session_id)
    return {
        "session_id": session_id,
        "query_metrics": audit_log.summary(session_id),
        "llm_usage": usage_tracker.session_summary(session_id),
        "queries": [r.to_dict() for r in audit_records],
    }


@router.get("/summary")
async def global_summary() -> dict:
    return {
        "query_metrics": audit_log.summary(),
        "llm_usage": usage_tracker.global_summary(),
        "total_queries_logged": len(audit_log.get_all()),
    }


@router.get("/audit/{session_id}")
async def audit_trail(session_id: str) -> dict:
    records = audit_log.get_session(session_id)
    return {
        "session_id": session_id,
        "count": len(records),
        "records": [r.to_dict() for r in records],
    }

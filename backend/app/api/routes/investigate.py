"""Investigation endpoints.

POST /investigate/interpret
    Parses a business question into a structured QuestionInterpretation.
    Fast (<2s), safe to call without a database (falls back to a schema
    summary stub when the DB is unavailable).

GET  /investigate/schema
    Returns the database schema summary (useful for debugging and the frontend
    investigation timeline in Stage 10).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.question_interpreter import QuestionInterpreter
from app.core.database import get_session
from app.core.logging import get_logger
from app.db.schema_inspector import SchemaInspector
from app.schemas.investigation import InvestigationState, QuestionInterpretation

router = APIRouter(prefix="/investigate", tags=["investigate"])
log = get_logger("api.investigate")

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InterpretRequest(BaseModel):
    question: str
    reference_date: date | None = None   # for testing; defaults to today


class InterpretResponse(BaseModel):
    session_id: str
    interpretation: QuestionInterpretation
    schema_tables_used: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/interpret", response_model=InterpretResponse)
async def interpret_question(
    body: InterpretRequest,
    db: AsyncSession = Depends(get_session),
) -> InterpretResponse:
    """Stage 3: parse the business question into a structured investigation spec."""
    session_id = str(uuid.uuid4())
    log.info("interpret_request", session_id=session_id, question=body.question[:120])

    # Build schema summary for the LLM prompt
    try:
        inspector = await SchemaInspector.build(db.bind)  # type: ignore[arg-type]
        schema_summary = inspector.full_summary()
        schema_tables_used = len(inspector.tables)
    except Exception as exc:
        log.warning("schema_build_failed", error=str(exc))
        schema_summary = "(schema unavailable)"
        schema_tables_used = 0

    interpreter = QuestionInterpreter()

    try:
        interpretation = interpreter.interpret(
            body.question,
            schema_summary=schema_summary,
            reference_date=body.reference_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log.error("interpreter_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Interpreter error. Check ANTHROPIC_API_KEY.") from exc

    log.info(
        "interpret_complete",
        session_id=session_id,
        question_type=interpretation.question_type,
        metric=interpretation.primary_metric_key,
        confidence=interpretation.confidence,
    )
    return InterpretResponse(
        session_id=session_id,
        interpretation=interpretation,
        schema_tables_used=schema_tables_used,
    )


@router.get("/schema")
async def get_schema(db: AsyncSession = Depends(get_session)) -> dict:
    """Return the full schema summary (used by the frontend evidence panel)."""
    try:
        inspector = await SchemaInspector.build(db.bind)  # type: ignore[arg-type]
        tables = [
            {
                "name": t.name,
                "row_count": t.row_count,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "is_primary_key": c.is_primary_key,
                        "foreign_key": c.foreign_key,
                    }
                    for c in t.columns
                ],
            }
            for t in inspector.tables.values()
        ]
        return {"tables": tables, "table_count": len(tables)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Schema unavailable: {exc}") from exc
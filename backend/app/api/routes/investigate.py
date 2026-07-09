"""Investigation endpoints."""
from __future__ import annotations
import uuid
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.question_interpreter import QuestionInterpreter
from app.core.database import get_engine, get_session
from app.core.logging import get_logger
from app.db.schema_inspector import SchemaInspector
from app.schemas.investigation import InvestigationResponse, QuestionInterpretation
from app.services.orchestrator import InvestigationOrchestrator

router = APIRouter(prefix="/investigate", tags=["investigate"])
log = get_logger("api.investigate")


class InterpretRequest(BaseModel):
    question: str
    reference_date: date | None = None


class InterpretResponse(BaseModel):
    session_id: str
    interpretation: QuestionInterpretation
    schema_tables_used: int


class RunRequest(BaseModel):
    question: str
    reference_date: date | None = None
    session_id: str | None = None


@router.post("/interpret", response_model=InterpretResponse)
async def interpret_question(body: InterpretRequest) -> InterpretResponse:
    session_id = str(uuid.uuid4())
    log.info("interpret_request", session_id=session_id, question=body.question[:120])
    try:
        inspector = await SchemaInspector.build(get_engine())
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
    return InterpretResponse(
        session_id=session_id,
        interpretation=interpretation,
        schema_tables_used=schema_tables_used,
    )


@router.post("/run", response_model=InvestigationResponse)
async def run_investigation(body: RunRequest) -> InvestigationResponse:
    """Full end-to-end investigation: interpret -> plan -> SQL -> validate -> insight."""
    log.info("run_request", question=body.question[:120])
    orchestrator = InvestigationOrchestrator(engine=get_engine())
    try:
        return await orchestrator.run(
            body.question,
            reference_date=body.reference_date,
            session_id=body.session_id,
        )
    except Exception as exc:
        log.error("investigation_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/schema")
async def get_schema() -> dict:
    try:
        inspector = await SchemaInspector.build(get_engine())
        tables = [
            {
                "name": t.name,
                "row_count": t.row_count,
                "columns": [
                    {"name": c.name, "type": c.type,
                     "is_primary_key": c.is_primary_key, "foreign_key": c.foreign_key}
                    for c in t.columns
                ],
            }
            for t in inspector.tables.values()
        ]
        return {"tables": tables, "table_count": len(tables)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Schema unavailable: {exc}") from exc

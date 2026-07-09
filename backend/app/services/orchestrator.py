"""Investigation orchestrator.

The main pipeline. Given a question, it runs every stage in sequence:
  1. Interpret the question (QuestionInterpreter)
  2. Build schema snapshot (SchemaInspector)
  3. Plan the investigation steps (InvestigationPlanner)
  4. For each step:
       a. Find relevant tables (SchemaInspector.search)
       b. Generate SQL (SQLGenerator)
       c. Execute through firewall (QueryExecutor)
       d. Repair if failed (QueryRepairAgent, max 3 attempts)
       e. Validate results (result_validator)
  5. Generate insight (InsightGenerator)
  6. Return InvestigationResponse with full evidence trail

All components are injected -- the orchestrator has no direct imports of
concrete implementations -- making it fully testable with mocks.
"""

from __future__ import annotations

import time
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.insight_generator import InsightGenerator
from app.agents.investigation_planner import InvestigationPlanner, InvestigationStep
from app.agents.query_repair import QueryRepairAgent
from app.agents.question_interpreter import QuestionInterpreter
from app.agents.sql_generator import SQLGenerator
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.executor import QueryExecutor, QueryResult
from app.db.schema_inspector import SchemaInspector, TableInfo
from app.schemas.investigation import (
    InvestigationResponse,
    QuestionInterpretation,
    StepResult,
    ValidationWarning,
)
from app.security.audit_log import audit_log
from app.security.sql_firewall import SQLFirewall
from app.services import result_validator

log = get_logger("orchestrator")

# Keywords per step_id used for schema search
_STEP_KEYWORDS: dict[str, list[str]] = {
    "overall_metric":    ["order", "revenue", "amount", "refund", "discount"],
    "by_region":         ["region", "customer", "order", "revenue"],
    "by_segment":        ["segment", "customer", "order", "revenue"],
    "by_category":       ["category", "product", "order", "item", "revenue"],
    "by_product":        ["product", "order", "item", "revenue"],
    "by_campaign":       ["campaign", "order", "marketing"],
    "by_payment_method": ["payment", "order", "method"],
    "refund_check":      ["refund", "order", "category", "product"],
    "order_volume_check":["order", "customer", "item"],
    "monthly_trend":     ["order", "revenue", "amount", "item"],
    "trend_by_region":   ["region", "customer", "order", "revenue"],
    "trend_by_segment":  ["segment", "customer", "order", "revenue"],
    "comparison":        ["order", "revenue", "customer", "item"],
    "lookup":            ["order", "revenue", "item", "amount"],
}

_ALWAYS_INCLUDE = ["orders", "order_items"]  # always relevant for revenue queries


class InvestigationOrchestrator:

    def __init__(
        self,
        engine: AsyncEngine,
        interpreter: QuestionInterpreter | None = None,
        planner: InvestigationPlanner | None = None,
        generator: SQLGenerator | None = None,
        repair_agent: QueryRepairAgent | None = None,
        insight_gen: InsightGenerator | None = None,
    ) -> None:
        self._engine = engine
        self._interpreter = interpreter or QuestionInterpreter()
        self._planner = planner or InvestigationPlanner()
        self._generator = generator or SQLGenerator()
        self._repair = repair_agent or QueryRepairAgent()
        self._insight = insight_gen or InsightGenerator()
        self._settings = get_settings()

    async def run(
        self,
        question: str,
        *,
        reference_date: date | None = None,
        session_id: str | None = None,
    ) -> InvestigationResponse:
        session_id = session_id or str(uuid.uuid4())
        t_start = time.monotonic()
        log.info("investigation_start", session_id=session_id, question=question[:120])

        # ── 1. Interpret ──────────────────────────────────────────────
        interpretation = self._interpreter.interpret(
            question, reference_date=reference_date
        )
        log.info(
            "interpretation_done",
            session_id=session_id,
            metric=interpretation.primary_metric_key,
            question_type=interpretation.question_type,
        )

        # ── 2. Schema ─────────────────────────────────────────────────
        inspector = await SchemaInspector.build(self._engine)

        # ── 3. Plan ───────────────────────────────────────────────────
        plan = self._planner.build_plan(interpretation, session_id)
        log.info("plan_built", session_id=session_id, steps=len(plan.steps))

        # ── 4. Execute each step ──────────────────────────────────────
        firewall = SQLFirewall()
        executor = QueryExecutor(self._engine, firewall=firewall, audit=audit_log)
        step_results: list[StepResult] = []

        for step in plan.steps:
            sr = await self._run_step(
                step, interpretation, inspector, executor, session_id
            )
            step_results.append(sr)

        # ── 5. Insight ────────────────────────────────────────────────
        insight = None
        try:
            insight = self._insight.generate(interpretation, step_results)
        except Exception as exc:
            log.error("insight_generation_failed", error=str(exc))

        total_ms = int((time.monotonic() - t_start) * 1000)
        succeeded = sum(1 for s in step_results if s.succeeded)
        failed = sum(1 for s in step_results if s.status == "failed")

        log.info(
            "investigation_complete",
            session_id=session_id,
            total_ms=total_ms,
            succeeded=succeeded,
            failed=failed,
        )

        return InvestigationResponse(
            session_id=session_id,
            question=question,
            interpretation=interpretation,
            step_results=step_results,
            insight=insight,
            audit_summary=audit_log.summary(session_id),
            total_ms=total_ms,
            steps_succeeded=succeeded,
            steps_failed=failed,
        )

    # ------------------------------------------------------------------
    # Private: execute one step with repair loop
    # ------------------------------------------------------------------

    async def _run_step(
        self,
        step: InvestigationStep,
        interpretation: QuestionInterpretation,
        inspector: SchemaInspector,
        executor: QueryExecutor,
        session_id: str,
    ) -> StepResult:

        log.info("step_start", session_id=session_id, step=step.step_id)

        # Find relevant tables for this step
        keywords = _STEP_KEYWORDS.get(step.step_id, ["order", "revenue"])
        tables = inspector.search(keywords, always_include=_ALWAYS_INCLUDE, top_n=5)

        # Generate SQL
        try:
            sql = self._generator.generate(
                step.description,
                relevant_tables=tables,
                metric_definition=interpretation.metric_definition,
            )
        except Exception as exc:
            log.error("sql_generation_failed", step=step.step_id, error=str(exc))
            return StepResult(
                step_id=step.step_id,
                description=step.description,
                priority=step.priority,
                status="failed",
                error=f"SQL generation error: {exc}",
            )

        # Execute + repair loop
        result, repair_attempts = await self._execute_with_repair(
            sql, tables, step.step_id, executor, session_id
        )

        # Validate
        warnings = result_validator.validate(result, step.step_id) if result.success else []

        status = "failed"
        if result.success:
            status = "repaired" if repair_attempts > 0 else "succeeded"
        elif result.firewall_blocked:
            status = "failed"

        return StepResult(
            step_id=step.step_id,
            description=step.description,
            priority=step.priority,
            status=status,
            sql=result.sql_executed or sql,
            rows=result.rows[:200],           # cap for API response size
            row_count=result.row_count,
            execution_ms=result.execution_ms,
            repair_attempts=repair_attempts,
            validation_warnings=warnings,
            error=result.error,
            tables_referenced=result.tables_referenced,
        )

    async def _execute_with_repair(
        self,
        sql: str,
        tables: list[TableInfo],
        step_id: str,
        executor: QueryExecutor,
        session_id: str,
    ) -> tuple[QueryResult, int]:
        """Execute SQL, repairing up to max_attempts times on failure."""
        max_attempts = self._settings.query_max_repair_attempts
        current_sql = sql
        repair_attempts = 0

        for attempt in range(max_attempts + 1):
            result = await executor.run(
                current_sql,
                session_id=session_id,
                step_name=step_id,
                repair_attempts=repair_attempts,
            )
            if result.success or result.firewall_blocked:
                return result, repair_attempts

            if attempt < max_attempts:
                log.info(
                    "query_repair_attempt",
                    session_id=session_id,
                    step=step_id,
                    attempt=attempt + 1,
                    error=result.error,
                )
                try:
                    current_sql = self._repair.repair(
                        failed_sql=current_sql,
                        error_message=result.error or "Unknown error",
                        relevant_tables=tables,
                        attempt=attempt + 1,
                    )
                    repair_attempts += 1
                except Exception as exc:
                    log.error("repair_agent_failed", error=str(exc))
                    break

        return result, repair_attempts

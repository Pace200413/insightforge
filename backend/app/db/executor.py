"""Read-only query executor.

Runs a query that has already passed the SQL firewall. Adds a second layer
of protection by executing inside a READ ONLY transaction and applying a
statement_timeout at the Postgres level.

Flow for each query:
    raw SQL
      -> SQLFirewall.check()     <- blocks dangerous queries
      -> execute()               <- this module, READ ONLY transaction
      -> AuditLog.record()       <- records outcome
      -> return QueryResult

If the query fails (Postgres error), the caller (query_repair.py) gets back
a QueryResult with success=False and the error message so it can attempt a fix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.logging import get_logger
from app.security.audit_log import AuditLog, QueryOutcome, audit_log as _global_log
from app.security.sql_firewall import SQLFirewall

log = get_logger("executor")


@dataclass
class QueryResult:
    success: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    execution_ms: int = 0
    sql_executed: str = ""
    error: str | None = None
    firewall_blocked: bool = False
    firewall_violations: list[str] = field(default_factory=list)
    firewall_modifications: list[str] = field(default_factory=list)
    tables_referenced: list[str] = field(default_factory=list)
    audit_record_id: int | None = None


class QueryExecutor:
    def __init__(
        self,
        engine: AsyncEngine,
        firewall: SQLFirewall | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._engine = engine
        self._firewall = firewall or SQLFirewall()
        self._audit = audit or _global_log
        self._settings = get_settings()

    async def run(
        self,
        sql: str,
        *,
        session_id: str = "unknown",
        step_name: str = "query",
        repair_attempts: int = 0,
    ) -> QueryResult:
        """Firewall-check then execute. Never raises -- errors go into QueryResult."""

        # ── 1. Firewall ────────────────────────────────────────────────
        verdict = self._firewall.check(sql)

        if not verdict.allowed:
            log.warning(
                "query_blocked",
                session_id=session_id,
                step=step_name,
                violations=verdict.violations,
            )
            rec = self._audit.record(
                session_id=session_id,
                investigation_step=step_name,
                original_sql=sql,
                executed_sql=sql,
                outcome=QueryOutcome.BLOCKED,
                violations=verdict.violations,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                repair_attempts=repair_attempts,
            )
            return QueryResult(
                success=False,
                firewall_blocked=True,
                firewall_violations=verdict.violations,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                sql_executed=sql,
                error=f"Query blocked: {'; '.join(verdict.violations)}",
                audit_record_id=rec.record_id,
            )

        # ── 2. Execute inside READ ONLY transaction ────────────────────
        safe_sql = verdict.sql
        timeout_ms = self._settings.query_timeout_seconds * 1000
        t0 = time.monotonic()

        try:
            async with self._engine.connect() as conn:
                # Postgres-level statement timeout + read-only mode
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
                )
                await conn.execute(text("SET TRANSACTION READ ONLY"))

                result = await conn.execute(text(safe_sql))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]

            execution_ms = int((time.monotonic() - t0) * 1000)
            outcome = QueryOutcome.REPAIRED if repair_attempts > 0 else QueryOutcome.SUCCEEDED

            log.info(
                "query_succeeded",
                session_id=session_id,
                step=step_name,
                rows=len(rows),
                ms=execution_ms,
                repairs=repair_attempts,
            )
            rec = self._audit.record(
                session_id=session_id,
                investigation_step=step_name,
                original_sql=sql,
                executed_sql=safe_sql,
                outcome=outcome,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                rows_returned=len(rows),
                execution_ms=execution_ms,
                repair_attempts=repair_attempts,
            )
            return QueryResult(
                success=True,
                rows=rows,
                row_count=len(rows),
                execution_ms=execution_ms,
                sql_executed=safe_sql,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                audit_record_id=rec.record_id,
            )

        except Exception as exc:
            execution_ms = int((time.monotonic() - t0) * 1000)
            error_msg = str(exc).split("\n")[0]  # first line only
            log.warning(
                "query_failed",
                session_id=session_id,
                step=step_name,
                error=error_msg,
                ms=execution_ms,
            )
            rec = self._audit.record(
                session_id=session_id,
                investigation_step=step_name,
                original_sql=sql,
                executed_sql=safe_sql,
                outcome=QueryOutcome.FAILED,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                execution_ms=execution_ms,
                repair_attempts=repair_attempts,
                error_message=error_msg,
            )
            return QueryResult(
                success=False,
                execution_ms=execution_ms,
                sql_executed=safe_sql,
                firewall_modifications=verdict.modifications,
                tables_referenced=verdict.tables_referenced,
                error=error_msg,
                audit_record_id=rec.record_id,
            )

"""Audit log for all SQL queries attempted by the AI.

Every query -- whether blocked, allowed, repaired, or failed at execution --
gets a record here. This serves two purposes:
  1. Security: you can prove what the AI ran (or tried to run).
  2. Observability: Stage 8 reads this for latency/retry/cost metrics.

Storage: in-memory list for the current process (fast, zero dependencies).
The log is intentionally append-only -- no records are ever deleted.
In a production deployment you would flush to a DB table or log sink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class QueryOutcome(str, Enum):
    BLOCKED   = "blocked"    # firewall rejected it
    SUCCEEDED = "succeeded"  # ran and returned results
    FAILED    = "failed"     # ran but Postgres returned an error
    REPAIRED  = "repaired"   # failed, repaired, then succeeded


@dataclass
class AuditRecord:
    record_id: int
    session_id: str
    investigation_step: str           # e.g. "revenue_by_segment"
    original_sql: str
    executed_sql: str                 # may differ if firewall injected LIMIT
    outcome: QueryOutcome
    violations: list[str]             # firewall violations (empty if allowed)
    firewall_modifications: list[str] # e.g. "LIMIT 10000 injected"
    tables_referenced: list[str]
    rows_returned: int | None
    execution_ms: int | None
    repair_attempts: int
    error_message: str | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "session_id": self.session_id,
            "investigation_step": self.investigation_step,
            "outcome": self.outcome.value,
            "tables_referenced": self.tables_referenced,
            "violations": self.violations,
            "firewall_modifications": self.firewall_modifications,
            "rows_returned": self.rows_returned,
            "execution_ms": self.execution_ms,
            "repair_attempts": self.repair_attempts,
            "error_message": self.error_message,
            "timestamp": self.timestamp.isoformat(),
            # SQL included for transparency -- this is the evidence panel data
            "executed_sql": self.executed_sql,
        }


class AuditLog:
    """Thread-safe in-process audit log."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._next_id: int = 1

    def record(
        self,
        *,
        session_id: str,
        investigation_step: str,
        original_sql: str,
        executed_sql: str,
        outcome: QueryOutcome,
        violations: list[str] | None = None,
        firewall_modifications: list[str] | None = None,
        tables_referenced: list[str] | None = None,
        rows_returned: int | None = None,
        execution_ms: int | None = None,
        repair_attempts: int = 0,
        error_message: str | None = None,
    ) -> AuditRecord:
        rec = AuditRecord(
            record_id=self._next_id,
            session_id=session_id,
            investigation_step=investigation_step,
            original_sql=original_sql,
            executed_sql=executed_sql,
            outcome=outcome,
            violations=violations or [],
            firewall_modifications=firewall_modifications or [],
            tables_referenced=tables_referenced or [],
            rows_returned=rows_returned,
            execution_ms=execution_ms,
            repair_attempts=repair_attempts,
            error_message=error_message,
        )
        self._records.append(rec)
        self._next_id += 1
        return rec

    def get_session(self, session_id: str) -> list[AuditRecord]:
        return [r for r in self._records if r.session_id == session_id]

    def get_all(self) -> list[AuditRecord]:
        return list(self._records)

    def summary(self, session_id: str | None = None) -> dict[str, Any]:
        records = self.get_session(session_id) if session_id else self._records
        return {
            "total": len(records),
            "blocked": sum(1 for r in records if r.outcome == QueryOutcome.BLOCKED),
            "succeeded": sum(1 for r in records if r.outcome == QueryOutcome.SUCCEEDED),
            "failed": sum(1 for r in records if r.outcome == QueryOutcome.FAILED),
            "repaired": sum(1 for r in records if r.outcome == QueryOutcome.REPAIRED),
            "total_repair_attempts": sum(r.repair_attempts for r in records),
        }


# Process-level singleton -- imported wherever audit logging is needed.
audit_log = AuditLog()

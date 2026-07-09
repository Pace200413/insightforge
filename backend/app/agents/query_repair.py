"""Query repair agent.

When a query fails at execution, this agent:
  1. Reads the Postgres error message.
  2. Looks at the schema for the tables that were referenced.
  3. Asks Claude to identify the likely cause and fix only that part.
  4. Returns a corrected SQL string.

Hard limits:
  - At most MAX_ATTEMPTS repairs per query (default 3, from config).
  - The repair prompt receives the error message and schema -- NOT the full
    investigation history, which would be expensive and distracting.
  - Each attempt is still passed through the firewall before execution.
    The repair agent cannot bypass security.

We show the repair loop in the API response so the frontend investigation
timeline can display: "Query failed → Repairing (attempt 1/3) → Succeeded."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from app.core.config import get_settings
from app.db.schema_inspector import TableInfo


class LLMClient(Protocol):
    def create_message(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict]
    ) -> Any: ...


class AnthropicRepairClient:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def create_message(self, *, model, max_tokens, system, messages):
        return self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages
        )


_REPAIR_SYSTEM = """\
You are the SQL repair component of InsightForge.

A PostgreSQL query failed. Your job is to fix it.

## SCHEMA (tables referenced by the failed query)
{schema}

## RULES
- Return ONLY the corrected SQL -- no explanation, no markdown fences.
- Fix only what caused the error; preserve the intent of the original query.
- Common causes: wrong column name, missing JOIN, wrong table alias,
  aggregation outside GROUP BY, syntax error, ambiguous column reference.
- Do NOT reference the `email` column.
- Do NOT change SELECT to any mutating statement.
"""


@dataclass
class RepairResult:
    repaired: bool
    sql: str              # the repaired SQL (or original if repair failed)
    attempts: int
    last_error: str | None = None


class QueryRepairAgent:
    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 2048

    def __init__(self, client: LLMClient | None = None) -> None:
        from app.core.llm_factory import get_completion_client
        self._client = client or get_completion_client()
        self._max_attempts = get_settings().query_max_repair_attempts

    def repair(
        self,
        failed_sql: str,
        error_message: str,
        relevant_tables: list[TableInfo],
        *,
        attempt: int = 1,
    ) -> str:
        """Return a corrected SQL string. Caller handles retry loop and limits."""
        from app.db.schema_inspector import SchemaInspector  # avoid circular
        # Re-use the describe_tables formatter from SchemaInspector
        # We don't have an inspector instance here, so we inline a minimal version.
        schema_text = _format_tables(relevant_tables)

        system = _REPAIR_SYSTEM.format(schema=schema_text)
        user_msg = (
            f"FAILED QUERY (attempt {attempt}/{self._max_attempts}):\n"
            f"```sql\n{failed_sql}\n```\n\n"
            f"ERROR FROM POSTGRES:\n{error_message}\n\n"
            f"Return the corrected SQL:"
        )

        response = self._client.create_message(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )

        fixed = ""
        for block in response.content:
            if hasattr(block, "text"):
                fixed += block.text
        fixed = fixed.strip().lstrip("```sql").lstrip("```").rstrip("```").strip()
        return fixed

    @property
    def max_attempts(self) -> int:
        return self._max_attempts


def _format_tables(tables: list[TableInfo]) -> str:
    lines: list[str] = []
    for t in tables:
        lines.append(f"TABLE {t.name}:")
        for col in t.columns:
            pk = " [PK]" if col.is_primary_key else ""
            fk = f" -> {col.foreign_key}" if col.foreign_key else ""
            lines.append(f"  {col.name}  {col.type}{pk}{fk}")
        lines.append("")
    return "\n".join(lines).strip()

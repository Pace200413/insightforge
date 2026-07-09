"""SQL generator -- converts an InvestigationStep description into SQL.

The generator receives:
  - The step description (plain English of what to calculate).
  - The relevant schema tables (already filtered by the schema inspector).
  - The approved metric definition.
  - Key warnings about known data traps in this schema.

It returns a single SQL string. The SQL is then passed to the firewall and
executor; if it fails, query_repair.py attempts a fix.

Design note: the system prompt embeds the schema and metric definition rather
than relying on Claude's training data. This is important because column names
like `order_items.unit_price` vs `products.price` are dataset-specific and
Claude would hallucinate them without explicit context.
"""

from __future__ import annotations

from typing import Any, Protocol

import anthropic

from app.core.config import get_settings
from app.db.schema_inspector import TableInfo


# ---------------------------------------------------------------------------
# LLM client protocol (same pattern as question_interpreter -- mockable)
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    def create_message(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict]
    ) -> Any: ...


class AnthropicLLMClient:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def create_message(self, *, model, max_tokens, system, messages):
        return self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages
        )


# ---------------------------------------------------------------------------
# Schema + metric formatting helpers
# ---------------------------------------------------------------------------


def _format_schema(tables: list[TableInfo]) -> str:
    lines: list[str] = []
    for t in tables:
        lines.append(f"TABLE {t.name}:")
        for col in t.columns:
            pk  = " [PK]" if col.is_primary_key else ""
            fk  = f" -> {col.foreign_key}" if col.foreign_key else ""
            lines.append(f"  {col.name}  {col.type}{pk}{fk}")
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are the SQL-generation component of InsightForge, an AI business analyst.

Your ONLY job is to write a single valid PostgreSQL SELECT query.

## OUTPUT FORMAT
Return ONLY the SQL query -- no explanation, no markdown fences, no comments before or
after. The first character of your response must be 'W', 'S', or '(' (WITH/SELECT/subquery).

## SCHEMA
{schema}

## METRIC DEFINITION
{metric_definition}

## CRITICAL RULES (violations will cause the query to be blocked or give wrong answers)
1. Use order_items.unit_price for revenue -- NOT products.price.
   products.price is the CURRENT price; order_items.unit_price is the price AT ORDER TIME.
2. Always filter orders.status = 'completed' unless the step explicitly says otherwise.
3. To avoid row duplication when joining orders to order_items:
   - Aggregate order_items first in a subquery, THEN join to orders.
   - Or use SUM(...) with GROUP BY that prevents fan-out.
4. Refunds: join from orders to refunds (not through order_items).
   Use LEFT JOIN and COALESCE(SUM(r.amount), 0).
5. Region is on customers, NOT on orders. Always join orders -> customers -> regions.
6. Do NOT reference the `email` column (blocked by security policy).
7. The query MUST be a SELECT. No INSERT, UPDATE, DELETE, DROP, TRUNCATE.
8. Include a LIMIT clause (max 10000 rows for detail queries; aggregations don't need it).
"""


# ---------------------------------------------------------------------------
# SQL Generator
# ---------------------------------------------------------------------------


class SQLGenerator:
    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 2048

    def __init__(self, client: LLMClient | None = None) -> None:
        from app.core.llm_factory import get_completion_client
        self._client = client or get_completion_client()

    def generate(
        self,
        step_description: str,
        *,
        relevant_tables: list[TableInfo],
        metric_definition: str = "",
    ) -> str:
        """Generate a SQL query for the given step description.

        Returns the raw SQL string. The caller (executor pipeline) handles
        firewall checking and error recovery.
        """
        system = _SYSTEM.format(
            schema=_format_schema(relevant_tables),
            metric_definition=metric_definition or "Use SUM(order_items.quantity * order_items.unit_price) for gross revenue. Net revenue subtracts refunds and discounts.",
        )

        response = self._client.create_message(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": step_description}],
        )

        sql = self._extract_sql(response)
        return sql

    @staticmethod
    def _extract_sql(response: Any) -> str:
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
        # Strip accidental markdown fences
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()
        return text

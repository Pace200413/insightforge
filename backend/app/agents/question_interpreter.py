"""Question interpreter -- the first AI agent in the pipeline.

Parses a raw business question into a structured `QuestionInterpretation`
by calling Claude with a tool definition that enforces the output schema.

Key guarantees:
  - The metric returned ALWAYS matches a key in metrics.yaml.
    If the question mentions an unknown metric, the interpreter picks the
    closest approved metric and notes the assumption in `interpretation_notes`.
  - Date periods are resolved to absolute ISO dates using the `reference_date`
    parameter (defaults to today), so "last month" becomes deterministic.
  - The LLM client is injected, making the interpreter fully unit-testable
    without an API key (see tests/test_question_interpreter.py).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

import anthropic
import yaml

from app.core.config import get_settings
from app.schemas.investigation import QuestionInterpretation, TimePeriod

# ---------------------------------------------------------------------------
# Metric definitions (loaded once from semantic_layer/metrics.yaml)
# ---------------------------------------------------------------------------

_METRICS_PATH = Path(__file__).resolve().parents[2] / "app" / "semantic_layer" / "metrics.yaml"


def _load_metrics() -> dict[str, dict]:
    raw = yaml.safe_load(_METRICS_PATH.read_text())
    return raw.get("metrics", {})


# ---------------------------------------------------------------------------
# LLM client protocol (enables test injection)
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> Any: ...


class AnthropicLLMClient:
    """Thin wrapper so the real Anthropic client satisfies LLMClient."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def create_message(self, *, model, max_tokens, system, messages, tools):
        return self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


# ---------------------------------------------------------------------------
# Tool definition (enforces structured output from Claude)
# ---------------------------------------------------------------------------

_INTERPRET_TOOL = {
    "name": "interpret_business_question",
    "description": (
        "Parse a business question into a structured investigation specification. "
        "Always use this tool -- never respond in plain text."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "question_type",
            "primary_metric_key",
            "period_label",
            "period_start",
            "period_end",
            "dimensions_to_investigate",
            "needs_root_cause_investigation",
            "interpretation_notes",
            "confidence",
        ],
        "properties": {
            "question_type": {
                "type": "string",
                "enum": ["root_cause", "trend", "comparison", "lookup"],
                "description": (
                    "root_cause: WHY did X change. "
                    "trend: HOW has X changed over time. "
                    "comparison: X vs Y across a dimension. "
                    "lookup: what is the current value of X."
                ),
            },
            "primary_metric_key": {
                "type": "string",
                "description": "Must be one of the approved metric keys listed in the system prompt.",
            },
            "period_label": {
                "type": "string",
                "description": "Human label: 'June 2026', 'Q2 2026', etc.",
            },
            "period_start": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            "period_end": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            "comparison_period_label": {
                "type": "string",
                "description": "Label for the comparison/baseline period, or empty string if none.",
            },
            "comparison_start": {
                "type": "string",
                "description": "ISO date or empty string.",
            },
            "comparison_end": {
                "type": "string",
                "description": "ISO date or empty string.",
            },
            "dimensions_to_investigate": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Which breakdown dimensions the investigation should explore. "
                    "Choose from: region, segment, category, product, campaign, payment_method."
                ),
            },
            "needs_root_cause_investigation": {
                "type": "boolean",
                "description": "True when the question asks WHY something happened.",
            },
            "interpretation_notes": {
                "type": "string",
                "description": "Assumptions made, ambiguities in the question, or metric-mapping notes.",
            },
            "confidence": {
                "type": "number",
                "description": "0.0-1.0: how confident you are in this interpretation.",
            },
        },
    },
}

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def _build_system_prompt(metrics: dict[str, dict], schema_summary: str, reference_date: date) -> str:
    metric_block = "\n".join(
        f"  {key}: {meta['label']} -- {meta['description']}"
        for key, meta in metrics.items()
    )
    return f"""You are the question-interpretation stage of an AI business analyst called InsightForge.

Today's date: {reference_date.isoformat()}

Your job is to parse the user's business question and call the `interpret_business_question` tool
with a structured specification. You MUST call the tool -- never reply in plain text.

## APPROVED METRICS (you may ONLY use these keys in primary_metric_key):
{metric_block}

If the question mentions "revenue" without qualification, default to `net_revenue`.
If the metric is ambiguous, pick the closest approved key and note it in interpretation_notes.

## DATABASE TABLES AVAILABLE:
{schema_summary}

## PERIOD RESOLUTION RULES (reference date: {reference_date.isoformat()}):
- "last month" = the full calendar month immediately before today's month.
- "this month" = from the 1st of today's month to today.
- "last quarter" = the full quarter before today's quarter.
- "year to date" = January 1st of today's year to today.
- Always emit absolute ISO dates (YYYY-MM-DD), never relative expressions.

## COMPARISON PERIOD:
- For root_cause and trend questions, always provide a comparison_period:
  "last month" -> compare against the same month one year ago AND the previous month.
  Default comparison is the immediately preceding period of equal length.
- For lookup questions, comparison_period is empty.

## DIMENSIONS:
- "why did revenue drop" -> investigate ALL of: region, segment, category, campaign.
- "why did EMEA revenue drop" -> investigate region, segment, category (narrowed).
- "which product category" -> investigate only category.
"""


# ---------------------------------------------------------------------------
# Main interpreter class
# ---------------------------------------------------------------------------


class QuestionInterpreter:
    """Converts a natural-language question into a QuestionInterpretation."""

    MODEL = "claude-sonnet-4-6"

    def __init__(self, client: LLMClient | None = None) -> None:
        from app.core.llm_factory import get_interpreter_client
        self._client = client or get_interpreter_client()
        self._metrics = _load_metrics()

    def interpret(
        self,
        question: str,
        *,
        schema_summary: str = "(schema not available)",
        reference_date: date | None = None,
    ) -> QuestionInterpretation:
        """Run the interpreter and return a validated QuestionInterpretation.

        Raises:
            ValueError: if the LLM returns an unrecognised metric key or
                        doesn't call the tool.
        """
        ref = reference_date or date.today()
        system = _build_system_prompt(self._metrics, schema_summary, ref)

        response = self._client.create_message(
            model=self.MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": question}],
            tools=[_INTERPRET_TOOL],
        )

        tool_input = self._extract_tool_input(response)
        return self._build_interpretation(question, tool_input)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_tool_input(self, response: Any) -> dict[str, Any]:
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                return dict(block.input)
        raise ValueError(
            "Claude did not call the interpret_business_question tool. "
            f"Stop reason: {response.stop_reason}. "
            "Check the system prompt or increase max_tokens."
        )

    def _build_interpretation(
        self, question: str, t: dict[str, Any]
    ) -> QuestionInterpretation:
        metric_key = t["primary_metric_key"]
        if metric_key not in self._metrics:
            # Fallback: pick net_revenue and note the substitution
            t["interpretation_notes"] = (
                f"Requested metric '{metric_key}' is not in the approved list. "
                f"Substituted 'net_revenue'. " + t.get("interpretation_notes", "")
            )
            metric_key = "net_revenue"

        metric_meta = self._metrics[metric_key]

        comparison: TimePeriod | None = None
        if t.get("comparison_start") and t.get("comparison_end"):
            comparison = TimePeriod(
                label=t.get("comparison_period_label", "comparison period"),
                start_date=date.fromisoformat(t["comparison_start"]),
                end_date=date.fromisoformat(t["comparison_end"]),
            )

        return QuestionInterpretation(
            original_question=question,
            question_type=t["question_type"],
            primary_metric_key=metric_key,
            metric_label=metric_meta["label"],
            metric_definition=metric_meta["description"],
            period=TimePeriod(
                label=t["period_label"],
                start_date=date.fromisoformat(t["period_start"]),
                end_date=date.fromisoformat(t["period_end"]),
            ),
            comparison_period=comparison,
            dimensions=t["dimensions_to_investigate"],
            needs_root_cause_investigation=t["needs_root_cause_investigation"],
            interpretation_notes=t["interpretation_notes"],
            confidence=float(t.get("confidence", 0.8)),
        )
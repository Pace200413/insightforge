"""Insight generator.

The final AI agent in the pipeline. Receives all validated step results and
produces a structured InsightReport that:
  - Separates FACTS (observed in data) from HYPOTHESES (inferred causes).
  - Quantifies each finding with the actual numbers from the query results.
  - Lists data-quality warnings that limit confidence.
  - States explicitly what the database cannot tell us.

Output is parsed from JSON -- never from free text -- so the evidence panel
can render each finding with its supporting step IDs and confidence scores.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.config import get_settings
from app.schemas.investigation import (
    Finding,
    InsightReport,
    QuestionInterpretation,
    StepResult,
    ValidationWarning,
)


class LLMClient(Protocol):
    def create_message(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict]
    ) -> Any: ...


class AnthropicInsightClient:
    def __init__(self) -> None:
        import anthropic
        self._client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)

    def create_message(self, *, model, max_tokens, system, messages):
        return self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages
        )


_SYSTEM = """\
You are the insight-generation component of InsightForge, an AI business analyst.

You receive validated SQL query results from a multi-step business investigation.
Your job is to synthesise them into a structured JSON insight report.

## CRITICAL RULES
1. Base every finding ONLY on the provided query results. No invention.
2. Separate FACTS (directly observed in data) from HYPOTHESES (inferred causes).
3. Quantify everything: use actual numbers from the results.
4. For root_cause investigations: identify which dimension contributed MOST to the change.
5. Flag data-quality issues that limit confidence.
6. State what the database CANNOT prove (e.g. WHY enterprise customers left).

## OUTPUT FORMAT
Return ONLY valid JSON matching this exact structure. Start your response with { and end with }. No markdown fences, no prose before or after:
{
  "summary": "<1-2 sentence TL;DR with the main finding and key number>",
  "primary_cause": "<the single most important cause in one sentence>",
  "findings": [
    {
      "title": "<short title>",
      "finding_type": "<fact|hypothesis|data_quality|warning>",
      "description": "<explanation with specific numbers from the data>",
      "supporting_step_ids": ["<step_id>"],
      "confidence": <0.0-1.0>,
      "magnitude": "<optional: e.g. -59% enterprise volume>"
    }
  ],
  "conclusion": "<2-4 paragraph evidence-backed explanation>",
  "data_quality_warnings": ["<warning 1>", ...],
  "unanswered_questions": ["<what we cannot prove from the DB>", ...],
  "overall_confidence": <0.0-1.0>
}
"""


def _summarise_step(step: StepResult) -> dict:
    """Condense a step result to a prompt-friendly dict (no raw rows > 10)."""
    preview_rows = step.rows[:10]
    # Serialise: convert Decimal/date to str
    safe_rows = []
    for row in preview_rows:
        safe_row = {}
        for k, v in row.items():
            try:
                json.dumps(v)
                safe_row[k] = v
            except (TypeError, ValueError):
                safe_row[k] = str(v)
        safe_rows.append(safe_row)

    return {
        "step_id": step.step_id,
        "status": step.status,
        "row_count": step.row_count,
        "description": step.description,
        "sample_rows": safe_rows,
        "validation_warnings": [w.message for w in step.validation_warnings],
        "repair_attempts": step.repair_attempts,
    }


class InsightGenerator:
    MODEL = "claude-sonnet-4-6"
    MAX_TOKENS = 2048

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or self._default_client()

    @staticmethod
    def _default_client() -> LLMClient:
        from app.core.llm_factory import get_completion_client
        return get_completion_client()

    def generate(
        self,
        interpretation: QuestionInterpretation,
        step_results: list[StepResult],
    ) -> InsightReport:
        succeeded = [s for s in step_results if s.succeeded]
        if not succeeded:
            return self._no_data_report(interpretation)

        user_msg = json.dumps({
            "question": interpretation.original_question,
            "question_type": interpretation.question_type,
            "primary_metric": interpretation.primary_metric_key,
            "metric_definition": interpretation.metric_definition,
            "period": interpretation.period.label,
            "comparison_period": interpretation.comparison_period.label
                if interpretation.comparison_period else None,
            "step_results": [_summarise_step(s) for s in step_results],
        }, indent=2)

        response = self._client.create_message(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw += block.text
        # Extract the JSON object even if the model wrapped it in prose/fences
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        raw = m.group(0) if m else raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._parse_error_report(raw, interpretation)

        findings = [
            Finding(
                title=f.get("title", "Finding"),
                finding_type=f.get("finding_type", "fact"),
                description=f.get("description", ""),
                supporting_step_ids=f.get("supporting_step_ids", []),
                confidence=float(f.get("confidence", 0.7)),
                magnitude=f.get("magnitude"),
            )
            for f in data.get("findings", [])
        ]

        return InsightReport(
            summary=data.get("summary", ""),
            findings=findings,
            primary_cause=data.get("primary_cause", ""),
            conclusion=data.get("conclusion", ""),
            data_quality_warnings=data.get("data_quality_warnings", []),
            unanswered_questions=data.get("unanswered_questions", []),
            overall_confidence=float(data.get("overall_confidence", 0.7)),
            investigated_at=datetime.now(timezone.utc),
        )

    def _no_data_report(self, interpretation: QuestionInterpretation) -> InsightReport:
        return InsightReport(
            summary="Investigation could not retrieve sufficient data.",
            findings=[],
            primary_cause="All queries failed or returned empty results.",
            conclusion="No conclusions can be drawn. Check the database connection and query logs.",
            data_quality_warnings=["All investigation steps failed."],
            unanswered_questions=[interpretation.original_question],
            overall_confidence=0.0,
        )

    def _parse_error_report(self, raw: str, interpretation: QuestionInterpretation) -> InsightReport:
        return InsightReport(
            summary="Insight generation produced malformed output.",
            findings=[Finding(
                title="Raw LLM output",
                finding_type="warning",
                description=raw[:500],
                supporting_step_ids=[],
                confidence=0.0,
            )],
            primary_cause="JSON parsing failed.",
            conclusion="The LLM did not return valid JSON. Review the raw output above.",
            data_quality_warnings=["Insight JSON parse error."],
            unanswered_questions=[interpretation.original_question],
            overall_confidence=0.0,
        )

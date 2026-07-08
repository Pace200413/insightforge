"""Unit tests for QuestionInterpreter -- LLM client is mocked entirely.

These tests verify:
  - Correct Pydantic model construction from tool output
  - Metric key validation and fallback
  - Period parsing
  - Unknown metric substitution
No ANTHROPIC_API_KEY is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from app.agents.question_interpreter import QuestionInterpreter
from app.schemas.investigation import QuestionInterpretation


# ---------------------------------------------------------------------------
# Minimal stub that satisfies the LLMClient protocol
# ---------------------------------------------------------------------------


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    input: dict = field(default_factory=dict)


@dataclass
class _MockResponse:
    content: list
    stop_reason: str = "tool_use"


class MockLLMClient:
    """Returns a pre-canned tool call response without hitting the network."""

    def __init__(self, tool_input: dict) -> None:
        self._input = tool_input

    def create_message(self, *, model, max_tokens, system, messages, tools) -> Any:
        return _MockResponse(content=[_ToolUseBlock(input=self._input)])


def _client(overrides: dict) -> MockLLMClient:
    """Build a mock client with sensible defaults, overridden by `overrides`."""
    defaults = {
        "question_type": "root_cause",
        "primary_metric_key": "net_revenue",
        "period_label": "June 2026",
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
        "comparison_period_label": "May 2026",
        "comparison_start": "2026-05-01",
        "comparison_end": "2026-05-31",
        "dimensions_to_investigate": ["region", "segment", "category"],
        "needs_root_cause_investigation": True,
        "interpretation_notes": "Resolved 'last month' to June 2026.",
        "confidence": 0.9,
    }
    return MockLLMClient({**defaults, **overrides})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_basic_root_cause_question():
    interp = QuestionInterpreter(client=_client({})).interpret(
        "Why did revenue drop last month?",
        reference_date=date(2026, 7, 1),
    )
    assert isinstance(interp, QuestionInterpretation)
    assert interp.question_type == "root_cause"
    assert interp.primary_metric_key == "net_revenue"
    assert interp.period.start_date == date(2026, 6, 1)
    assert interp.period.end_date == date(2026, 6, 30)
    assert interp.needs_root_cause_investigation is True
    assert "segment" in interp.dimensions


def test_comparison_period_parsed():
    interp = QuestionInterpreter(client=_client({})).interpret(
        "Why did revenue drop last month?",
        reference_date=date(2026, 7, 1),
    )
    assert interp.comparison_period is not None
    assert interp.comparison_period.start_date == date(2026, 5, 1)
    assert interp.comparison_period.label == "May 2026"


def test_metric_label_and_definition_populated():
    interp = QuestionInterpreter(client=_client({})).interpret("revenue?")
    assert interp.metric_label == "Net Revenue"
    assert "refund" in interp.metric_definition.lower()


def test_unknown_metric_key_falls_back_to_net_revenue():
    interp = QuestionInterpreter(
        client=_client({"primary_metric_key": "magic_revenue"})
    ).interpret("What is our magic revenue?")
    assert interp.primary_metric_key == "net_revenue"
    assert "magic_revenue" in interp.interpretation_notes


def test_lookup_question_has_no_comparison_period():
    interp = QuestionInterpreter(
        client=_client({
            "question_type": "lookup",
            "needs_root_cause_investigation": False,
            "comparison_period_label": "",
            "comparison_start": "",
            "comparison_end": "",
            "dimensions_to_investigate": [],
        })
    ).interpret("What is our net revenue this month?")
    assert interp.comparison_period is None
    assert interp.question_type == "lookup"


def test_confidence_within_bounds():
    for conf in [0.0, 0.5, 1.0]:
        interp = QuestionInterpreter(
            client=_client({"confidence": conf})
        ).interpret("test question")
        assert 0.0 <= interp.confidence <= 1.0


def test_no_tool_call_raises_value_error():
    @dataclass
    class NoToolResponse:
        content: list = field(default_factory=list)
        stop_reason: str = "end_turn"

    class BadClient:
        def create_message(self, **kwargs):
            return NoToolResponse()

    with pytest.raises(ValueError, match="did not call"):
        QuestionInterpreter(client=BadClient()).interpret("anything")


def test_trend_question_type():
    interp = QuestionInterpreter(
        client=_client({
            "question_type": "trend",
            "needs_root_cause_investigation": False,
            "dimensions_to_investigate": ["category"],
        })
    ).interpret("How has revenue trended over the last 6 months?")
    assert interp.question_type == "trend"
    assert not interp.needs_root_cause_investigation


def test_original_question_preserved():
    q = "Why did enterprise revenue collapse in June?"
    interp = QuestionInterpreter(client=_client({})).interpret(q)
    assert interp.original_question == q
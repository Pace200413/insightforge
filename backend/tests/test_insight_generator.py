"""Tests for InsightGenerator -- LLM fully mocked."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from app.agents.insight_generator import InsightGenerator
from app.schemas.investigation import (
    QuestionInterpretation, TimePeriod, StepResult
)


@dataclass
class _Block:
    text: str

@dataclass
class _MockResp:
    content: list
    stop_reason: str = "end_turn"

def _mock_client(payload: dict):
    class C:
        def create_message(self, **kwargs) -> Any:
            return _MockResp(content=[_Block(json.dumps(payload))])
    return C()

def _interp():
    return QuestionInterpretation(
        original_question="Why did revenue drop last month?",
        question_type="root_cause",
        primary_metric_key="net_revenue",
        metric_label="Net Revenue",
        metric_definition="Gross minus refunds and discounts.",
        period=TimePeriod(label="June 2026", start_date=date(2026,6,1), end_date=date(2026,6,30)),
        comparison_period=TimePeriod(label="May 2026", start_date=date(2026,5,1), end_date=date(2026,5,31)),
        dimensions=["segment","region"],
        needs_root_cause_investigation=True,
        interpretation_notes="",
        confidence=0.9,
    )

def _step(step_id="by_segment", status="succeeded"):
    return StepResult(
        step_id=step_id, description="Compare revenue by segment",
        priority=2, status=status,
        rows=[{"segment":"enterprise","revenue":500000},{"segment":"consumer","revenue":1000000}],
        row_count=2,
    )

_VALID_PAYLOAD = {
    "summary": "Enterprise revenue dropped 59% in June 2026.",
    "primary_cause": "Enterprise order volume collapsed to 45% of May levels.",
    "findings": [
        {
            "title": "Enterprise collapse",
            "finding_type": "fact",
            "description": "Enterprise orders fell 59% MoM.",
            "supporting_step_ids": ["by_segment"],
            "confidence": 0.95,
            "magnitude": "-59% enterprise volume",
        }
    ],
    "conclusion": "The primary driver was enterprise customers reducing orders.",
    "data_quality_warnings": [],
    "unanswered_questions": ["Why did enterprise customers reduce orders?"],
    "overall_confidence": 0.85,
}


def test_generates_insight_from_valid_payload():
    gen = InsightGenerator(client=_mock_client(_VALID_PAYLOAD))
    report = gen.generate(_interp(), [_step()])
    assert report.summary != ""
    assert len(report.findings) == 1
    assert report.findings[0].title == "Enterprise collapse"
    assert report.overall_confidence == 0.85


def test_finding_fields_populated():
    gen = InsightGenerator(client=_mock_client(_VALID_PAYLOAD))
    report = gen.generate(_interp(), [_step()])
    f = report.findings[0]
    assert f.finding_type == "fact"
    assert f.confidence == 0.95
    assert f.magnitude == "-59% enterprise volume"
    assert "by_segment" in f.supporting_step_ids


def test_no_succeeded_steps_returns_no_data_report():
    gen = InsightGenerator(client=_mock_client(_VALID_PAYLOAD))
    report = gen.generate(_interp(), [_step(status="failed")])
    assert report.overall_confidence == 0.0
    assert "failed" in report.conclusion.lower() or "no" in report.conclusion.lower()


def test_malformed_json_returns_parse_error_report():
    class BadClient:
        def create_message(self, **kw):
            return _MockResp(content=[_Block("not json at all")])
    gen = InsightGenerator(client=BadClient())
    report = gen.generate(_interp(), [_step()])
    assert report.overall_confidence == 0.0
    assert len(report.findings) == 1


def test_unanswered_questions_preserved():
    gen = InsightGenerator(client=_mock_client(_VALID_PAYLOAD))
    report = gen.generate(_interp(), [_step()])
    assert len(report.unanswered_questions) == 1
    assert "enterprise" in report.unanswered_questions[0].lower()


def test_investigated_at_set():
    gen = InsightGenerator(client=_mock_client(_VALID_PAYLOAD))
    report = gen.generate(_interp(), [_step()])
    assert report.investigated_at is not None

"""Tests for InvestigationPlanner -- pure, no LLM, no database."""

from datetime import date
import pytest
from app.agents.investigation_planner import InvestigationPlanner, InvestigationStep
from app.schemas.investigation import QuestionInterpretation, TimePeriod


def _interp(
    question_type="root_cause",
    metric="net_revenue",
    dimensions=None,
    has_comparison=True,
):
    return QuestionInterpretation(
        original_question="Why did revenue drop last month?",
        question_type=question_type,
        primary_metric_key=metric,
        metric_label="Net Revenue",
        metric_definition="Gross revenue minus refunds and discounts.",
        period=TimePeriod(label="June 2026", start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)),
        comparison_period=TimePeriod(label="May 2026", start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        if has_comparison else None,
        dimensions=dimensions if dimensions is not None else ["region", "segment", "category"],
        needs_root_cause_investigation=question_type == "root_cause",
        interpretation_notes="",
        confidence=0.9,
    )


planner = InvestigationPlanner()


def test_root_cause_plan_has_overall_step():
    plan = planner.build_plan(_interp(), session_id="s1")
    assert plan.step_by_id("overall_metric") is not None


def test_root_cause_plan_has_dimension_steps():
    plan = planner.build_plan(_interp(dimensions=["region", "segment"]), session_id="s1")
    assert plan.step_by_id("by_region") is not None
    assert plan.step_by_id("by_segment") is not None


def test_root_cause_net_revenue_includes_refund_check():
    plan = planner.build_plan(_interp(metric="net_revenue"), session_id="s1")
    assert plan.step_by_id("refund_check") is not None


def test_root_cause_gross_revenue_no_refund_check():
    plan = planner.build_plan(_interp(metric="gross_revenue"), session_id="s1")
    assert plan.step_by_id("refund_check") is None


def test_root_cause_always_has_order_volume_check():
    plan = planner.build_plan(_interp(), session_id="s1")
    assert plan.step_by_id("order_volume_check") is not None


def test_trend_plan_has_monthly_step():
    plan = planner.build_plan(_interp(question_type="trend"), session_id="s1")
    assert plan.step_by_id("monthly_trend") is not None


def test_lookup_plan_has_single_step():
    plan = planner.build_plan(_interp(question_type="lookup", has_comparison=False), session_id="s1")
    assert len(plan.steps) == 1
    assert plan.step_by_id("lookup") is not None


def test_all_steps_start_pending():
    plan = planner.build_plan(_interp(), session_id="s1")
    assert all(s.status == "pending" for s in plan.steps)
    assert plan.pending_steps == plan.steps


def test_step_descriptions_mention_metric():
    plan = planner.build_plan(_interp(), session_id="s1")
    for step in plan.steps:
        assert any(word in step.description.lower() for word in ("net_revenue", "revenue", "order", "aov"))


def test_step_descriptions_mention_period():
    plan = planner.build_plan(_interp(), session_id="s1")
    overall = plan.step_by_id("overall_metric")
    assert "June 2026" in overall.description
    assert "May 2026" in overall.description


def test_priority_ordering():
    plan = planner.build_plan(_interp(), session_id="s1")
    priorities = [s.priority for s in plan.steps]
    assert priorities == sorted(priorities)


def test_comparison_plan_has_single_step():
    plan = planner.build_plan(_interp(question_type="comparison"), session_id="s1")
    assert plan.step_by_id("comparison") is not None

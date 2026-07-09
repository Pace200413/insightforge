"""Tests for the evaluation scorer -- pure, synthetic responses, no DB/LLM."""

from datetime import date
from app.evaluation.scorer import (
    BenchmarkCase, score_case, aggregate_scores, PASS_THRESHOLD,
)
from app.schemas.investigation import (
    InvestigationResponse, QuestionInterpretation, TimePeriod,
    InsightReport, Finding,
)


def _case(**over):
    d = dict(
        id="revenue_drop", question="Why did revenue drop last month?",
        reference_date="2026-07-01", expected_question_type="root_cause",
        expected_metric="net_revenue", expected_primary_keywords=["enterprise"],
        expected_secondary_keywords=["emea", "refund", "electronics"],
        min_secondary_hits=1, notes="",
    )
    d.update(over)
    return BenchmarkCase(**d)


def _response(primary_cause="", conclusion="", summary="", q_type="root_cause",
              metric="net_revenue", blocked=0, succeeded=6, failed=0, confidence=0.9):
    interp = QuestionInterpretation(
        original_question="Why did revenue drop last month?",
        question_type=q_type, primary_metric_key=metric,
        metric_label="Net Revenue", metric_definition="...",
        period=TimePeriod(label="June 2026", start_date=date(2026,6,1), end_date=date(2026,6,30)),
        comparison_period=None, dimensions=["segment"],
        needs_root_cause_investigation=True, interpretation_notes="", confidence=0.9,
    )
    insight = InsightReport(
        summary=summary, findings=[], primary_cause=primary_cause,
        conclusion=conclusion, data_quality_warnings=[], unanswered_questions=[],
        overall_confidence=confidence,
    ) if confidence > 0 else None
    return InvestigationResponse(
        session_id="s1", question="Why did revenue drop last month?",
        interpretation=interp, step_results=[], insight=insight,
        audit_summary={"blocked": blocked, "succeeded": succeeded, "failed": failed},
        total_ms=30000, steps_succeeded=succeeded, steps_failed=failed,
    )


def test_perfect_response_passes():
    case = _case()
    resp = _response(
        primary_cause="Enterprise segment revenue collapsed in June.",
        conclusion="The EMEA region and electronics refunds also contributed.",
    )
    score = score_case(case, resp)
    assert score.passed
    assert score.primary_cause_score == 1.0
    assert "enterprise" in score.matched_primary


def test_wrong_cause_fails_primary():
    case = _case()
    resp = _response(primary_cause="Consumer spending dropped.", conclusion="Nothing else.")
    score = score_case(case, resp)
    assert score.primary_cause_score == 0.0
    assert "enterprise" not in score.matched_primary


def test_wrong_interpretation_lowers_score():
    case = _case()
    resp = _response(
        primary_cause="Enterprise collapsed.", conclusion="EMEA too.",
        q_type="lookup", metric="gross_revenue",
    )
    score = score_case(case, resp)
    assert score.interpretation_score == 0.0


def test_correct_type_wrong_metric_half_interpretation():
    case = _case()
    resp = _response(
        primary_cause="Enterprise collapsed.", conclusion="EMEA.",
        q_type="root_cause", metric="gross_revenue",
    )
    score = score_case(case, resp)
    assert score.interpretation_score == 0.5


def test_blocked_query_lowers_safety():
    case = _case()
    resp = _response(primary_cause="Enterprise.", conclusion="EMEA.", blocked=2)
    score = score_case(case, resp)
    assert score.safety_score < 1.0


def test_no_insight_zero_completion():
    case = _case()
    resp = _response(confidence=0.0, succeeded=0)
    score = score_case(case, resp)
    assert score.completion_score == 0.0


def test_lookup_no_primary_keywords_gets_full_primary_score():
    case = _case(expected_primary_keywords=[])
    resp = _response(primary_cause="May revenue was $33M.", conclusion="May.")
    score = score_case(case, resp)
    assert score.primary_cause_score == 1.0


def test_evidence_score_counts_secondary():
    case = _case(min_secondary_hits=2)
    resp = _response(
        primary_cause="Enterprise.",
        conclusion="EMEA region and electronics refunds.",
    )
    score = score_case(case, resp)
    assert score.evidence_score == 1.0   # emea, refund, electronics all present


def test_aggregate_scores():
    case = _case()
    good = score_case(case, _response(primary_cause="Enterprise.", conclusion="EMEA refund electronics."))
    bad = score_case(case, _response(primary_cause="Nothing.", conclusion="Nothing.", confidence=0.0, succeeded=0))
    agg = aggregate_scores([good, bad])
    assert agg["cases"] == 2
    assert 0 <= agg["pass_rate"] <= 1
    assert "mean_overall" in agg


def test_empty_aggregate():
    agg = aggregate_scores([])
    assert agg["cases"] == 0
    assert agg["pass_rate"] == 0.0


def test_case_score_to_dict():
    case = _case()
    score = score_case(case, _response(primary_cause="Enterprise.", conclusion="EMEA."))
    d = score.to_dict()
    assert "overall_score" in d
    assert "scores" in d
    assert d["case_id"] == "revenue_drop"

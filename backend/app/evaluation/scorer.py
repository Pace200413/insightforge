"""Evaluation scorer.

Pure functions (no DB, no LLM) that score an InvestigationResponse against a
benchmark case's expected answer. Separating scoring from running makes the
scoring logic fully unit-testable with synthetic responses.

Score components (each 0.0-1.0):
  interpretation_score : correct question_type + metric
  primary_cause_score  : expected primary keyword(s) in primary_cause/conclusion
  evidence_score       : enough expected secondary keywords appear
  safety_score         : no queries wrongly blocked
  completion_score     : pipeline finished with an insight

overall_score = weighted average.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.investigation import InvestigationResponse


@dataclass
class BenchmarkCase:
    id: str
    question: str
    reference_date: str
    expected_question_type: str
    expected_metric: str
    expected_primary_keywords: list[str]
    expected_secondary_keywords: list[str]
    min_secondary_hits: int
    notes: str = ""


@dataclass
class CaseScore:
    case_id: str
    question: str
    interpretation_score: float
    primary_cause_score: float
    evidence_score: float
    safety_score: float
    completion_score: float
    overall_score: float
    # Diagnostic detail
    matched_primary: list[str] = field(default_factory=list)
    matched_secondary: list[str] = field(default_factory=list)
    steps_succeeded: int = 0
    steps_failed: int = 0
    queries_blocked: int = 0
    total_ms: int = 0
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "overall_score": round(self.overall_score, 3),
            "passed": self.passed,
            "scores": {
                "interpretation": round(self.interpretation_score, 3),
                "primary_cause": round(self.primary_cause_score, 3),
                "evidence": round(self.evidence_score, 3),
                "safety": round(self.safety_score, 3),
                "completion": round(self.completion_score, 3),
            },
            "matched_primary": self.matched_primary,
            "matched_secondary": self.matched_secondary,
            "steps_succeeded": self.steps_succeeded,
            "steps_failed": self.steps_failed,
            "queries_blocked": self.queries_blocked,
            "total_ms": self.total_ms,
        }


# Weights for the overall score
_WEIGHTS = {
    "interpretation": 0.20,
    "primary_cause": 0.35,   # most important: did it find the right cause?
    "evidence": 0.20,
    "safety": 0.15,
    "completion": 0.10,
}

PASS_THRESHOLD = 0.6


def _insight_text(response: InvestigationResponse) -> str:
    """Concatenate all insight text for keyword matching."""
    if not response.insight:
        return ""
    ins = response.insight
    parts = [ins.summary, ins.primary_cause, ins.conclusion]
    for f in ins.findings:
        parts += [f.title, f.description, f.magnitude or ""]
    return " ".join(parts).lower()


def score_case(case: BenchmarkCase, response: InvestigationResponse) -> CaseScore:
    text = _insight_text(response)

    # ── interpretation ────────────────────────────────────────────────
    interp = response.interpretation
    type_ok = interp.question_type == case.expected_question_type
    metric_ok = interp.primary_metric_key == case.expected_metric
    interpretation_score = (0.5 * type_ok) + (0.5 * metric_ok)

    # ── primary cause ─────────────────────────────────────────────────
    matched_primary = [kw for kw in case.expected_primary_keywords if kw.lower() in text]
    if not case.expected_primary_keywords:
        primary_cause_score = 1.0   # lookup questions have no primary cause
    else:
        primary_cause_score = len(matched_primary) / len(case.expected_primary_keywords)

    # ── evidence (secondary keywords) ─────────────────────────────────
    matched_secondary = [kw for kw in case.expected_secondary_keywords if kw.lower() in text]
    if case.min_secondary_hits <= 0:
        evidence_score = 1.0
    else:
        evidence_score = min(1.0, len(matched_secondary) / case.min_secondary_hits)

    # ── safety (no wrongful blocks) ───────────────────────────────────
    blocked = response.audit_summary.get("blocked", 0)
    # In this benchmark, no legitimate query should be blocked.
    safety_score = 1.0 if blocked == 0 else max(0.0, 1.0 - 0.25 * blocked)

    # ── completion ────────────────────────────────────────────────────
    has_insight = response.insight is not None and response.insight.overall_confidence > 0
    some_data = response.steps_succeeded > 0
    completion_score = (0.5 * has_insight) + (0.5 * some_data)

    overall = (
        _WEIGHTS["interpretation"] * interpretation_score
        + _WEIGHTS["primary_cause"] * primary_cause_score
        + _WEIGHTS["evidence"] * evidence_score
        + _WEIGHTS["safety"] * safety_score
        + _WEIGHTS["completion"] * completion_score
    )

    return CaseScore(
        case_id=case.id,
        question=case.question,
        interpretation_score=interpretation_score,
        primary_cause_score=primary_cause_score,
        evidence_score=evidence_score,
        safety_score=safety_score,
        completion_score=completion_score,
        overall_score=overall,
        matched_primary=matched_primary,
        matched_secondary=matched_secondary,
        steps_succeeded=response.steps_succeeded,
        steps_failed=response.steps_failed,
        queries_blocked=blocked,
        total_ms=response.total_ms,
        passed=overall >= PASS_THRESHOLD,
    )


def aggregate_scores(scores: list[CaseScore]) -> dict:
    if not scores:
        return {"cases": 0, "passed": 0, "pass_rate": 0.0, "mean_overall": 0.0}
    return {
        "cases": len(scores),
        "passed": sum(1 for s in scores if s.passed),
        "pass_rate": round(sum(1 for s in scores if s.passed) / len(scores), 3),
        "mean_overall": round(sum(s.overall_score for s in scores) / len(scores), 3),
        "mean_interpretation": round(sum(s.interpretation_score for s in scores) / len(scores), 3),
        "mean_primary_cause": round(sum(s.primary_cause_score for s in scores) / len(scores), 3),
        "mean_evidence": round(sum(s.evidence_score for s in scores) / len(scores), 3),
        "mean_safety": round(sum(s.safety_score for s in scores) / len(scores), 3),
        "total_steps_succeeded": sum(s.steps_succeeded for s in scores),
        "total_steps_failed": sum(s.steps_failed for s in scores),
        "mean_latency_ms": round(sum(s.total_ms for s in scores) / len(scores)),
    }

"""Evaluation runner.

Loads the benchmark, runs each question through the full investigation
pipeline, scores the result, and produces a scorecard.

Run as a script (server does NOT need to be running, but the DB does):
    make eval
    # or:
    PYTHONPATH=backend .venv/bin/python -m app.evaluation.runner

Prints a formatted scorecard and writes docs/eval_results.json for the README.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from app.evaluation.scorer import (
    BenchmarkCase,
    CaseScore,
    aggregate_scores,
    score_case,
)

_BENCHMARK_PATH = Path(__file__).resolve().parent / "benchmark.yaml"


def load_benchmark() -> list[BenchmarkCase]:
    raw = yaml.safe_load(_BENCHMARK_PATH.read_text())
    return [
        BenchmarkCase(
            id=c["id"],
            question=c["question"],
            reference_date=c["reference_date"],
            expected_question_type=c["expected_question_type"],
            expected_metric=c["expected_metric"],
            expected_primary_keywords=c.get("expected_primary_keywords", []),
            expected_secondary_keywords=c.get("expected_secondary_keywords", []),
            min_secondary_hits=c.get("min_secondary_hits", 1),
            notes=c.get("notes", ""),
        )
        for c in raw["benchmark"]
    ]


async def run_evaluation(engine) -> tuple[list[CaseScore], dict]:
    """Run all benchmark cases through the pipeline and score them."""
    from app.services.orchestrator import InvestigationOrchestrator

    cases = load_benchmark()
    orchestrator = InvestigationOrchestrator(engine=engine)
    scores: list[CaseScore] = []

    for case in cases:
        print(f"\n▶ Running: {case.id} -- \"{case.question}\"")
        ref = date.fromisoformat(case.reference_date)
        try:
            response = await orchestrator.run(case.question, reference_date=ref)
            cs = score_case(case, response)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue
        scores.append(cs)
        mark = "✅ PASS" if cs.passed else "❌ FAIL"
        print(f"  {mark}  overall={cs.overall_score:.2f}  "
              f"(cause={cs.primary_cause_score:.2f} evidence={cs.evidence_score:.2f} "
              f"safety={cs.safety_score:.2f})")
        print(f"  matched primary: {cs.matched_primary or '—'}  "
              f"steps: {cs.steps_succeeded}✓/{cs.steps_failed}✗  {cs.total_ms}ms")

    summary = aggregate_scores(scores)
    return scores, summary


def print_scorecard(scores: list[CaseScore], summary: dict) -> None:
    print("\n" + "=" * 72)
    print("EVALUATION SCORECARD")
    print("=" * 72)
    print(f"  Cases run:          {summary['cases']}")
    print(f"  Passed:             {summary['passed']}/{summary['cases']}  "
          f"({summary['pass_rate']:.0%})")
    print(f"  Mean overall score: {summary['mean_overall']:.2f}")
    print(f"  ─ interpretation:   {summary.get('mean_interpretation', 0):.2f}")
    print(f"  ─ primary cause:    {summary.get('mean_primary_cause', 0):.2f}")
    print(f"  ─ evidence:         {summary.get('mean_evidence', 0):.2f}")
    print(f"  ─ safety:           {summary.get('mean_safety', 0):.2f}")
    print(f"  Steps: {summary.get('total_steps_succeeded', 0)} succeeded, "
          f"{summary.get('total_steps_failed', 0)} failed")
    print(f"  Mean latency:       {summary.get('mean_latency_ms', 0):,}ms")
    print("=" * 72)


async def _main() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.core.database import get_engine, dispose_engine

    engine = get_engine()
    scores, summary = await run_evaluation(engine)
    print_scorecard(scores, summary)

    # Write results for the README / portfolio
    out = Path(__file__).resolve().parents[3] / "docs" / "eval_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "summary": summary,
        "cases": [s.to_dict() for s in scores],
    }, indent=2))
    print(f"\nResults written to {out}")
    await dispose_engine()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())

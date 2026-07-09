"""Tests for the usage tracker -- pure, no dependencies."""

from app.observability.usage_tracker import (
    LLMCall, UsageTracker, estimate_tokens,
    DEFAULT_INPUT_COST_PER_MTOK, DEFAULT_OUTPUT_COST_PER_MTOK,
)


def test_estimate_tokens_roughly_chars_over_4():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1   # minimum 1


def test_llm_call_cost_calculation():
    call = LLMCall(
        session_id="s1", agent="interpreter",
        input_tokens=1_000_000, output_tokens=1_000_000, latency_ms=100,
    )
    expected = DEFAULT_INPUT_COST_PER_MTOK + DEFAULT_OUTPUT_COST_PER_MTOK
    assert abs(call.cost_usd - expected) < 1e-9


def test_tracker_records_and_summarises():
    tracker = UsageTracker()
    tracker.record(session_id="s1", agent="interpreter",
                   input_tokens=100, output_tokens=50, latency_ms=200)
    tracker.record(session_id="s1", agent="sql_generator",
                   input_tokens=200, output_tokens=80, latency_ms=300)
    summary = tracker.session_summary("s1")
    assert summary["llm_calls"] == 2
    assert summary["input_tokens"] == 300
    assert summary["output_tokens"] == 130
    assert summary["total_tokens"] == 430
    assert summary["total_latency_ms"] == 500


def test_tracker_by_agent_breakdown():
    tracker = UsageTracker()
    tracker.record(session_id="s1", agent="sql_generator",
                   input_tokens=100, output_tokens=50, latency_ms=100)
    tracker.record(session_id="s1", agent="sql_generator",
                   input_tokens=100, output_tokens=50, latency_ms=100)
    tracker.record(session_id="s1", agent="insight",
                   input_tokens=500, output_tokens=200, latency_ms=400)
    summary = tracker.session_summary("s1")
    assert summary["by_agent"]["sql_generator"]["calls"] == 2
    assert summary["by_agent"]["insight"]["calls"] == 1


def test_tracker_isolates_sessions():
    tracker = UsageTracker()
    tracker.record(session_id="s1", agent="interpreter",
                   input_tokens=100, output_tokens=50, latency_ms=100)
    tracker.record(session_id="s2", agent="interpreter",
                   input_tokens=999, output_tokens=999, latency_ms=100)
    assert tracker.session_summary("s1")["input_tokens"] == 100
    assert tracker.session_summary("s2")["input_tokens"] == 999


def test_empty_session_summary():
    tracker = UsageTracker()
    summary = tracker.session_summary("nonexistent")
    assert summary["llm_calls"] == 0
    assert summary["estimated_cost_usd"] == 0.0


def test_global_summary_aggregates_all_sessions():
    tracker = UsageTracker()
    tracker.record(session_id="s1", agent="interpreter",
                   input_tokens=100, output_tokens=50, latency_ms=100)
    tracker.record(session_id="s2", agent="interpreter",
                   input_tokens=100, output_tokens=50, latency_ms=100)
    g = tracker.global_summary()
    assert g["llm_calls"] == 2
    assert g["input_tokens"] == 200


def test_cost_is_positive_for_real_usage():
    tracker = UsageTracker()
    tracker.record(session_id="s1", agent="insight",
                   input_tokens=5000, output_tokens=2000, latency_ms=800)
    assert tracker.session_summary("s1")["estimated_cost_usd"] > 0

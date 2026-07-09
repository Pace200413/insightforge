"""Token and cost estimation for LLM calls.

Tracks per-session LLM usage so the observability endpoint can report
estimated cost per investigation. Since Groq's free tier and Anthropic have
different pricing, we estimate tokens (chars/4 heuristic when the provider
doesn't return usage) and apply a configurable rate.

This is intentionally an *estimate* -- the point is to demonstrate cost
awareness in an AI system, not to bill anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

# Approximate blended $/1M tokens. Groq free tier = 0; these are Anthropic
# Sonnet-class defaults so the number is meaningful if you switch providers.
DEFAULT_INPUT_COST_PER_MTOK = 3.00
DEFAULT_OUTPUT_COST_PER_MTOK = 15.00


@dataclass
class LLMCall:
    session_id: str
    agent: str                 # "interpreter" | "sql_generator" | "repair" | "insight"
    input_tokens: int
    output_tokens: int
    latency_ms: int

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens / 1_000_000 * DEFAULT_INPUT_COST_PER_MTOK
            + self.output_tokens / 1_000_000 * DEFAULT_OUTPUT_COST_PER_MTOK
        )


def estimate_tokens(text: str) -> int:
    """Rough heuristic: ~4 characters per token."""
    return max(1, len(text) // 4)


class UsageTracker:
    """Process-level LLM usage tracker."""

    def __init__(self) -> None:
        self._calls: list[LLMCall] = []
        self._lock = Lock()

    def record(
        self,
        *,
        session_id: str,
        agent: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> LLMCall:
        call = LLMCall(
            session_id=session_id,
            agent=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )
        with self._lock:
            self._calls.append(call)
        return call

    def session_calls(self, session_id: str) -> list[LLMCall]:
        return [c for c in self._calls if c.session_id == session_id]

    def session_summary(self, session_id: str) -> dict:
        calls = self.session_calls(session_id)
        return self._summarise(calls)

    def global_summary(self) -> dict:
        return self._summarise(self._calls)

    @staticmethod
    def _summarise(calls: list[LLMCall]) -> dict:
        if not calls:
            return {
                "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "estimated_cost_usd": 0.0,
                "total_latency_ms": 0, "by_agent": {},
            }
        by_agent: dict[str, dict] = {}
        for c in calls:
            a = by_agent.setdefault(
                c.agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
            )
            a["calls"] += 1
            a["input_tokens"] += c.input_tokens
            a["output_tokens"] += c.output_tokens
            a["latency_ms"] += c.latency_ms
        return {
            "llm_calls": len(calls),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "total_tokens": sum(c.input_tokens + c.output_tokens for c in calls),
            "estimated_cost_usd": round(sum(c.cost_usd for c in calls), 6),
            "total_latency_ms": sum(c.latency_ms for c in calls),
            "by_agent": by_agent,
        }


# Process-level singleton
usage_tracker = UsageTracker()

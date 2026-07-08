"""Pydantic models for the investigation pipeline.

These are the shared data contracts between every stage:
  QuestionInterpretation  -- output of Stage 3 (question interpreter)
  InvestigationPlan       -- output of Stage 4 (planner)  [placeholder]
  QueryResult             -- output of Stage 5 (executor)  [placeholder]
  InvestigationState      -- the full stateful object passed between agents
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class TimePeriod(BaseModel):
    label: str             # human-readable: "June 2026", "last month"
    start_date: date
    end_date: date


class QuestionInterpretation(BaseModel):
    original_question: str
    question_type: Literal["root_cause", "trend", "comparison", "lookup"]
    primary_metric_key: str              # must match a key in metrics.yaml
    metric_label: str                    # human label from metrics.yaml
    metric_definition: str               # the approved definition text
    period: TimePeriod                   # the period being asked about
    comparison_period: TimePeriod | None # the baseline to compare against
    dimensions: list[str] = Field(       # which dimensions to investigate
        description="e.g. ['region', 'segment', 'category']"
    )
    needs_root_cause_investigation: bool
    interpretation_notes: str            # ambiguities / assumptions
    confidence: float = Field(ge=0.0, le=1.0)


class InvestigationState(BaseModel):
    """Stateful envelope passed between every agent in the pipeline."""
    session_id: str
    question: str
    interpretation: QuestionInterpretation | None = None
    # Stages 4-7 will add: plan, relevant_tables, queries, results, insights
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage: str = "received"              # tracks where we are in the pipeline
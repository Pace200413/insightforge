"""Pydantic models shared across the investigation pipeline."""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

class TimePeriod(BaseModel):
    label: str
    start_date: date
    end_date: date

class QuestionInterpretation(BaseModel):
    original_question: str
    question_type: Literal["root_cause", "trend", "comparison", "lookup"]
    primary_metric_key: str
    metric_label: str
    metric_definition: str
    period: TimePeriod
    comparison_period: TimePeriod | None
    dimensions: list[str] = Field(description="e.g. ['region', 'segment', 'category']")
    needs_root_cause_investigation: bool
    interpretation_notes: str
    confidence: float = Field(ge=0.0, le=1.0)

class InvestigationState(BaseModel):
    session_id: str
    question: str
    interpretation: QuestionInterpretation | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage: str = "received"

class ValidationWarning(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"

class StepResult(BaseModel):
    step_id: str
    description: str
    priority: int
    status: str
    sql: str | None = None
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    execution_ms: int = 0
    repair_attempts: int = 0
    validation_warnings: list[ValidationWarning] = Field(default_factory=list)
    error: str | None = None
    tables_referenced: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status in ("succeeded", "repaired")

class Finding(BaseModel):
    title: str
    finding_type: Literal["fact", "hypothesis", "data_quality", "warning"]
    description: str
    supporting_step_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    magnitude: str | None = None

class InsightReport(BaseModel):
    summary: str
    findings: list[Finding]
    primary_cause: str
    conclusion: str
    data_quality_warnings: list[str]
    unanswered_questions: list[str]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    investigated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

class InvestigationResponse(BaseModel):
    session_id: str
    question: str
    interpretation: QuestionInterpretation
    step_results: list[StepResult]
    insight: InsightReport | None = None
    audit_summary: dict = Field(default_factory=dict)
    total_ms: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0

"""Investigation planner.

Converts a QuestionInterpretation into a list of InvestigationSteps. Each
step has a clear description of what to calculate; the SQL generator in the
next stage turns each description into a runnable query.

The planner is intentionally rule-based, not LLM-based. This is deliberate:
  - Fast (no LLM call = no latency or cost).
  - Deterministic (same question always produces the same plan).
  - Easily unit-tested.
  - The LLM's job is to write SQL, not to decide which steps to run.

For root_cause questions the plan always includes:
  Step 1 - Overall metric: current period vs comparison period.
  Step N - One step per requested dimension (region, segment, category, ...).
  Step N+1 - Refund check (if net_revenue is the metric).
  Step N+2 - Order volume check (orders are often the root cause of revenue Δ).

For trend questions: monthly timeseries of the metric.
For comparison: metric broken down by the first requested dimension.
For lookup: single metric value for the period.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.investigation import QuestionInterpretation


@dataclass
class InvestigationStep:
    step_id: str               # slug, e.g. "overall_metric"
    description: str           # what this query should calculate (plain English)
    priority: int              # lower = run first
    # Set by the SQL generator and executor later:
    sql: str | None = None
    status: str = "pending"    # pending | running | succeeded | failed | repaired | skipped
    error: str | None = None
    repair_attempts: int = 0
    rows_returned: int | None = None
    execution_ms: int | None = None
    result_summary: str | None = None   # 1-line human summary added after execution


@dataclass
class InvestigationPlan:
    session_id: str
    question_type: str
    primary_metric_key: str
    period_label: str
    comparison_period_label: str | None
    steps: list[InvestigationStep] = field(default_factory=list)

    @property
    def pending_steps(self) -> list[InvestigationStep]:
        return [s for s in self.steps if s.status == "pending"]

    @property
    def succeeded_steps(self) -> list[InvestigationStep]:
        return [s for s in self.steps if s.status in ("succeeded", "repaired")]

    def step_by_id(self, step_id: str) -> InvestigationStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)


# Dimension -> human-readable description fragment
_DIM_DESCRIPTIONS: dict[str, str] = {
    "region":          "break down by geographic region (NA, EMEA, APAC, LATAM)",
    "segment":         "break down by customer segment (consumer, smb, enterprise)",
    "category":        "break down by product category (Electronics, Apparel, etc.)",
    "product":         "break down by top individual products",
    "campaign":        "break down by marketing campaign attribution",
    "payment_method":  "break down by payment method (card, paypal, bank_transfer)",
}


class InvestigationPlanner:

    def build_plan(
        self,
        interpretation: QuestionInterpretation,
        session_id: str,
    ) -> InvestigationPlan:
        plan = InvestigationPlan(
            session_id=session_id,
            question_type=interpretation.question_type,
            primary_metric_key=interpretation.primary_metric_key,
            period_label=interpretation.period.label,
            comparison_period_label=(
                interpretation.comparison_period.label
                if interpretation.comparison_period else None
            ),
        )

        builder = {
            "root_cause":  self._root_cause_steps,
            "trend":       self._trend_steps,
            "comparison":  self._comparison_steps,
            "lookup":      self._lookup_steps,
        }.get(interpretation.question_type, self._lookup_steps)

        builder(plan, interpretation)
        return plan

    # ------------------------------------------------------------------
    # Plan builders by question type
    # ------------------------------------------------------------------

    def _root_cause_steps(
        self, plan: InvestigationPlan, interp: QuestionInterpretation
    ) -> None:
        period = interp.period
        comp = interp.comparison_period
        metric = interp.primary_metric_key
        comp_label = comp.label if comp else "prior period"

        plan.steps.append(InvestigationStep(
            step_id="overall_metric",
            priority=1,
            description=(
                f"Calculate the total {metric} for {period.label} "
                f"({period.start_date} to {period.end_date}) "
                f"and compare it to {comp_label} "
                f"({'%s to %s' % (comp.start_date, comp.end_date) if comp else 'N/A'}). "
                f"IMPORTANT: use order_items.unit_price (not products.price) for revenue. "
                f"Filter to orders.status = 'completed'."
            ),
        ))

        for i, dim in enumerate(interp.dimensions, start=2):
            dim_desc = _DIM_DESCRIPTIONS.get(dim, f"break down by {dim}")
            plan.steps.append(InvestigationStep(
                step_id=f"by_{dim}",
                priority=i,
                description=(
                    f"Calculate {metric} for {period.label} vs {comp_label}, "
                    f"{dim_desc}. "
                    f"Show both periods side by side so we can identify which "
                    f"{dim} contributed most to the change. "
                    f"Order by absolute change descending."
                ),
            ))

        # Always check refunds when investigating net_revenue
        if metric == "net_revenue":
            plan.steps.append(InvestigationStep(
                step_id="refund_check",
                priority=len(interp.dimensions) + 2,
                description=(
                    f"Calculate total refund amount and refund rate (refunds / gross revenue) "
                    f"for {period.label} vs {comp_label}, broken down by product category. "
                    f"This checks whether a refund spike is distorting net revenue."
                ),
            ))

        # Order volume check: revenue can drop because of volume OR AOV
        plan.steps.append(InvestigationStep(
            step_id="order_volume_check",
            priority=len(interp.dimensions) + 3,
            description=(
                f"Calculate completed order count and average order value (AOV) "
                f"for {period.label} vs {comp_label}. "
                f"This separates a volume decline from an AOV decline."
            ),
        ))

    def _trend_steps(
        self, plan: InvestigationPlan, interp: QuestionInterpretation
    ) -> None:
        period = interp.period
        metric = interp.primary_metric_key
        plan.steps.append(InvestigationStep(
            step_id="monthly_trend",
            priority=1,
            description=(
                f"Calculate monthly {metric} from {period.start_date} to {period.end_date}. "
                f"Group by month (date_trunc('month', order_date)), ordered chronologically. "
                f"Use order_items.unit_price for revenue. Filter to completed orders."
            ),
        ))
        for i, dim in enumerate(interp.dimensions[:2], start=2):
            dim_desc = _DIM_DESCRIPTIONS.get(dim, f"by {dim}")
            plan.steps.append(InvestigationStep(
                step_id=f"trend_by_{dim}",
                priority=i,
                description=(
                    f"Calculate monthly {metric} {dim_desc}, "
                    f"from {period.start_date} to {period.end_date}. "
                    f"Show each {dim} as a separate column or row for comparison."
                ),
            ))

    def _comparison_steps(
        self, plan: InvestigationPlan, interp: QuestionInterpretation
    ) -> None:
        period = interp.period
        metric = interp.primary_metric_key
        dim = interp.dimensions[0] if interp.dimensions else "segment"
        dim_desc = _DIM_DESCRIPTIONS.get(dim, f"by {dim}")
        plan.steps.append(InvestigationStep(
            step_id="comparison",
            priority=1,
            description=(
                f"Calculate {metric} {dim_desc} for {period.label} "
                f"({period.start_date} to {period.end_date}). "
                f"Order by {metric} descending."
            ),
        ))

    def _lookup_steps(
        self, plan: InvestigationPlan, interp: QuestionInterpretation
    ) -> None:
        period = interp.period
        plan.steps.append(InvestigationStep(
            step_id="lookup",
            priority=1,
            description=(
                f"Calculate the total {interp.primary_metric_key} "
                f"for {period.label} ({period.start_date} to {period.end_date}). "
                f"Single aggregated value."
            ),
        ))

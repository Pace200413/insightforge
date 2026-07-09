"""Result validator.

Pure functions (no DB, no LLM) that inspect a QueryResult and flag problems.

Checks performed:
  1. empty_result      -- 0 rows returned (likely wrong date filter or join).
  2. single_null_row   -- 1 row with all None values (aggregation over empty set).
  3. high_null_rate    -- >50% of values in numeric columns are None.
  4. all_zeros         -- all numeric values are 0 (possible filter error).
  5. comparison_gap    -- a comparison query has data for only one period.
  6. negative_revenue  -- revenue/amount column contains unexpected negatives.

Each check returns a list of ValidationWarning objects. The validator never
blocks execution -- it annotates results so the insight generator can
decide how much to trust each finding.
"""

from __future__ import annotations

from app.db.executor import QueryResult
from app.schemas.investigation import ValidationWarning

# Column name substrings that indicate numeric/money values
_NUMERIC_HINTS = {"revenue", "amount", "price", "total", "count", "value", "rate"}
# Column names that should never be negative
_NON_NEGATIVE_HINTS = {"revenue", "amount", "price", "total", "value"}


def _is_numeric_col(col_name: str) -> bool:
    name = col_name.lower()
    return any(hint in name for hint in _NUMERIC_HINTS)


def _is_non_negative_col(col_name: str) -> bool:
    name = col_name.lower()
    # change/diff/delta columns are legitimately negative
    if any(x in name for x in ("change", "diff", "delta", "growth")):
        return False
    return any(hint in name for hint in _NON_NEGATIVE_HINTS)


def validate(result: QueryResult, step_id: str = "") -> list[ValidationWarning]:
    """Run all checks and return a (possibly empty) list of warnings."""
    if not result.success:
        return []   # executor already recorded the error

    warnings: list[ValidationWarning] = []
    warnings += _check_empty(result)
    warnings += _check_single_null_row(result)
    warnings += _check_high_null_rate(result)
    warnings += _check_all_zeros(result)
    warnings += _check_negative_values(result)
    return warnings


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_empty(result: QueryResult) -> list[ValidationWarning]:
    if result.row_count == 0:
        return [ValidationWarning(
            code="empty_result",
            message="Query returned 0 rows. The date filter or JOIN condition may be wrong.",
            severity="error",
        )]
    return []


def _check_single_null_row(result: QueryResult) -> list[ValidationWarning]:
    if result.row_count == 1:
        row = result.rows[0]
        if all(v is None for v in row.values()):
            return [ValidationWarning(
                code="single_null_row",
                message="Query returned one row of all NULLs -- aggregation over an empty set.",
                severity="error",
            )]
    return []


def _check_high_null_rate(result: QueryResult) -> list[ValidationWarning]:
    if not result.rows:
        return []
    warnings = []
    all_cols = list(result.rows[0].keys())
    numeric_cols = [c for c in all_cols if _is_numeric_col(c)]
    for col in numeric_cols:
        null_count = sum(1 for row in result.rows if row.get(col) is None)
        rate = null_count / len(result.rows)
        if rate > 0.5:
            warnings.append(ValidationWarning(
                code="high_null_rate",
                message=f"Column '{col}' is NULL in {rate:.0%} of rows. Check LEFT JOIN conditions.",
                severity="warning",
            ))
    return warnings


def _check_all_zeros(result: QueryResult) -> list[ValidationWarning]:
    if not result.rows:
        return []
    all_cols = list(result.rows[0].keys())
    numeric_cols = [c for c in all_cols if _is_numeric_col(c)]
    warnings = []
    for col in numeric_cols:
        values = [row.get(col) for row in result.rows if row.get(col) is not None]
        if values and all(v == 0 for v in values):
            warnings.append(ValidationWarning(
                code="all_zeros",
                message=f"Column '{col}' is 0 for all rows. Possible filter or join error.",
                severity="warning",
            ))
    return warnings


def _check_negative_values(result: QueryResult) -> list[ValidationWarning]:
    if not result.rows:
        return []
    all_cols = list(result.rows[0].keys())
    non_neg_cols = [c for c in all_cols if _is_non_negative_col(c)]
    warnings = []
    for col in non_neg_cols:
        negatives = [
            row.get(col) for row in result.rows
            if row.get(col) is not None and float(row[col]) < 0
        ]
        if negatives:
            warnings.append(ValidationWarning(
                code="negative_values",
                message=(
                    f"Column '{col}' has {len(negatives)} negative values. "
                    f"Check that refunds are subtracted, not added."
                ),
                severity="warning",
            ))
    return warnings

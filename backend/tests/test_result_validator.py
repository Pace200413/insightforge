"""Tests for result_validator -- pure, no database or LLM required."""

from app.db.executor import QueryResult
from app.services import result_validator


def _result(rows: list[dict], success: bool = True) -> QueryResult:
    return QueryResult(
        success=success,
        rows=rows,
        row_count=len(rows),
        sql_executed="SELECT 1",
    )


def test_empty_result_flagged():
    warnings = result_validator.validate(_result([]))
    codes = [w.code for w in warnings]
    assert "empty_result" in codes


def test_single_null_row_flagged():
    warnings = result_validator.validate(_result([{"revenue": None, "count": None}]))
    codes = [w.code for w in warnings]
    assert "single_null_row" in codes


def test_good_result_no_warnings():
    rows = [
        {"segment": "enterprise", "revenue": 1000000},
        {"segment": "consumer",   "revenue":  500000},
    ]
    warnings = result_validator.validate(_result(rows))
    assert warnings == []


def test_high_null_rate_flagged():
    rows = [{"revenue": None} for _ in range(6)] + [{"revenue": 100}] * 4
    warnings = result_validator.validate(_result(rows))
    codes = [w.code for w in warnings]
    assert "high_null_rate" in codes


def test_all_zeros_flagged():
    rows = [{"revenue": 0, "segment": "enterprise"} for _ in range(5)]
    warnings = result_validator.validate(_result(rows))
    codes = [w.code for w in warnings]
    assert "all_zeros" in codes


def test_non_zero_values_not_flagged():
    rows = [{"revenue": 100 * i, "segment": f"s{i}"} for i in range(1, 5)]
    warnings = result_validator.validate(_result(rows))
    codes = [w.code for w in warnings]
    assert "all_zeros" not in codes


def test_negative_revenue_flagged():
    rows = [{"revenue": -500, "segment": "enterprise"}]
    warnings = result_validator.validate(_result(rows))
    codes = [w.code for w in warnings]
    assert "negative_values" in codes


def test_failed_result_skipped():
    bad = QueryResult(success=False, error="DB error", sql_executed="SELECT 1")
    warnings = result_validator.validate(bad)
    assert warnings == []


def test_non_numeric_columns_ignored():
    rows = [{"name": "product_a", "category": "Electronics", "revenue": 1000}]
    warnings = result_validator.validate(_result(rows))
    assert warnings == []


def test_warning_has_severity():
    warnings = result_validator.validate(_result([]))
    assert all(w.severity in ("info", "warning", "error") for w in warnings)

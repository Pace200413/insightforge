"""Unit tests for the synthetic data generator's pure logic.

These run WITHOUT a database -- they verify the manifest parses and the
anomaly math is applied exactly as declared.
"""

import random
from datetime import date
from pathlib import Path

from app.db.datagen import (
    duplicate_payment_fraction,
    generate_reference_data,
    load_manifest,
    price_factor,
    refund_rate,
    seasonality_factor,
    volume_multiplier,
)

MANIFEST = Path(__file__).resolve().parents[2] / "scripts" / "anomalies.yaml"


def _m():
    return load_manifest(MANIFEST)


def test_manifest_loads_with_expected_anomalies():
    m = _m()
    ids = {a["id"] for a in m.anomalies}
    assert ids == {
        "enterprise_volume_drop",
        "emea_slowdown",
        "electronics_refund_spike",
        "apparel_price_increase",
        "duplicate_payments",
        "failed_summer_campaign",
    }
    assert m.start_date == date(2025, 1, 1)
    assert m.end_date == date(2026, 6, 30)


def test_enterprise_drop_applies_only_in_june_2026():
    m = _m()
    in_june = volume_multiplier(date(2026, 6, 15), "enterprise", "NA", m)
    in_may = volume_multiplier(date(2026, 5, 15), "enterprise", "NA", m)
    consumer_june = volume_multiplier(date(2026, 6, 15), "consumer", "NA", m)
    assert in_june == 0.45
    assert in_may == 1.0
    assert consumer_june == 1.0


def test_overlapping_anomalies_multiply():
    """EMEA enterprise in June gets BOTH multipliers."""
    m = _m()
    combined = volume_multiplier(date(2026, 6, 15), "enterprise", "EMEA", m)
    assert abs(combined - 0.45 * 0.75) < 1e-9


def test_apparel_price_factor_active_from_april():
    m = _m()
    assert price_factor(date(2026, 3, 31), "Apparel", m) == 1.0
    assert price_factor(date(2026, 4, 1), "Apparel", m) == 1.15
    assert price_factor(date(2026, 5, 1), "Electronics", m) == 1.0


def test_refund_rate_spikes_for_electronics_in_june():
    m = _m()
    assert refund_rate(date(2026, 6, 10), {"Electronics"}, m) == 0.12
    assert refund_rate(date(2026, 5, 10), {"Electronics"}, m) == 0.025
    assert refund_rate(date(2026, 6, 10), {"Apparel"}, m) == 0.025


def test_duplicate_payments_only_in_may_june_2026():
    m = _m()
    assert duplicate_payment_fraction(date(2026, 5, 15), m) == 0.015
    assert duplicate_payment_fraction(date(2026, 4, 15), m) == 0.0


def test_seasonality_november_higher_than_january():
    m = _m()
    nov = seasonality_factor(date(2025, 11, 3), m)  # a Monday
    jan = seasonality_factor(date(2025, 1, 6), m)   # a Monday
    assert nov > jan


def test_reference_data_shapes_and_determinism():
    m = _m()
    ref1 = generate_reference_data(random.Random(m.random_seed), m)
    ref2 = generate_reference_data(random.Random(m.random_seed), m)
    assert len(ref1["regions"]) == 4
    assert len(ref1["categories"]) == 5
    assert len(ref1["products"]) == 150
    assert len(ref1["customers"]) == 8000
    # Same seed -> identical output (reproducible ground truth)
    assert ref1["customers"][0] == ref2["customers"][0]
    assert ref1["products"][-1] == ref2["products"][-1]
    # Failed campaign from the manifest exists
    names = {c["name"] for c in ref1["marketing_campaigns"]}
    assert "Summer Splash 2026" in names
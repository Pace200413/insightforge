"""Synthetic business-data generator.

Reads the ground-truth manifest (scripts/anomalies.yaml), produces ~18 months
of realistic e-commerce activity, and injects the declared anomalies.

Design principles:
- DETERMINISTIC: seeded RNG, so the dataset (and therefore the ground truth)
  is reproducible on any machine.
- MANIFEST-DRIVEN: the generator contains no hard-coded anomalies; everything
  comes from anomalies.yaml, which doubles as the evaluation answer key.
- PURE CORE: seasonality and anomaly-multiplier functions are side-effect-free
  and unit-tested.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

# ----------------------------------------------------------------------------
# Manifest loading
# ----------------------------------------------------------------------------


@dataclass
class Manifest:
    start_date: date
    end_date: date
    random_seed: int
    base_orders_per_day: int
    anomalies: list[dict[str, Any]]
    monthly_factors: dict[int, float]
    weekday_factors: dict[int, float]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def load_manifest(path: str | Path) -> Manifest:
    raw = yaml.safe_load(Path(path).read_text())
    ds = raw["dataset"]
    season = raw["seasonality"]
    return Manifest(
        start_date=_as_date(ds["start_date"]),
        end_date=_as_date(ds["end_date"]),
        random_seed=int(ds["random_seed"]),
        base_orders_per_day=int(ds["base_orders_per_day"]),
        anomalies=raw["anomalies"],
        monthly_factors={int(k): float(v) for k, v in season["monthly_factors"].items()},
        weekday_factors={int(k): float(v) for k, v in season["weekday_factors"].items()},
        raw=raw,
    )


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _in_period(d: date, anomaly: dict[str, Any]) -> bool:
    p = anomaly["period"]
    return _as_date(p["start"]) <= d <= _as_date(p["end"])


# ----------------------------------------------------------------------------
# Pure functions (unit-tested)
# ----------------------------------------------------------------------------


def seasonality_factor(d: date, manifest: Manifest) -> float:
    """Normal, expected variation: annual seasonality x weekday pattern."""
    return manifest.monthly_factors[d.month] * manifest.weekday_factors[d.weekday()]


def volume_multiplier(d: date, segment: str, region_code: str, manifest: Manifest) -> float:
    """Combined keep-probability from all active `volume` anomalies.

    An order for (segment, region) on date d is kept with this probability.
    Multiple matching anomalies multiply (e.g. EMEA enterprise in June 2026
    gets 0.45 * 0.75).
    """
    m = 1.0
    for a in manifest.anomalies:
        if a["type"] != "volume" or not _in_period(d, a):
            continue
        dims = a.get("dimensions", {})
        if "segment" in dims and dims["segment"] != segment:
            continue
        if "region" in dims and dims["region"] != region_code:
            continue
        m *= float(a["multiplier"])
    return m


def price_factor(d: date, category: str, manifest: Manifest) -> float:
    """Combined price multiplier from active `price_change` anomalies."""
    f = 1.0
    for a in manifest.anomalies:
        if a["type"] != "price_change" or not _in_period(d, a):
            continue
        if a.get("dimensions", {}).get("category") == category:
            f *= float(a["price_factor"])
    return f


def refund_rate(d: date, categories_in_order: set[str], manifest: Manifest) -> float:
    """Refund probability for an order, given the categories it contains."""
    rate = 0.025  # global baseline
    for a in manifest.anomalies:
        if a["type"] != "refund_rate" or not _in_period(d, a):
            continue
        if a.get("dimensions", {}).get("category") in categories_in_order:
            rate = max(rate, float(a["anomaly_rate"]))
    return rate


def duplicate_payment_fraction(d: date, manifest: Manifest) -> float:
    for a in manifest.anomalies:
        if a["type"] == "data_quality" and a["id"] == "duplicate_payments" and _in_period(d, a):
            return float(a["duplicate_fraction"])
    return 0.0


# ----------------------------------------------------------------------------
# Static reference data
# ----------------------------------------------------------------------------

REGIONS = [
    ("NA", "North America", 0.40),
    ("EMEA", "Europe, Middle East & Africa", 0.30),
    ("APAC", "Asia Pacific", 0.20),
    ("LATAM", "Latin America", 0.10),
]

SEGMENTS = [("consumer", 0.70), ("smb", 0.22), ("enterprise", 0.08)]

# Per-order behavior by segment: (order-frequency weight, items range, qty range)
SEGMENT_BEHAVIOR = {
    "consumer": {"weight": 1.0, "items": (1, 3), "qty": (1, 2)},
    "smb": {"weight": 2.5, "items": (2, 5), "qty": (1, 5)},
    "enterprise": {"weight": 8.0, "items": (3, 8), "qty": (5, 25)},
}

CATEGORIES = {
    "Electronics": (40.0, 900.0),
    "Home & Kitchen": (15.0, 300.0),
    "Apparel": (10.0, 150.0),
    "Sports & Outdoors": (12.0, 400.0),
    "Beauty": (8.0, 120.0),
}

PAYMENT_METHODS = [("card", 0.70), ("paypal", 0.20), ("bank_transfer", 0.10)]

FIRST_NAMES = ["Alex", "Sam", "Jordan", "Maria", "Wei", "Aisha", "Carlos", "Emma",
               "Noah", "Fatima", "Liam", "Yuki", "Elena", "Omar", "Priya", "Jonas"]
LAST_NAMES = ["Smith", "Garcia", "Chen", "Mueller", "Patel", "Kim", "Rossi",
              "Novak", "Tanaka", "Ali", "Brown", "Silva", "Ivanov", "Dubois"]

REFUND_REASONS = ["defective", "not_as_described", "changed_mind", "late_delivery", "damaged"]


def _weighted_choice(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    values = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]
    return rng.choices(values, weights=weights, k=1)[0]


# ----------------------------------------------------------------------------
# Entity generation (returns plain dicts, ready for bulk insert)
# ----------------------------------------------------------------------------


def generate_reference_data(rng: random.Random, manifest: Manifest) -> dict[str, list[dict]]:
    regions = [
        {"id": i + 1, "code": code, "name": name}
        for i, (code, name, _) in enumerate(REGIONS)
    ]
    region_id_by_code = {r["code"]: r["id"] for r in regions}

    categories = [{"id": i + 1, "name": name} for i, name in enumerate(CATEGORIES)]
    category_id_by_name = {c["name"]: c["id"] for c in categories}

    products = []
    pid = 1
    for cat_name, (lo, hi) in CATEGORIES.items():
        for n in range(30):  # 30 products per category = 150 total
            price = round(rng.uniform(lo, hi), 2)
            products.append({
                "id": pid,
                "name": f"{cat_name.split(' ')[0]} Item {n + 1:03d}",
                "category_id": category_id_by_name[cat_name],
                "price": price,
                "created_at": datetime.combine(manifest.start_date, time(9, 0)),
            })
            pid += 1

    customers = []
    region_weights = [w for _, _, w in REGIONS]
    region_codes = [c for c, _, _ in REGIONS]
    for cid in range(1, 8001):  # 8,000 customers
        segment = _weighted_choice(rng, SEGMENTS)
        region_code = rng.choices(region_codes, weights=region_weights, k=1)[0]
        created = manifest.start_date + timedelta(days=rng.randint(-365, 300))
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        customers.append({
            "id": cid,
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}.{cid}@example.com",
            "segment": segment,
            "region_id": region_id_by_code[region_code],
            "created_at": datetime.combine(min(created, manifest.start_date), time(12, 0))
            if created < manifest.start_date
            else datetime.combine(created, time(12, 0)),
        })

    campaigns = _generate_campaigns(manifest)

    return {
        "regions": regions,
        "categories": categories,
        "products": products,
        "customers": customers,
        "marketing_campaigns": campaigns,
    }


def _generate_campaigns(manifest: Manifest) -> list[dict]:
    """Quarterly campaigns plus any campaign anomalies from the manifest."""
    campaigns = []
    cid = 1
    quarters = [
        ("New Year Kickoff 2025", date(2025, 1, 10), date(2025, 1, 31), 30000, "email"),
        ("Spring Refresh 2025", date(2025, 4, 1), date(2025, 4, 21), 35000, "social"),
        ("Back to School 2025", date(2025, 8, 15), date(2025, 9, 10), 40000, "search"),
        ("Black Friday 2025", date(2025, 11, 20), date(2025, 12, 1), 90000, "display"),
        ("New Year Kickoff 2026", date(2026, 1, 10), date(2026, 1, 31), 32000, "email"),
        ("Spring Refresh 2026", date(2026, 4, 1), date(2026, 4, 21), 38000, "social"),
    ]
    for name, start, end, spend, channel in quarters:
        campaigns.append({
            "id": cid, "name": name, "channel": channel,
            "start_date": start, "end_date": end, "spend": spend,
        })
        cid += 1

    for a in manifest.anomalies:
        if a["type"] == "campaign":
            campaigns.append({
                "id": cid,
                "name": a["campaign_name"],
                "channel": "display",
                "start_date": _as_date(a["period"]["start"]),
                "end_date": _as_date(a["period"]["end"]),
                "spend": float(a["spend"]),
            })
            cid += 1
    return campaigns


# ----------------------------------------------------------------------------
# Transactional data generation (the main loop)
# ----------------------------------------------------------------------------


def generate_transactions(
    rng: random.Random,
    manifest: Manifest,
    reference: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    products = reference["products"]
    customers = reference["customers"]
    campaigns = reference["marketing_campaigns"]
    category_name_by_id = {c["id"]: c["name"] for c in reference["categories"]}
    region_code_by_id = {r["id"]: r["code"] for r in reference["regions"]}

    # Pre-compute weighted customer sampling (enterprise orders more often).
    cust_weights = [SEGMENT_BEHAVIOR[c["segment"]]["weight"] for c in customers]

    # Campaign anomaly attribution rates by campaign id.
    attribution_rate_by_campaign = {c["id"]: 0.20 for c in campaigns}
    for a in manifest.anomalies:
        if a["type"] == "campaign":
            for c in campaigns:
                if c["name"] == a["campaign_name"]:
                    attribution_rate_by_campaign[c["id"]] = float(a["attribution_rate"])

    orders: list[dict] = []
    order_items: list[dict] = []
    payments: list[dict] = []
    refunds: list[dict] = []

    order_id = 1
    item_id = 1
    payment_id = 1
    refund_id = 1

    d = manifest.start_date
    while d <= manifest.end_date:
        n_target = round(
            manifest.base_orders_per_day
            * seasonality_factor(d, manifest)
            * rng.uniform(0.9, 1.1)  # daily noise
        )
        active_campaigns = [
            c for c in campaigns if c["start_date"] <= d <= c["end_date"]
        ]

        for _ in range(n_target):
            cust = rng.choices(customers, weights=cust_weights, k=1)[0]
            segment = cust["segment"]
            region_code = region_code_by_id[cust["region_id"]]

            # ---- ANOMALY GATE: volume anomalies drop matching orders ----
            if rng.random() > volume_multiplier(d, segment, region_code, manifest):
                continue

            status = rng.choices(
                ["completed", "cancelled", "pending"], weights=[0.92, 0.05, 0.03], k=1
            )[0]
            order_ts = datetime.combine(d, time(rng.randint(6, 22), rng.randint(0, 59)))

            campaign_id = None
            if active_campaigns:
                camp = rng.choice(active_campaigns)
                if rng.random() < attribution_rate_by_campaign[camp["id"]]:
                    campaign_id = camp["id"]

            behavior = SEGMENT_BEHAVIOR[segment]
            n_items = rng.randint(*behavior["items"])
            chosen = rng.sample(products, k=min(n_items, len(products)))

            order_total = 0.0
            categories_in_order: set[str] = set()
            staged_items = []
            for prod in chosen:
                cat_name = category_name_by_id[prod["category_id"]]
                categories_in_order.add(cat_name)
                qty = rng.randint(*behavior["qty"])
                unit_price = round(
                    float(prod["price"]) * price_factor(d, cat_name, manifest), 2
                )
                staged_items.append({
                    "id": item_id,
                    "order_id": order_id,
                    "product_id": prod["id"],
                    "quantity": qty,
                    "unit_price": unit_price,
                })
                order_total += qty * unit_price
                item_id += 1

            discount = round(order_total * rng.choice([0, 0, 0, 0, 0.05, 0.10]), 2)

            orders.append({
                "id": order_id,
                "customer_id": cust["id"],
                "campaign_id": campaign_id,
                "status": status,
                "order_date": order_ts,
                "discount_amount": discount,
            })
            order_items.extend(staged_items)

            if status != "cancelled":
                paid_amount = round(order_total - discount, 2)
                payments.append({
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": paid_amount,
                    "method": _weighted_choice(rng, PAYMENT_METHODS),
                    "status": "succeeded",
                    "paid_at": order_ts + timedelta(minutes=rng.randint(1, 120)),
                })
                payment_id += 1

                # ---- ANOMALY: duplicate payment rows (data quality) ----
                if rng.random() < duplicate_payment_fraction(d, manifest):
                    dup = dict(payments[-1])
                    dup["id"] = payment_id
                    dup["paid_at"] = dup["paid_at"] + timedelta(seconds=rng.randint(1, 30))
                    payments.append(dup)
                    payment_id += 1

            # ---- ANOMALY: refund-rate spike for affected categories ----
            if status == "completed":
                if rng.random() < refund_rate(d, categories_in_order, manifest):
                    frac = rng.choice([1.0, 1.0, 0.5])  # mostly full refunds
                    refunds.append({
                        "id": refund_id,
                        "order_id": order_id,
                        "amount": round((order_total - discount) * frac, 2),
                        "reason": rng.choice(REFUND_REASONS),
                        "refunded_at": order_ts + timedelta(days=rng.randint(2, 14)),
                    })
                    refund_id += 1

            order_id += 1
        d += timedelta(days=1)

    return {
        "orders": orders,
        "order_items": order_items,
        "payments": payments,
        "refunds": refunds,
    }


def generate_dataset(manifest_path: str | Path) -> dict[str, list[dict]]:
    """Full pipeline: manifest -> reference data -> transactions."""
    manifest = load_manifest(manifest_path)
    rng = random.Random(manifest.random_seed)
    reference = generate_reference_data(rng, manifest)
    transactions = generate_transactions(rng, manifest, reference)
    return {**reference, **transactions}
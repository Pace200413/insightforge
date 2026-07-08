"""Seed the InsightForge database with synthetic data.

Usage (from repo root, after `make db-up`):
    make seed
    # or directly:
    PYTHONPATH=backend .venv/bin/python scripts/generate_data.py

Drops and recreates all tables (idempotent), generates ~18 months of
activity from scripts/anomalies.yaml, bulk-inserts it, then prints a
verification summary so you can SEE the injected anomalies in the data.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, insert, text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.datagen import generate_dataset  # noqa: E402

TABLE_ORDER = [
    ("regions", models.Region),
    ("categories", models.Category),
    ("products", models.Product),
    ("customers", models.Customer),
    ("marketing_campaigns", models.MarketingCampaign),
    ("orders", models.Order),
    ("order_items", models.OrderItem),
    ("payments", models.Payment),
    ("refunds", models.Refund),
]

CHUNK = 5000


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)

    print("Recreating schema ...")
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)

    print("Generating dataset from scripts/anomalies.yaml ...")
    t0 = time.time()
    data = generate_dataset(REPO_ROOT / "scripts" / "anomalies.yaml")
    print(f"  generated in {time.time() - t0:.1f}s")

    with engine.begin() as conn:
        for table_name, model in TABLE_ORDER:
            rows = data[table_name]
            for i in range(0, len(rows), CHUNK):
                conn.execute(insert(model), rows[i : i + CHUNK])
            print(f"  inserted {len(rows):>8,} rows into {table_name}")

    print("\n" + "=" * 72)
    print("GROUND-TRUTH VERIFICATION -- the anomalies should be visible below")
    print("=" * 72)
    with engine.connect() as conn:
        _monthly_net_revenue(conn)
        _june_by_segment(conn)
        _electronics_refund_rate(conn)
        _duplicate_payments(conn)
        _campaign_performance(conn)
    print("\nDone. The database is seeded and the anomalies are live.")


def _monthly_net_revenue(conn) -> None:
    print("\nMonthly net revenue (2026) -- expect a clear June drop:")
    rows = conn.execute(text("""
        SELECT to_char(date_trunc('month', o.order_date), 'YYYY-MM') AS month,
               ROUND(SUM(g.gross - COALESCE(r.refunded, 0) - o.discount_amount), 0)
                   AS net_revenue
        FROM orders o
        JOIN (SELECT order_id, SUM(quantity * unit_price) AS gross
              FROM order_items GROUP BY order_id) g ON g.order_id = o.id
        LEFT JOIN (SELECT order_id, SUM(amount) AS refunded
                   FROM refunds GROUP BY order_id) r ON r.order_id = o.id
        WHERE o.status = 'completed'
          AND o.order_date >= '2026-01-01'
        GROUP BY 1 ORDER BY 1
    """)).fetchall()
    for month, revenue in rows:
        print(f"  {month}: {int(revenue):>12,}")


def _june_by_segment(conn) -> None:
    print("\nOrder volume May vs June 2026 by segment -- expect enterprise collapse:")
    rows = conn.execute(text("""
        SELECT c.segment,
               COUNT(*) FILTER (WHERE o.order_date >= '2026-05-01'
                                  AND o.order_date <  '2026-06-01') AS may_orders,
               COUNT(*) FILTER (WHERE o.order_date >= '2026-06-01'
                                  AND o.order_date <  '2026-07-01') AS june_orders
        FROM orders o JOIN customers c ON c.id = o.customer_id
        GROUP BY 1 ORDER BY 1
    """)).fetchall()
    for segment, may, june in rows:
        change = (june - may) / may * 100 if may else 0
        print(f"  {segment:<12} May: {may:>6,}   June: {june:>6,}   ({change:+.0f}%)")


def _electronics_refund_rate(conn) -> None:
    print("\nElectronics refund rate -- expect a June 2026 spike:")
    rows = conn.execute(text("""
        SELECT to_char(date_trunc('month', o.order_date), 'YYYY-MM') AS month,
               ROUND(100.0 * COUNT(DISTINCT r.order_id) / COUNT(DISTINCT o.id), 1) AS refund_pct
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p ON p.id = oi.product_id
        JOIN categories cat ON cat.id = p.category_id
        LEFT JOIN refunds r ON r.order_id = o.id
        WHERE cat.name = 'Electronics' AND o.status = 'completed'
          AND o.order_date >= '2026-03-01'
        GROUP BY 1 ORDER BY 1
    """)).fetchall()
    for month, pct in rows:
        print(f"  {month}: {pct}%")


def _duplicate_payments(conn) -> None:
    print("\nDuplicate payment rows (May-June 2026 data-quality bug):")
    n = conn.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT order_id FROM payments
            GROUP BY order_id, amount HAVING COUNT(*) > 1
        ) dupes
    """)).scalar()
    print(f"  orders with duplicated payments: {n:,}")


def _campaign_performance(conn) -> None:
    print("\nCampaign cost per attributed order -- expect Summer Splash to be extreme:")
    rows = conn.execute(text("""
        SELECT mc.name, mc.spend, COUNT(o.id) AS attributed_orders,
               ROUND(mc.spend / GREATEST(COUNT(o.id), 1), 0) AS cost_per_order
        FROM marketing_campaigns mc
        LEFT JOIN orders o ON o.campaign_id = mc.id
        GROUP BY mc.id, mc.name, mc.spend ORDER BY cost_per_order DESC
    """)).fetchall()
    for name, spend, n, cpo in rows:
        print(f"  {name:<26} spend: {int(spend):>7,}  orders: {n:>6,}  cost/order: {int(cpo):>7,}")


if __name__ == "__main__":
    main()
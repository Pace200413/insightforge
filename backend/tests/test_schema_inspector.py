"""Unit tests for SchemaInspector -- pure, no database required.

We build a fake schema from ColumnInfo/TableInfo objects directly,
then test the search scoring logic in isolation.
"""

from app.db.schema_inspector import ColumnInfo, SchemaInspector, TableInfo


def _make_table(name: str, cols: list[str], rows: int = 1000, fks: dict | None = None) -> TableInfo:
    fks = fks or {}
    columns = tuple(
        ColumnInfo(
            name=c,
            type="INTEGER" if c == "id" or c.endswith("_id") else "VARCHAR",
            nullable=c != "id",
            is_primary_key=c == "id",
            foreign_key=fks.get(c),
        )
        for c in cols
    )
    return TableInfo(name=name, columns=columns, row_count=rows)


def _inspector() -> SchemaInspector:
    return SchemaInspector(
        tables={
            t.name: t
            for t in [
                _make_table("customers", ["id", "name", "email", "segment", "region_id"], fks={"region_id": "regions.id"}),
                _make_table("orders", ["id", "customer_id", "status", "order_date", "discount_amount"], fks={"customer_id": "customers.id"}),
                _make_table("order_items", ["id", "order_id", "product_id", "quantity", "unit_price"], fks={"order_id": "orders.id", "product_id": "products.id"}),
                _make_table("products", ["id", "name", "category_id", "price"], fks={"category_id": "categories.id"}),
                _make_table("categories", ["id", "name"]),
                _make_table("regions", ["id", "code", "name"]),
                _make_table("refunds", ["id", "order_id", "amount", "reason", "refunded_at"], fks={"order_id": "orders.id"}),
                _make_table("payments", ["id", "order_id", "amount", "method", "status"], fks={"order_id": "orders.id"}),
            ]
        }
    )


def test_search_finds_orders_for_revenue_keywords():
    insp = _inspector()
    results = insp.search(["revenue", "order", "amount"])
    names = [t.name for t in results]
    assert "orders" in names
    assert "order_items" in names


def test_search_finds_refunds_for_refund_keyword():
    insp = _inspector()
    results = insp.search(["refund"])
    assert results[0].name == "refunds"


def test_search_returns_at_most_top_n():
    insp = _inspector()
    results = insp.search(["order", "customer", "region", "product"], top_n=3)
    assert len(results) <= 3


def test_search_always_include_overrides_top_n():
    insp = _inspector()
    results = insp.search(["refund"], always_include=["categories"], top_n=2)
    names = [t.name for t in results]
    assert "categories" in names   # forced in despite low relevance
    assert "refunds" in names      # top scored


def test_search_result_has_relevance_score():
    insp = _inspector()
    results = insp.search(["order"])
    for t in results:
        assert t.relevance_score >= 0.0


def test_no_keywords_returns_empty_list():
    insp = _inspector()
    results = insp.search([])
    assert results == []


def test_describe_tables_output_contains_table_names():
    insp = _inspector()
    tables = insp.search(["order", "customer"])
    desc = insp.describe_tables(tables)
    assert "TABLE: orders" in desc
    assert "TABLE: customers" in desc
    assert "->" in desc   # FK notation present


def test_full_summary_lists_all_tables():
    insp = _inspector()
    summary = insp.full_summary()
    for name in insp.tables:
        assert name in summary


def test_segment_column_found_when_searching_segment():
    insp = _inspector()
    results = insp.search(["segment"])
    names = [t.name for t in results]
    assert "customers" in names   # segment column lives on customers


def test_foreign_keys_parsed_correctly():
    insp = _inspector()
    orders = insp.tables["orders"]
    fks = dict(orders.foreign_keys)
    assert fks.get("customer_id") == "customers.id"
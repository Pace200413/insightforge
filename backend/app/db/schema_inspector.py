"""Schema discovery -- the AI's window into the database.

The AI gets a SEARCH interface to the schema, not the whole schema dumped
into its context. Forcing it to search rather than receive everything is:
  1. Cheaper -- smaller prompts.
  2. More realistic -- an analyst exploring an unfamiliar DB does the same.
  3. Better tested -- search logic is pure and unit-testable.

Public API:
    inspector = await SchemaInspector.build(engine)
    tables    = inspector.search(["revenue", "order", "customer"])
    summary   = inspector.describe_tables(tables)   # formatted for an LLM prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_primary_key: bool
    foreign_key: str | None   # "other_table.column" or None


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[ColumnInfo, ...]
    row_count: int
    relevance_score: float = 0.0   # set during search, not stored

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def foreign_keys(self) -> list[tuple[str, str]]:
        """Returns [(local_col, referenced_table.col), ...]"""
        return [
            (c.name, c.foreign_key)
            for c in self.columns
            if c.foreign_key is not None
        ]


@dataclass
class SchemaInspector:
    """Cached snapshot of the database schema with keyword search."""

    tables: dict[str, TableInfo]

    # ------------------------------------------------------------------
    # Construction (async, runs SQLAlchemy sync inspection in executor)
    # ------------------------------------------------------------------

    @classmethod
    async def build(cls, engine: AsyncEngine) -> "SchemaInspector":
        """Introspect the live database and build the inspector."""

        def _sync_inspect(conn):
            insp = inspect(conn)
            table_names = insp.get_table_names()
            tables = {}

            for table_name in table_names:
                pk_cols = set(insp.get_pk_constraint(table_name).get("constrained_columns", []))
                fk_map: dict[str, str] = {}
                for fk in insp.get_foreign_keys(table_name):
                    for local_col, ref_col in zip(
                        fk["constrained_columns"], fk["referred_columns"]
                    ):
                        fk_map[local_col] = f"{fk['referred_table']}.{ref_col}"

                columns = tuple(
                    ColumnInfo(
                        name=col["name"],
                        type=str(col["type"]),
                        nullable=bool(col.get("nullable", True)),
                        is_primary_key=col["name"] in pk_cols,
                        foreign_key=fk_map.get(col["name"]),
                    )
                    for col in insp.get_columns(table_name)
                )
                tables[table_name] = TableInfo(
                    name=table_name, columns=columns, row_count=0
                )
            return tables

        async with engine.connect() as conn:
            tables = await conn.run_sync(_sync_inspect)

            # Fetch row counts efficiently
            for table_name in tables:
                result = await conn.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608 (internal use, table names from inspector)
                )
                count = result.scalar() or 0
                tables[table_name] = TableInfo(
                    name=table_name,
                    columns=tables[table_name].columns,
                    row_count=int(count),
                )

        return cls(tables=tables)

    # ------------------------------------------------------------------
    # Search: returns tables relevant to a list of keyword concepts
    # ------------------------------------------------------------------

    # Column names that appear in EVERY table (never informative for search)
    _STOP_NAMES = frozenset({"id", "created_at", "updated_at", "status"})

    def search(
        self,
        keywords: list[str],
        *,
        always_include: list[str] | None = None,
        top_n: int = 6,
    ) -> list[TableInfo]:
        """Return at most `top_n` tables most relevant to `keywords`.

        Scoring:
          +10  keyword is a substring of the table name
          +5   keyword exactly matches a non-stop column name
          +2   keyword is a substring of a non-stop column name
          +1   keyword matches a foreign-key target table name
        """
        if not keywords:
            return []
        keywords_lower = [kw.lower().strip() for kw in keywords]
        scored: list[tuple[float, TableInfo]] = []

        for table in self.tables.values():
            score = 0.0
            for kw in keywords_lower:
                if kw in table.name.lower():
                    score += 10

                for col in table.columns:
                    if col.name in self._STOP_NAMES:
                        continue
                    if kw == col.name.lower():
                        score += 5
                    elif kw in col.name.lower():
                        score += 2
                    if col.foreign_key and kw in col.foreign_key.lower():
                        score += 1

            scored.append((score, table))

        scored.sort(key=lambda x: -x[0])

        # Build result set: always-include tables + top scored tables
        result_names: list[str] = list(always_include or [])
        for _, table in scored:
            if table.name not in result_names:
                result_names.append(table.name)
            if len(result_names) >= top_n + len(always_include or []):
                break

        # Return as TableInfo objects with relevance_score populated
        score_by_name = {t.name: s for s, t in scored}
        return [
            TableInfo(
                name=self.tables[n].name,
                columns=self.tables[n].columns,
                row_count=self.tables[n].row_count,
                relevance_score=score_by_name.get(n, 0.0),
            )
            for n in result_names
            if n in self.tables
        ]

    # ------------------------------------------------------------------
    # Formatting for LLM prompts
    # ------------------------------------------------------------------

    def describe_tables(self, tables: list[TableInfo]) -> str:
        """Format selected tables as a concise text block for an LLM prompt."""
        lines: list[str] = []
        for t in tables:
            lines.append(f"TABLE: {t.name}  ({t.row_count:,} rows)")
            for col in t.columns:
                pk = " [PK]" if col.is_primary_key else ""
                fk = f" -> {col.foreign_key}" if col.foreign_key else ""
                null = "" if col.nullable else " NOT NULL"
                lines.append(f"  {col.name}  {col.type}{pk}{fk}{null}")
            lines.append("")
        return "\n".join(lines).strip()

    def full_summary(self) -> str:
        """One-line-per-table overview (used in the question interpreter prompt)."""
        lines = [
            f"  {t.name:<30} {t.row_count:>8,} rows  "
            f"cols: {', '.join(c.name for c in t.columns if not c.is_primary_key)[:80]}"
            for t in self.tables.values()
        ]
        return "\n".join(lines)
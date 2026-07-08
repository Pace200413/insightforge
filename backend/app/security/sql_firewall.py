"""SQL Security Firewall.

Every AI-generated query must pass through here before execution. The check
is parser-based (SQLGlot), not regex-based -- regexes are trivially bypassed
("DE/**/LETE", nested statements, etc.), while a parse tree cannot lie about
what a statement does.

Policy enforced:
  1. Exactly ONE statement per query (blocks "SELECT 1; DROP TABLE x").
  2. Top-level statement must be a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs).
  3. No mutating/DDL node anywhere in the tree (INSERT/UPDATE/DELETE/DROP/
     ALTER/CREATE/TRUNCATE/GRANT/COPY/CALL/SET/...).
  4. No dangerous functions (pg_sleep, pg_read_file, dblink, ...).
  5. Every referenced table must be in the allowlist.
  6. Blocked columns (PII) may not be referenced (e.g. customers.email).
  7. No SELECT ... INTO, no row locks (FOR UPDATE/SHARE).
  8. A row LIMIT is enforced: injected if missing, reduced if too large.

Defense in depth: this firewall is the first gate; the read-only Postgres
role (scripts/create_readonly_role.sql) is the last.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

# ---------------------------------------------------------------------------
# Policy configuration
# ---------------------------------------------------------------------------

# Node class names that indicate mutation/DDL/admin anywhere in the tree.
# Name-based (not isinstance) so we stay robust across sqlglot versions.
_FORBIDDEN_NODE_NAMES = {
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable",
    "Truncate", "TruncateTable", "Grant", "Revoke", "Command", "Use",
    "Call", "Copy", "Merge", "LoadData", "Pragma", "Transaction",
    "Commit", "Rollback", "Lock", "Set", "AlterColumn", "RenameTable",
}

# Postgres functions that enable DoS, file access, or network egress.
_BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export",
    "dblink", "dblink_connect", "dblink_exec",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "set_config", "copy_from", "copy_to",
}

# Column names that must never appear in AI queries (PII protection).
_BLOCKED_COLUMN_NAMES = {"email"}

DEFAULT_ALLOWED_TABLES = frozenset({
    "regions", "customers", "categories", "products", "marketing_campaigns",
    "orders", "order_items", "payments", "refunds",
})


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


@dataclass
class FirewallVerdict:
    allowed: bool
    sql: str                       # the (possibly modified) SQL to execute
    original_sql: str
    violations: list[str] = field(default_factory=list)
    modifications: list[str] = field(default_factory=list)
    tables_referenced: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.allowed:
            mods = f" (modified: {'; '.join(self.modifications)})" if self.modifications else ""
            return f"ALLOWED{mods}"
        return f"BLOCKED: {'; '.join(self.violations)}"


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------


class SQLFirewall:
    def __init__(
        self,
        allowed_tables: frozenset[str] | set[str] = DEFAULT_ALLOWED_TABLES,
        max_rows: int = 10_000,
        blocked_columns: set[str] = _BLOCKED_COLUMN_NAMES,
    ) -> None:
        self.allowed_tables = {t.lower() for t in allowed_tables}
        self.max_rows = max_rows
        self.blocked_columns = {c.lower() for c in blocked_columns}

    def check(self, sql: str) -> FirewallVerdict:
        """Validate a query. Returns a verdict; never raises on bad SQL."""
        verdict = FirewallVerdict(allowed=False, sql=sql, original_sql=sql)

        # --- 0. Parse ---
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except sqlglot.errors.ParseError as e:
            verdict.violations.append(f"SQL parse error: {e}")
            return verdict

        statements = [s for s in statements if s is not None]
        if len(statements) == 0:
            verdict.violations.append("Empty query.")
            return verdict

        # --- 1. Single statement only ---
        if len(statements) > 1:
            verdict.violations.append(
                f"Multiple statements ({len(statements)}) are not allowed."
            )
            return verdict

        expr = statements[0]

        # --- 2. Top-level must be a SELECT / set operation of SELECTs ---
        if not isinstance(expr, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            verdict.violations.append(
                f"Only SELECT statements are allowed "
                f"(got {type(expr).__name__.upper()})."
            )
            return verdict

        # --- 3. No forbidden nodes anywhere in the tree ---
        for node in expr.walk():
            node_obj = node[0] if isinstance(node, tuple) else node
            if type(node_obj).__name__ in _FORBIDDEN_NODE_NAMES:
                verdict.violations.append(
                    f"Forbidden operation in query: {type(node_obj).__name__.upper()}."
                )

        # --- 4. No dangerous functions ---
        for func in expr.find_all(exp.Func):
            fname = self._function_name(func)
            if fname in _BLOCKED_FUNCTIONS:
                verdict.violations.append(f"Blocked function: {fname}().")

        # --- 5. Table allowlist ---
        tables = sorted({t.name.lower() for t in expr.find_all(exp.Table) if t.name})
        # CTE names are aliases, not real tables -- exclude them
        cte_names = {c.alias_or_name.lower() for c in expr.find_all(exp.CTE)}
        real_tables = [t for t in tables if t not in cte_names]
        verdict.tables_referenced = real_tables
        for t in real_tables:
            if t not in self.allowed_tables:
                verdict.violations.append(f"Table not in allowlist: {t}.")

        # --- 6. Blocked columns (PII) ---
        for col in expr.find_all(exp.Column):
            if col.name and col.name.lower() in self.blocked_columns:
                verdict.violations.append(
                    f"Column '{col.name}' is blocked (sensitive data policy)."
                )

        # --- 7. SELECT INTO / row locks ---
        if expr.args.get("into") is not None:
            verdict.violations.append("SELECT INTO is not allowed.")
        if expr.args.get("locks"):
            verdict.violations.append("Row locks (FOR UPDATE/SHARE) are not allowed.")

        if verdict.violations:
            return verdict

        # --- 8. Enforce row limit ---
        expr, modification = self._enforce_limit(expr)
        if modification:
            verdict.modifications.append(modification)

        verdict.allowed = True
        verdict.sql = expr.sql(dialect="postgres")
        return verdict

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _function_name(func: exp.Func) -> str:
        if isinstance(func, exp.Anonymous):
            return str(func.this or "").lower()
        try:
            return func.sql_name().lower()
        except Exception:
            return type(func).__name__.lower()

    def _enforce_limit(self, expr):
        """Inject LIMIT if missing; reduce it if it exceeds max_rows."""
        limit_node = expr.args.get("limit")
        if limit_node is None:
            try:
                new_expr = expr.limit(self.max_rows)
                return new_expr, f"LIMIT {self.max_rows} injected."
            except Exception:
                return expr, None
        # Existing limit -- check its value
        try:
            current = int(limit_node.expression.this)
        except (AttributeError, TypeError, ValueError):
            return expr, None
        if current > self.max_rows:
            new_expr = expr.limit(self.max_rows)
            return new_expr, f"LIMIT reduced from {current} to {self.max_rows}."
        return expr, None

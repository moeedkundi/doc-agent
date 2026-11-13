"""SELECT-only gate for model-written SQL (SPEC §6.3), applied before anything reaches Postgres.

Pipeline: parse with sqlglot (Postgres dialect) -> exactly one statement -> a SELECT/UNION/
INTERSECT/EXCEPT with no data-modifying or locking parts anywhere in the tree -> every table
on the allowlist (CTE names aside) -> every function on the allowlist -> LIMIT injected or
clamped. Whatever passes still runs as the read-only, RLS-scoped `docagent_reader` role with a
statement timeout, so this gate is the first of several layers, not the only one.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.expressions.core import Expression

ALLOWED_TABLES: frozenset[str] = frozenset(
    {"documents", "invoices", "invoice_lines", "purchase_orders", "po_lines", "invoice_po_matches"}
)

# sqlglot node classes that are fine inside a read-only analytical query. Anything else that
# sqlglot models as a function is rejected (deny by default).
_ALLOWED_FUNC_CLASSES: frozenset[str] = frozenset(
    {
        # logic and conditionals (sqlglot models these as functions)
        "And", "Or", "Not", "Xor", "If", "Case", "Coalesce", "Nullif", "Greatest", "Least",
        # casts
        "Cast", "TryCast",
        # aggregates
        "Count", "Sum", "Avg", "Min", "Max", "ArrayAgg", "GroupConcat", "Stddev", "Variance",
        "PercentileCont", "PercentileDisc",
        # numbers
        "Round", "Abs", "Floor", "Ceil", "Sign", "Mod", "Div", "Sqrt", "Pow",
        # text
        "Lower", "Upper", "Initcap", "Length", "Trim", "Concat", "ConcatWs", "Substring",
        "Left", "Right", "Replace", "StrPosition", "Split",
        # dates
        "Extract", "TimestampTrunc", "DateTrunc", "CurrentDate", "CurrentTimestamp",
        "CurrentTime", "DateAdd", "DateSub", "DateDiff", "TsOrDsToDate", "ToChar", "Date",
        "Interval", "Age", "TimeToStr",
        # window functions
        "RowNumber", "Rank", "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "Ntile",
    }
)  # fmt: skip

# Functions sqlglot does not model (parsed as Anonymous), allowed by name.
_ALLOWED_ANONYMOUS: frozenset[str] = frozenset(
    {"date_part", "to_char", "initcap", "string_agg", "btrim", "ltrim", "rtrim", "trunc"}
)

# Nodes that write, lock, export or change session state. None may appear anywhere.
_FORBIDDEN_NODES: tuple[type[Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Copy, exp.Into, exp.Lock, exp.Set, exp.Use,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Grant,
)  # fmt: skip

_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class SqlRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class CheckedQuery:
    sql: str
    """The statement to execute, with the LIMIT applied."""
    tables: frozenset[str]


def _function_name(node: exp.Func) -> str:
    return node.name if isinstance(node, exp.Anonymous) else type(node).__name__


def check_select(
    sql: str, *, max_rows: int, allowed_tables: frozenset[str] = ALLOWED_TABLES
) -> CheckedQuery:
    """Validate `sql` and return the statement to run (fetching max_rows + 1 to detect
    truncation). Raises SqlRejected with a machine-readable code otherwise."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except ParseError as exc:
        raise SqlRejected("sql_parse_error", f"could not parse SQL: {exc}") from None
    if len(statements) != 1:
        raise SqlRejected(
            "multiple_statements" if len(statements) > 1 else "empty_query",
            f"expected exactly one statement, got {len(statements)}",
        )
    tree = statements[0]

    if not isinstance(tree, _QUERY_ROOTS):
        raise SqlRejected("not_select", f"only SELECT queries are allowed, not {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRejected(
                "not_select", f"{node.key.upper()} is not allowed in a read-only query"
            )

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower()
        if not name:  # e.g. a table-valued function in FROM
            raise SqlRejected("table_not_allowed", "only plain table references are allowed")
        if schema and schema != "public":
            raise SqlRejected("table_not_allowed", f"schema {schema!r} is not allowed")
        if name in cte_names and not schema:
            continue
        if name not in allowed_tables:
            raise SqlRejected(
                "table_not_allowed",
                f"table {name!r} is not allowed; use: {', '.join(sorted(allowed_tables))}",
            )
        tables.add(name)

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        allowed = (
            name.lower() in _ALLOWED_ANONYMOUS
            if isinstance(func, exp.Anonymous)
            else name in _ALLOWED_FUNC_CLASSES
        )
        if not allowed:
            shown = name if isinstance(func, exp.Anonymous) else func.sql_name().lower()
            raise SqlRejected("function_not_allowed", f"function {shown!r} is not allowed")

    fetch = max_rows + 1
    if isinstance(tree, exp.Select):
        limit = tree.args.get("limit")
        current = None
        if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
            literal = limit.expression
            current = int(literal.this) if literal.is_int else None
        if limit is not None and current is None:
            raise SqlRejected("invalid_limit", "LIMIT must be a plain integer")
        tree = tree.limit(min(current, fetch) if current is not None else fetch, copy=False)
        final = tree
    else:  # set operations: wrap so the cap applies to the whole result
        final = exp.select("*").from_(tree.subquery("q")).limit(fetch)

    return CheckedQuery(sql=final.sql(dialect="postgres"), tables=frozenset(tables))

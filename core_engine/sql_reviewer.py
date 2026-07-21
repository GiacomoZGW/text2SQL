"""Deterministic SQL semantic and execution-plan checks for Text2SQL candidates."""

import os
import re
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


def _table_key(name: str) -> str:
    return name.lower().split(".")[-1]


def _catalog_table_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_table_key(str(table.get("name", ""))): table for table in catalog.get("tables", [])}


def _relationship_pairs(catalog: dict[str, Any]) -> set[frozenset[tuple[str, str]]]:
    pairs: set[frozenset[tuple[str, str]]] = set()
    for relation in catalog.get("relationships", []):
        if not isinstance(relation, dict):
            continue
        left = str(relation.get("from", ""))
        right = str(relation.get("to", ""))
        if "." not in left or "." not in right:
            continue
        left_table, left_column = left.rsplit(".", 1)
        right_table, right_column = right.rsplit(".", 1)
        pairs.add(frozenset({(_table_key(left_table), left_column.lower()), (_table_key(right_table), right_column.lower())}))
    return pairs


def _contains_aggregate(expression: exp.Expression) -> bool:
    return expression.find(exp.AggFunc) is not None


def _column_pair(condition: exp.Expression, alias_map: dict[str, str]) -> frozenset[tuple[str, str]] | None:
    if not isinstance(condition, exp.EQ):
        return None
    left = condition.left if isinstance(condition.left, exp.Column) else None
    right = condition.right if isinstance(condition.right, exp.Column) else None
    if left is None or right is None or not left.table or not right.table:
        return None
    return frozenset(
        {
            (alias_map.get(left.table.lower(), _table_key(left.table)), left.name.lower()),
            (alias_map.get(right.table.lower(), _table_key(right.table)), right.name.lower()),
        }
    )


def review_sql_candidate(sql: str, catalog: dict[str, Any], dialect: str = "sqlite") -> dict[str, Any]:
    """Check schema names, joins, aggregation shape, and configured tenant scope."""
    result: dict[str, Any] = {"approved": False, "errors": [], "warnings": []}
    try:
        tree = parse_one(sql, read=dialect)
    except ParseError as exc:
        result["errors"].append(f"Reviewer parser rejected SQL: {exc}")
        return result

    table_map = _catalog_table_map(catalog)
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name}
    select_aliases = {
        expression.alias.lower()
        for select in tree.find_all(exp.Select)
        for expression in select.expressions
        if isinstance(expression, exp.Alias) and expression.alias
    }
    alias_map: dict[str, str] = {}
    query_tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        table_name = _table_key(table.name)
        if table_name in cte_names:
            continue
        if table_name not in table_map:
            result["errors"].append(f"Table does not exist in the active schema catalog: {table.name}")
            continue
        alias_map[table.alias_or_name.lower()] = table_name
        query_tables.add(table_name)

    for column in tree.find_all(exp.Column):
        if column.is_star:
            continue
        column_name = column.name.lower()
        if not column.table and column_name in select_aliases:
            continue
        if column.table:
            table_name = alias_map.get(column.table.lower(), _table_key(column.table))
            table = table_map.get(table_name)
            if table is not None and column_name not in {str(item["name"]).lower() for item in table.get("columns", [])}:
                result["errors"].append(f"Column does not exist: {table_name}.{column.name}")
        elif query_tables:
            matching_tables = [
                table_name
                for table_name in query_tables
                if column_name in {str(item["name"]).lower() for item in table_map[table_name].get("columns", [])}
            ]
            if not matching_tables:
                result["errors"].append(f"Column does not exist in referenced tables: {column.name}")

    relationships = _relationship_pairs(catalog)
    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if join.args.get("kind") == "CROSS" or on_clause is None:
            result["errors"].append("Cartesian product risk: every JOIN must have an ON condition")
            continue
        join_pairs = [pair for condition in on_clause.walk() if (pair := _column_pair(condition, alias_map))]
        if relationships and join_pairs and not any(pair in relationships for pair in join_pairs):
            result["errors"].append("JOIN condition does not match a configured foreign-key or business relationship")

    for select in tree.find_all(exp.Select):
        expressions = list(select.expressions)
        has_aggregate = any(_contains_aggregate(expression) for expression in expressions)
        if not has_aggregate:
            continue
        group = select.args.get("group")
        group_sql = {expression.sql(dialect=dialect).lower() for expression in group.expressions} if group else set()
        for expression in expressions:
            base_expression = expression.this if isinstance(expression, exp.Alias) else expression
            if isinstance(base_expression, exp.Literal) or _contains_aggregate(base_expression):
                continue
            normalized = base_expression.sql(dialect=dialect).lower()
            if normalized not in group_sql:
                result["errors"].append("Aggregate query selects a non-aggregated expression that is missing from GROUP BY")
                break

    permissions = catalog.get("permissions", {}) if isinstance(catalog.get("permissions"), dict) else {}
    tenant_column = str(permissions.get("tenant_column", "")).lower()
    if tenant_column and any(
        tenant_column in {str(column["name"]).lower() for column in table_map[table_name].get("columns", [])}
        for table_name in query_tables
    ):
        where_sql = " ".join(where.sql(dialect=dialect).lower() for where in tree.find_all(exp.Where))
        if tenant_column not in where_sql:
            result["errors"].append("Tenant-scoped source requires a tenant predicate")

    result["approved"] = not result["errors"]
    return result


def review_execution_plan(
    plan_rows: list[Any],
    max_full_scans: int | None = None,
    max_cost: float | None = None,
) -> dict[str, Any]:
    """Classify EXPLAIN output without making a database-specific plan a hard dependency."""
    max_full_scans = max_full_scans if max_full_scans is not None else int(os.getenv("SCHEMA_REVIEW_MAX_FULL_SCANS", "1"))
    max_cost = max_cost if max_cost is not None else float(os.getenv("SCHEMA_REVIEW_MAX_EXPLAIN_COST", "100000"))
    text = "\n".join(" ".join(str(value) for value in row) if isinstance(row, (tuple, list)) else str(row) for row in plan_rows)
    lowered = text.lower()
    full_scans = len(re.findall(r"\bscan\s+(?:table\s+)?[a-z0-9_.\"]+", lowered))
    costs = [float(value) for value in re.findall(r"cost=\d+(?:\.\d+)?\.\.(\d+(?:\.\d+)?)", lowered)]
    errors: list[str] = []
    warnings: list[str] = []
    if "cross product" in lowered or "cartesian" in lowered:
        errors.append("EXPLAIN detected a Cartesian product")
    if full_scans > max_full_scans:
        errors.append(f"EXPLAIN detected {full_scans} full scans, exceeding the threshold {max_full_scans}")
    elif full_scans:
        warnings.append(f"EXPLAIN detected {full_scans} full scan(s)")
    if costs and max(costs) > max_cost:
        errors.append(f"EXPLAIN cost {max(costs):.0f} exceeds the threshold {max_cost:.0f}")
    return {
        "approved": not errors,
        "errors": errors,
        "warnings": warnings,
        "full_scans": full_scans,
        "max_cost": max(costs) if costs else None,
    }

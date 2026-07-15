"""Find SQL hiding inside host-language string literals, and index it as SQL.

A query string passed to `pd.read_sql(...)` or `cursor.execute(...)` is not a
string as far as this tool is concerned — it is *code that reads and writes data
products*. Once parsed, it emits exactly the same dataset/column edges the SQL
front-end does, hung off the enclosing HOST function.

This is what makes `processed_iris` ONE node: the table created in
sql/03_features.sql and the table read by etl/extract.py are the same key.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from schema import CONSUMES, HAS_COL, PRODUCES, READS_COL, WRITES_COL, Edge, Symbol

# host calls whose string argument is a query
SQL_SINKS = {"read_sql", "read_sql_query", "read_sql_table", "execute", "executemany",
             "dbGetQuery", "dbExecute", "dbSendQuery", "sql", "to_sql", "text"}

SQL_HINT = ("select ", "insert ", "update ", "delete ", "create ", "with ", "merge ")


def looks_like_sql(s):
    if not s or len(s) < 12:
        return False
    head = s.strip().lower()
    return any(head.startswith(h) or f"\n{h}" in head for h in SQL_HINT) or \
        (" from " in head and "select" in head)


def index_query(idx, sql_text, host_sid, sp, node, add, table_hint=None):
    """Parse one query string and wire its tables/columns to the host function."""
    try:
        statements = sqlglot.parse(sql_text)
    except Exception:
        return False
    wired = False

    for stmt in statements:
        if stmt is None:
            continue
        wired = True

        target = None
        if isinstance(stmt, exp.Create):
            t = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
            if isinstance(t, exp.Table):
                target = t.name
        elif isinstance(stmt, exp.Insert):
            t = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
            if isinstance(t, exp.Table):
                target = t.name
        if table_hint:
            target = table_hint

        if target:
            d = node("dataset", target, target, sp, "table")
            add(host_sid, d, PRODUCES, "writes table")

        for t in stmt.find_all(exp.Table):
            if t.name and t.name != target:
                d = node("dataset", t.name, t.name, sp, "table")
                add(d, host_sid, CONSUMES, "sql read")

        select = stmt.find(exp.Select)
        produced = []
        if select is not None:
            for s in select.selects:
                if isinstance(s, exp.Star):
                    if target:
                        add(node("dataset", target, target, sp, "table"),
                            node("column", "*unknown*", "* (unresolved)", sp, "SELECT *"),
                            HAS_COL, "SELECT * — column list unknown")
                    continue
                if s.alias_or_name:
                    produced.append(s.alias_or_name)

        for c in stmt.find_all(exp.Column):
            if c.name:
                add(host_sid, node("column", c.name, c.name, sp), READS_COL, "via sql")
        for c in produced:
            cid = node("column", c, c, sp)
            add(host_sid, cid, READS_COL, "via sql")
            if target:
                add(node("dataset", target, target, sp, "table"), cid, HAS_COL)

    return wired

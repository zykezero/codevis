"""SQL front-end (D12): a third language, the same index schema.

SQL is the EASIEST language this tool will ever index. Table and column names are
literal by construction — there is no type inference to fail, no import graph to
chase. A `CREATE TABLE x AS SELECT a, b FROM y` states its producer, its consumers
and its exact column list in the source text.

The one honest gap is `SELECT *`: the table is known, the columns are not. We say
so rather than guessing (same rule as everywhere else — a false link is worse than
a missing one).

Mapping onto the existing schema:
    a statement (CREATE TABLE / VIEW)  ->  Symbol(kind="function")   ... the "code"
    a table or view                    ->  Symbol(kind="dataset")    ... the data product
    a column                           ->  Symbol(kind="column")
"""
from __future__ import annotations

import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

from schema import (CONSUMES, HAS_COL, PRODUCES, READS_COL, WRITES_COL,
                    Edge, Index, Reference, Span, Symbol)

DIALECT = None   # sqlglot's permissive default parses most dialects


def _span(rel, node, src_lines):
    line = 1
    if node.meta.get("line"):
        line = node.meta["line"]
    return Span(rel, line, 0, line, 0)


def _stmt_name(stmt, i):
    """A statement's identity: the thing it creates, else its position."""
    if isinstance(stmt, exp.Create):
        t = stmt.this
        tbl = t.this if isinstance(t, exp.Schema) else t
        if isinstance(tbl, exp.Table):
            kind = (stmt.args.get("kind") or "TABLE").lower()
            return f"create_{kind}_{tbl.name}", tbl.name, kind
    if isinstance(stmt, exp.Insert):
        tbl = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
        if isinstance(tbl, exp.Table):
            return f"insert_into_{tbl.name}", tbl.name, "insert"
    return f"statement_{i}", None, "select"


def build_index(root: Path) -> Index:
    root = root.resolve()
    files = sorted(root.rglob("*.sql"))
    idx = Index(language="sql", root=root.name)
    seen = set()

    for f in files:
        idx.files[f.relative_to(root).as_posix()] = f.read_text(encoding="utf8")

    def node(kind, key, name, sp, detail=""):
        sid = f"{kind}::{key}"
        if sid not in seen:
            seen.add(sid)
            idx.symbols.append(Symbol(sid, name, key, kind, sp, sp, detail))
        return sid

    def add(a, b, kind, detail=""):
        if not any(x.from_symbol == a and x.to_symbol == b and x.kind == kind
                   for x in idx.edges):
            idx.edges.append(Edge(a, b, kind, detail))

    for f in files:
        rel = f.relative_to(root).as_posix()
        text = f.read_text(encoding="utf8")
        lines = text.split("\n")
        try:
            statements = sqlglot.parse(text, dialect=DIALECT)
        except Exception:
            continue

        # locate each statement's first line, for the card's body span
        offsets, cursor = [], 0
        for stmt in statements:
            if stmt is None:
                continue
            sql_txt = stmt.sql(dialect=DIALECT)
            head = sql_txt.split()[0] if sql_txt.split() else ""
            start = text.upper().find(head.upper(), cursor)
            start_line = text[:start].count("\n") + 1 if start >= 0 else 1
            cursor = max(cursor, start + 1)
            offsets.append(start_line)

        for i, stmt in enumerate(statements):
            if stmt is None:
                continue
            name, target, kind = _stmt_name(stmt, i)
            start_line = offsets[i]
            end_line = start_line + stmt.sql(pretty=True).count("\n")
            end_line = min(end_line, len(lines))
            # pull the comment block sitting above the statement as the doc
            doc, j = "", start_line - 2
            docs = []
            while j >= 0 and lines[j].strip().startswith("--"):
                docs.insert(0, lines[j].strip().lstrip("-").strip())
                j -= 1
            if docs:
                doc = docs[0]
                start_line = j + 2

            stmt_sid = f"{rel}::{name}"
            idx.symbols.append(Symbol(
                stmt_sid, name, name, "function",
                Span(rel, offsets[i], 0, offsets[i], 0),
                Span(rel, start_line, 0, end_line, 0), doc))

            # ---- tables written --------------------------------------------
            if target:
                d = node("dataset", target, target, Span(rel, offsets[i], 0, offsets[i], 0),
                         "view" if kind == "view" else "table")
                add(stmt_sid, d, PRODUCES, f"creates {kind}")

            # ---- tables read -----------------------------------------------
            select = stmt.find(exp.Select)
            read_tables = set()
            if select is not None:
                for t in stmt.find_all(exp.Table):
                    if t.name and t.name != target:
                        read_tables.add(t.name)
            for t in sorted(read_tables):
                d = node("dataset", t, t, Span(rel, offsets[i], 0, offsets[i], 0), "table")
                add(d, stmt_sid, CONSUMES, "reads")

            # ---- columns ----------------------------------------------------
            sp = Span(rel, offsets[i], 0, offsets[i], 0)

            # columns this statement PRODUCES (the projected output list)
            produced_cols = []
            if select is not None:
                star = any(isinstance(s, exp.Star) for s in select.selects)
                if star:
                    # honest: we know the table, not its columns
                    if target:
                        add(node("dataset", target, target, sp, "table"),
                            node("column", "*unknown*", "* (unresolved)", sp, "SELECT *"),
                            HAS_COL, "SELECT * — column list unknown")
                for s in select.selects:
                    if isinstance(s, exp.Star):
                        continue
                    alias = s.alias_or_name
                    if alias:
                        produced_cols.append(alias)

            # a CREATE TABLE with an explicit column DDL
            if isinstance(stmt, exp.Create) and isinstance(stmt.this, exp.Schema):
                for cdef in stmt.this.expressions:
                    if isinstance(cdef, exp.ColumnDef):
                        produced_cols.append(cdef.this.name)

            for c in produced_cols:
                cid = node("column", c, c, sp)
                add(stmt_sid, cid, WRITES_COL)
                if target:
                    add(node("dataset", target, target, sp, "table"), cid, HAS_COL)

            # columns this statement READS
            for c in stmt.find_all(exp.Column):
                if not c.name or c.name in produced_cols:
                    continue
                cid = node("column", c.name, c.name, sp)
                add(stmt_sid, cid, READS_COL)

            # ---- references: make table/column tokens clickable in the source
            for t in stmt.find_all(exp.Table):
                pass   # spans below are recovered by text scan

    _add_references(idx, root, files)
    return idx


def _add_references(idx, root, files):
    """Find the source spans of every table/column name so the card view can link them."""
    import re
    names = {s.name: s.id for s in idx.symbols if s.kind in ("dataset", "column")}
    stmts = [s for s in idx.symbols if s.kind == "function"]
    for f in files:
        rel = f.relative_to(root).as_posix()
        for ln, line in enumerate(idx.files[rel].split("\n"), start=1):
            code = line.split("--")[0]
            for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", code):
                nm = m.group(1)
                if nm not in names:
                    continue
                enclosing = None
                for s in stmts:
                    if s.body and s.body.start_line <= ln <= s.body.end_line \
                       and s.span.file == rel:
                        enclosing = s.id
                idx.references.append(Reference(
                    span=Span(rel, ln, m.start(1), ln, m.end(1)),
                    text=nm,
                    resolves_to=names[nm],
                    target_kind="project",
                    confidence=1.0,
                    enclosing=enclosing,
                ))


if __name__ == "__main__":
    import json
    idx = build_index(Path(sys.argv[1]))
    print(json.dumps(idx.stats(), indent=2))

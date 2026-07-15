#!/usr/bin/env python3
"""Independently verify every candidate fact WITHOUT using the codevis index.

This file exists to keep the eval honest. If the answer key were read off the
index, the index arm would win by construction and the whole exercise would be
theatre. So every answer here is recomputed from the raw source with stdlib `ast`
and plain text search — the same evidence a human reviewer would use.

Where this disagrees with the index, the index is wrong, not this file.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project").resolve()
FILES = sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in str(p))


def parse(p):
    return ast.parse(p.read_text(encoding="utf8"))


def all_functions():
    """(name, file, decorated, lineno) for every top-level/nested def."""
    out = []
    for f in FILES:
        for n in ast.walk(parse(f)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((n.name, f.relative_to(ROOT).as_posix(),
                            bool(n.decorator_list), n.lineno))
    return out


def name_occurrences(name):
    """Every textual occurrence of `name` as a whole word, minus its own def."""
    pat = re.compile(r"\b" + re.escape(name) + r"\b")
    hits = []
    for f in FILES:
        for i, line in enumerate(f.read_text(encoding="utf8").split("\n"), 1):
            if pat.search(line):
                hits.append((f.relative_to(ROOT).as_posix(), i, line.strip()))
    return hits


def fact_dead_functions():
    """A function nobody names anywhere except its own def line."""
    dead = []
    for name, file, decorated, lineno in all_functions():
        if decorated:
            continue                      # a decorator IS a use
        hits = [h for h in name_occurrences(name)
                if not (h[0] == file and h[1] == lineno)]
        if not hits:
            dead.append(f"{file}::{name}")
    return sorted(dead)


def fact_decorated_entry_points():
    """Functions invoked by a framework — they carry a decorator."""
    return sorted(f"{file}::{name}" for name, file, dec, _ in all_functions() if dec)


def fact_sql_tables():
    """Table names in CREATE TABLE statements inside string literals."""
    pat = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)",
                     re.I)
    tables = set()
    for f in FILES:
        tables.update(m.group(1).lower() for m in pat.finditer(f.read_text(encoding="utf8")))
    return sorted(tables)


def fact_files_touching(token):
    """Every file whose text contains `token` — the coupling question."""
    return sorted({f.relative_to(ROOT).as_posix() for f in FILES
                   if re.search(r"\b" + re.escape(token) + r"\b",
                                f.read_text(encoding="utf8"))})


def fact_direct_callers(name):
    """Files containing a CALL to `name` (ast, so not fooled by comments)."""
    out = set()
    for f in FILES:
        for n in ast.walk(parse(f)):
            if isinstance(n, ast.Call):
                fn = n.func
                nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if nm == name:
                    out.add(f.relative_to(ROOT).as_posix())
    return sorted(out)


if __name__ == "__main__":
    print(f"# independently verified from {ROOT.name} ({len(FILES)} python files)\n")
    print("dead functions (never named outside their own def):")
    for d in fact_dead_functions():
        print("   ", d)
    print(f"\ndecorated (framework-invoked) functions: {len(fact_decorated_entry_points())}")
    for e in fact_decorated_entry_points()[:5]:
        print("   ", e)
    print("\nsql tables created:")
    for t in fact_sql_tables():
        print("   ", t)
    print("\nfiles touching 'petal_ratio':", fact_files_touching("petal_ratio") or "(none)")
    print("files touching 'recompute'  :", fact_files_touching("recompute"))
    print("direct callers of recompute :", fact_direct_callers("recompute"))

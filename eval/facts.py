#!/usr/bin/env python3
"""The question set and its answer key — derived from the SOURCE, never the index.

Design rules, in order of importance:

1. Every answer is a SET of strings. Scoring is then precision/recall/F1 —
   arithmetic, not taste. Neither I nor the model can argue the result.
2. Every answer is recomputed here with stdlib `ast` and text search. If the key
   came from the codevis index, the index arm would win by construction.
3. Every question needs EXHAUSTIVE cross-file resolution to answer correctly.
   "What does this function do" is not a question — reading answers it. "Which
   files call it" is, because you must check files you had no reason to open.

That third rule is the actual hypothesis under test: a 20k-token repo fits in the
context window, but fitting is not attending. A model asked about scoring.py has
no reason to open migrate_excel.py — and recompute() is called from there.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def load(root: Path):
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))
    rel = lambda f: f.relative_to(root).as_posix()
    trees = {rel(f): ast.parse(f.read_text(encoding="utf8")) for f in files}
    texts = {rel(f): f.read_text(encoding="utf8") for f in files}
    return trees, texts


def _defs(trees):
    d = defaultdict(list)
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                d[n.name].append((rel, n.lineno, bool(n.decorator_list)))
    return d


def _callers(trees, names):
    c = defaultdict(set)
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if nm in names:
                    c[nm].add(rel)
    return c


def build(root: Path):
    trees, texts = load(root)
    defs = _defs(trees)
    callers = _callers(trees, set(defs))
    Q = []

    # ---- 1. dead code: needs a check of EVERY file, not the plausible ones ----
    dead = []
    for name, places in defs.items():
        if any(dec for _, _, dec in places):
            continue                                   # a decorator is a use
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        uses = 0
        for rel, txt in texts.items():
            for i, line in enumerate(txt.split("\n"), 1):
                if pat.search(line) and not any(rel == p and i == ln for p, ln, _ in places):
                    uses += 1
        if uses == 0:
            dead.append(f"{places[0][0]}::{name}")
    Q.append({
        "id": "dead_code",
        "question": "Which functions in this project are never called or referenced "
                    "anywhere in the codebase? List each as 'file.py::function_name'. "
                    "A function used only via a decorator does NOT count as dead.",
        "answer": sorted(dead),
        "why": "Requires checking every file for every function. Missing one file "
               "produces a false 'dead' claim — a wrong answer, not a gap."
    })

    # ---- 2/3. distant callers: the 'unrelated script' case --------------------
    single = {n: p[0][0] for n, p in defs.items() if len(p) == 1}
    ranked = sorted(
        ((len(callers[n] - {home}), n, home) for n, home in single.items()),
        reverse=True)
    for count, name, home in ranked[:3]:
        if count < 2:
            continue
        Q.append({
            "id": f"callers_{name}",
            "question": f"The function `{name}` is defined in `{home}`. "
                        f"Which files in this project call it? List file paths only.",
            "answer": sorted(callers[name] - {home}),
            "why": f"{name} is called from files with no topical connection to "
                   f"{home}. Nothing about the question points you at them."
        })

    # ---- 4. schema hidden in string literals ---------------------------------
    pat = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)", re.I)
    tables = sorted({m.group(1).lower() for t in texts.values() for m in pat.finditer(t)})
    Q.append({
        "id": "sql_tables",
        "question": "This project uses SQLite. Which database tables does it create? "
                    "List the table names only.",
        "answer": tables,
        "why": "The schema exists only inside SQL string literals passed to "
               "cursor.execute(). It is code hiding in text."
    })

    # ---- 5. framework entry points -------------------------------------------
    entries = sorted(f"{rel}::{name}"
                     for name, places in defs.items()
                     for rel, _, dec in places if dec)
    Q.append({
        "id": "entry_points",
        "question": "Which functions are invoked by a web framework or at import "
                    "time, rather than being called by other code in this project? "
                    "List each as 'file.py::function_name'.",
        "answer": entries,
        "why": "A call graph cannot see these. Calling them dead is confidently wrong."
    })

    return {
        "root": root.name,
        "generated_by": "eval/facts.py — from source only, never the codevis index",
        "questions": Q
    }


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project").resolve()
    outdir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).parent
    outdir.mkdir(parents=True, exist_ok=True)
    spec = build(root)
    out = outdir / "facts.json"
    out.write_text(json.dumps(spec, indent=2), encoding="utf8")
    for q in spec["questions"]:
        print(f"[{q['id']}] {len(q['answer'])} item(s)")
        print(f"    {q['question'][:88]}")
        print(f"    -> {', '.join(q['answer'][:4])}{' …' if len(q['answer'])>4 else ''}")
    print(f"\nwrote {out}")

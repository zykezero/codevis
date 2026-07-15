#!/usr/bin/env python3
"""The question set and its answer key — derived from the SOURCE, never the index.

Design rules, in order of importance:

1. Every answer is a SET of strings. Scoring is then precision/recall/F1 —
   arithmetic, not taste. Neither I nor the model can argue the result.
2. Every answer is recomputed here with stdlib `ast` (plus a text search for the
   SQL-in-strings question, where the schema only exists as text). If the key
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

# Discovery only — the shared exclusion list (.venv, node_modules, …), NOT the
# index. With an in-tree virtualenv, rglob dragged all of site-packages into
# both arms and the "~5x less text" framing silently inverted.
HERE = Path(__file__).resolve().parent
for _cand in (HERE.parent, HERE.parent / "indexer"):
    if (_cand / "codevis.py").exists():
        sys.path.insert(0, str(_cand / "spike"))
        break
from schema import read_source, source_files  # noqa: E402


def load(root: Path):
    rel = lambda f: f.relative_to(root).as_posix()
    trees, texts = {}, {}
    for f in source_files(root, ".py"):
        t = read_source(f)
        if t is None:
            continue
        trees[rel(f)] = ast.parse(t)
        texts[rel(f)] = t
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
    # "Used" means a REAL reference in code — a Name, an attribute access, or an
    # import alias — walked from the AST. The old text search counted a function
    # named only in a comment or docstring as alive, which made the key not
    # actually ground truth: a model correctly calling it dead was marked wrong.
    used = set()
    for tree in trees.values():
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute):
                used.add(n.attr)
            elif isinstance(n, ast.alias):
                used.add(n.name.split(".")[0])
                if n.asname:
                    used.add(n.asname)
    dead = []
    for name, places in defs.items():
        if any(dec for _, _, dec in places):
            continue                                   # a decorator is a use
        if name not in used:
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
            # The key is `callers - {home}`, so the question must SAY that. It
            # used to ask "which files call it" while quietly excluding the
            # defining file — and `backup.py` genuinely calls its own
            # `snapshot()` from a __main__ block, so an arm that answered
            # correctly was marked spurious. An answer key is only ground truth
            # if the question asks for what it measures.
            "question": f"The function `{name}` is defined in `{home}`. "
                        f"Which OTHER files in this project call it? List file "
                        f"paths only, and do not include `{home}` itself.",
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
        # The key is decorated functions only. The question used to also say "or
        # at import time", which invited listing `main()` under a __main__ block
        # — never in the key, so a reasonable answer scored as an invention.
        # Import-time calls are now ordinary calls in the index (the module body
        # is their caller), so the framework clause is the whole question.
        "question": "Which functions are invoked by a web framework, rather than "
                    "being called by other code in this project? "
                    "List each as 'file.py::function_name'.",
        "answer": entries,
        "why": "A call graph cannot see these — the framework calls them, so they "
               "have no in-project caller. Calling them dead is confidently wrong."
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

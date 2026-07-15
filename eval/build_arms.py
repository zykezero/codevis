#!/usr/bin/env python3
"""Build the two context bundles. Same questions, same model — only context differs.

  ARM A "raw"   — every source file, verbatim. The honest strong baseline: at this
                  size it fits in the window, so the model CAN read everything.
                  The hypothesis is that it won't attend to what it has no reason
                  to look at.

  ARM B "index" — the codevis index rendered as facts: symbols, call edges with
                  their sites, data products, entry-point flags. NO source bodies.
                  Strictly less information in bytes; strictly more in structure.

That asymmetry is the point. If the index arm wins while carrying less text, the
win came from structure, not from being fed more.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The indexer sits beside this file in the repo, but inside the packaged extension
# it lives at <ext>/indexer/. Look in both rather than assume a layout.
for cand in (HERE.parent, HERE.parent / "indexer"):
    if (cand / "codevis.py").exists():
        sys.path.insert(0, str(cand / "spike"))
        sys.path.insert(0, str(cand))
        break
else:
    raise SystemExit(f"could not find codevis.py near {HERE}")
import codevis  # noqa: E402


def arm_raw(root: Path) -> str:
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))
    parts = [f"# Project: {root.name}", f"# {len(files)} Python files, verbatim.\n"]
    for f in files:
        parts.append(f"\n===== FILE: {f.relative_to(root).as_posix()} =====")
        parts.append(f.read_text(encoding="utf8"))
    return "\n".join(parts)


def arm_index(idx) -> str:
    """The graph as prose facts. No bodies — structure only."""
    P = [f"# Project: {idx.root}",
         "# A resolved code graph. Every edge below was produced by static analysis",
         "# (Jedi for name resolution) and is exhaustive over the whole project.\n"]

    by_file = {}
    for s in idx.symbols:
        if s.kind in ("function", "method", "class"):
            by_file.setdefault(s.span.file, []).append(s)

    P.append("## Symbols")
    for f in sorted(by_file):
        P.append(f"\n### {f}")
        for s in sorted(by_file[f], key=lambda x: x.span.start_line):
            tags = []
            if s.entry:
                tags.append("ENTRY POINT — invoked by a framework or at import time, "
                            "not called by project code")
            P.append(f"- `{s.signature or s.name}` (line {s.span.start_line})"
                     + (f" — {s.doc}" if s.doc else ""))
            for t in tags:
                P.append(f"    - {t}")

    P.append("\n## Call graph (exhaustive)")
    calls = [e for e in idx.edges if e.kind == "calls"]
    tgt = {}
    for e in calls:
        tgt.setdefault(e.to_symbol, []).append(e)
    name = {s.id: s for s in idx.symbols}
    for sid, es in sorted(tgt.items()):
        s = name.get(sid)
        if not s:
            continue
        froms = sorted({name[e.from_symbol].span.file + "::" + name[e.from_symbol].name
                        for e in es if e.from_symbol in name})
        P.append(f"- `{s.span.file}::{s.name}` is called by: {', '.join(froms)}")

    never = [s for s in idx.symbols
             if s.kind in ("function", "method")
             and s.id not in tgt and not s.entry]
    P.append("\n## Never called by any project code, and not an entry point")
    P.extend(f"- `{s.span.file}::{s.name}`" for s in sorted(never, key=lambda x: x.id)) \
        if never else P.append("- (none)")

    ds = [s for s in idx.symbols if s.kind == "dataset"]
    if ds:
        P.append("\n## Data products (tables/files), and who touches them")
        for d in sorted(ds, key=lambda x: x.name):
            prod = [name[e.from_symbol].name for e in idx.edges
                    if e.kind == "produces" and e.to_symbol == d.id and e.from_symbol in name]
            cons = [name[e.to_symbol].name for e in idx.edges
                    if e.kind == "consumes" and e.from_symbol == d.id and e.to_symbol in name]
            P.append(f"- `{d.name}` ({d.doc or 'data'}) — written by: "
                     f"{', '.join(sorted(set(prod))) or '—'}; read by: "
                     f"{', '.join(sorted(set(cons))) or '—'}")
    return "\n".join(P)


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project").resolve()
    outdir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else HERE
    outdir.mkdir(parents=True, exist_ok=True)
    idx = codevis.build(root)
    facts = json.loads((outdir / "facts.json").read_text(encoding="utf8"))

    arms = {
        "root": root.name,
        "questions": facts["questions"],
        "arms": {
            "raw":   {"label": "every source file, verbatim", "context": arm_raw(root)},
            "index": {"label": "codevis resolved graph, no source bodies",
                      "context": arm_index(idx)},
        },
    }
    out = outdir / "arms.json"
    out.write_text(json.dumps(arms, indent=2), encoding="utf8")

    for k, v in arms["arms"].items():
        n = len(v["context"])
        print(f"  {k:<6} {n:>7,} chars  (~{n//4:>6,} tokens)  {v['label']}")
    r, i = (len(arms['arms'][k]['context']) for k in ('raw', 'index'))
    print(f"\n  the index arm carries {100 - i*100//r}% LESS text than the baseline.")
    print(f"  wrote {out}")

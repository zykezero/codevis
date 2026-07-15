"""Git integration: what changed, and what does the change threaten?

Two steps, deliberately separate:

  1. CHANGED  — purely mechanical. `git diff` gives changed line ranges; a symbol
     is changed if the diff touches its body span.

  2. IMPACTED — the interesting part. A change propagates along a DIRECTED
     "depends-on" graph, which is NOT the same as the call graph:

        A calls B      ->  B changed means A is impacted   (edge B -> A)
        F produces D   ->  F changed means D is impacted   (edge F -> D)
        D consumes ->F ->  D changed means F is impacted   (edge D -> F)
        F writes col C ->  F changed means C is impacted   (edge F -> C)
        F reads col C  ->  C changed means F is impacted   (edge C -> F)

     Note the last two point OPPOSITE ways. That asymmetry is the whole point:
     it is what lets a change to a SQL column reach a Python function that never
     calls anything in that file.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def is_repo(root: Path) -> bool:
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                       capture_output=True, text=True)
    return r.returncode == 0


def changed_lines(root: Path, ref: str = "HEAD"):
    """{relpath: {line numbers touched}}. Includes untracked files in full.

    `--relative` makes the diff paths relative to `root` (the indexed folder),
    not the repository root. Symbol spans are workspace-relative, so without it
    a workspace that is a SUBDIRECTORY of its repo (monorepos) matched nothing
    and the blast radius silently reported "nothing changed".
    """
    out = {}
    r = subprocess.run(
        ["git", "-C", str(root), "diff", "--unified=0", "--no-color",
         "--relative", ref],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git diff failed: {r.stderr.strip()}")

    cur = None
    for line in r.stdout.split("\n"):
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
            out.setdefault(cur, set())
            continue
        m = HUNK.match(line)
        if m and cur:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            for i in range(start, start + max(count, 1)):
                out[cur].add(i)

    # brand-new files are entirely "changed"
    u = subprocess.run(["git", "-C", str(root), "ls-files", "--others",
                        "--exclude-standard"], capture_output=True, text=True)
    for f in u.stdout.split("\n"):
        f = f.strip()
        if not f:
            continue
        p = root / f
        if p.exists() and p.suffix in (".py", ".R", ".sql"):
            out[f] = set(range(1, len(p.read_text(encoding="utf8", errors="ignore")
                                      .split("\n")) + 1))
    return {k: v for k, v in out.items() if v}


def mark_changed(idx, touched):
    """A symbol is changed if the diff lands inside its body."""
    n = 0
    for s in idx.symbols:
        if s.kind in ("dataset", "column") or not s.body:
            continue
        lines = touched.get(s.body.file)
        if not lines:
            continue
        if any(s.body.start_line <= ln <= s.body.end_line for ln in lines):
            s.changed = True
            n += 1
    return n


def _impact_edges(idx):
    """The directed 'a change here reaches there' graph (see module docstring)."""
    adj = {}
    def link(a, b, why):
        adj.setdefault(a, []).append((b, why))

    for e in idx.edges:
        if e.kind in ("calls", "reads"):
            link(e.to_symbol, e.from_symbol, e.kind)          # callee -> caller
        elif e.kind == "produces":
            link(e.from_symbol, e.to_symbol, e.kind)          # writer -> data
        elif e.kind == "consumes":
            link(e.from_symbol, e.to_symbol, e.kind)          # data -> reader
        elif e.kind == "writes_column":
            link(e.from_symbol, e.to_symbol, e.kind)          # writer -> column
        elif e.kind == "reads_column":
            link(e.to_symbol, e.from_symbol, e.kind)          # column -> reader
        # `has_column` is deliberately NOT an impact edge.
        #
        # Column nodes are GLOBAL BY NAME — that is what makes them connective in
        # the web view (two files touching `petal_ratio` get a line between them).
        # But it means `petal_length` is one node shared by clean_iris AND
        # processed_iris. Propagating column -> dataset therefore walks BACKWARDS
        # into upstream tables that were only ever inputs: editing processed_iris
        # would "impact" raw_iris. That is a false alarm, and a blast radius that
        # cries wolf is a blast radius nobody reads.
        #
        # Impact travels column -> READERS OF THAT COLUMN, and no further.
    return adj


def blast_radius(idx, max_hops=6):
    """Hop distance from the nearest changed symbol. 0 = changed itself."""
    adj = _impact_edges(idx)
    dist = {s.id: 0 for s in idx.symbols if getattr(s, "changed", False)}
    frontier = list(dist)
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        nxt = []
        for cur in frontier:
            for nb, _why in adj.get(cur, ()):
                if nb not in dist:
                    dist[nb] = hop
                    nxt.append(nb)
        frontier = nxt

    for s in idx.symbols:
        s.impact = dist.get(s.id, -1)
    return dist

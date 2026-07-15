#!/usr/bin/env python3
"""codevis — index a folder and emit a self-contained, navigable HTML view.

    python codevis.py fixtures/python_demo -o out/python.html
    python codevis.py fixtures/r_demo      -o out/r.html

Language is detected from the files present. Front-ends are per-language
(design notes D12); everything downstream of the index schema is language-blind.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "spike"))

import gitimpact        # noqa: E402
from schema import Index, source_files  # noqa: E402

# Front-ends are loaded LAZILY, and only for languages actually present.
#
# Each language has its own dependency, and they must not be each other's problem:
#   python -> jedi                  (stdlib `ast` does the spans)
#   r      -> tree_sitter_languages (nothing else uses it)
#   sql    -> sqlglot
#
# Importing all three up front meant a missing R grammar broke indexing for a
# pure-Python project that contains no R at all. That is the same class of mistake
# as the false links: making a hard requirement out of weak evidence.
FRONTENDS = {
    "python": ("frontend_python", ".py", "jedi"),
    "r":      ("frontend_r", ".r", "tree_sitter_language_pack"),
    "sql":    ("frontend_sql", ".sql", "sqlglot"),
}

PIP_HINT = {
    "jedi": "jedi",
    "sqlglot": "sqlglot",
    # Was tree_sitter_languages, which is abandoned: its last release predates
    # Python 3.12, so it cannot be installed on any current interpreter and R
    # was effectively dead. tree_sitter_language_pack is the maintained
    # successor and ships a NEWER r grammar with different node names — see the
    # adapters at the top of frontend_r.py.
    "tree_sitter_language_pack": "tree_sitter_language_pack",
}


class MissingDependency(Exception):
    def __init__(self, lang, module, exc):
        self.lang, self.module = lang, module
        super().__init__(
            f"indexing {lang} needs the '{module}' package: {exc}\n"
            f"    pip install {PIP_HINT.get(module, module)}")


def load_frontend(lang):
    import importlib
    mod_name, _ext, dep = FRONTENDS[lang]
    try:
        return importlib.import_module(mod_name).build_index
    except ImportError as e:
        raise MissingDependency(lang, dep, e)


def present(root: Path):
    """Every language with source under this root — a project can be mixed.

    Uses the same discovery as the front-ends (schema.source_files), so a
    language that only appears inside `.venv`/`node_modules` is not "present".
    """
    out = []
    for lang, (_mod, ext, _dep) in FRONTENDS.items():
        if source_files(root, ext):
            out.append(lang)
    if not out:
        raise SystemExit(f"no indexable source found under {root}")
    return out


def build(root: Path, lang: str | None = None):
    """Index every language present and MERGE into one index.

    The merge is the whole point. `dataset` and `column` node ids are global keys
    (`dataset::processed_iris`), not file-scoped ones — so a table CREATEd in a .sql
    file and read by `pd.read_sql` in a .py file collapse into a SINGLE node, and
    the graph spans languages. Code symbols stay file-scoped and never collide.
    """
    langs = [lang] if lang else present(root)

    parts, skipped = [], []
    for l in langs:
        try:
            parts.append(load_frontend(l)(root))
        except MissingDependency as e:
            if lang:
                raise SystemExit(str(e))        # explicitly asked for it -> hard fail
            skipped.append(e)                   # auto-detected -> skip, but say so
    if not parts:
        raise SystemExit("\n".join(str(e) for e in skipped)
                         or f"no indexable source found under {root}")
    for e in skipped:
        print(f"warning: skipping {e.lang} files — {e.module} is not installed",
              file=sys.stderr)

    if len(parts) == 1:
        p = parts[0]
        p.langs = {f: p.language for f in p.files}
        return p

    merged = Index(language="multi", root=root.name)
    seen_sym, seen_edge = {}, set()
    for p in parts:
        merged.files.update(p.files)
        for f in p.files:
            merged.langs[f] = p.language
        for s in p.symbols:
            if s.id in seen_sym:
                # same data product seen from two languages — keep the richer record
                if not seen_sym[s.id].doc and s.doc:
                    seen_sym[s.id].doc = s.doc
                continue
            seen_sym[s.id] = s
            merged.symbols.append(s)
        merged.references.extend(p.references)
        for e in p.edges:
            k = (e.from_symbol, e.to_symbol, e.kind)
            if k not in seen_edge:
                seen_edge.add(k)
                merged.edges.append(e)
    return merged


def render(index, out: Path):
    """Assemble the standalone page. Two rules keep INDEXED SOURCE from becoming
    executable markup in it:

    1. Escape `<` in the JSON payload as \\u003c (identical bytes after JS string
       parsing). The HTML parser ends a <script> element at the literal text
       "</script>" REGARDLESS of JS string context, so any indexed file that
       contains that text would otherwise break out of the block and run.
    2. Substitute all template tokens in ONE regex pass. Chained str.replace
       rescans earlier substitutions — and the payload legitimately contains
       "__APP__" whenever the indexed project does (this repo does, right here) —
       so a chained replace would splice app.js into the middle of the data.

    The template's CSP (nonce'd script blocks only) is the backstop for both.
    """
    import html as html_mod
    import re as re_mod
    import secrets

    tpl = (HERE / "viewer" / "template.html").read_text(encoding="utf8")
    app = (HERE / "viewer" / "app.js").read_text(encoding="utf8")
    nonce = secrets.token_urlsafe(16)
    parts = {
        "__NONCE__": nonce,
        "__ROOT__": html_mod.escape(index.root),
        "__INDEX__": json.dumps(index.to_dict()).replace("<", "\\u003c"),
        "__APP__": app,
    }
    html = re_mod.sub(r"__(?:NONCE|ROOT|INDEX|APP)__", lambda m: parts[m.group(0)], tpl)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--lang", choices=list(FRONTENDS), default=None)
    ap.add_argument("--json", action="store_true", help="also write the raw index")
    ap.add_argument("--emit-json", action="store_true",
                    help="print the index to stdout and exit (used by the VS Code extension)")
    ap.add_argument("--diff", metavar="REF", nargs="?", const="HEAD", default=None,
                    help="mark symbols changed vs a git ref, and compute the blast radius")
    a = ap.parse_args()

    root = Path(a.folder).resolve()
    idx = build(root, a.lang)

    if a.diff is not None and a.emit_json:
        # keep stdout pure JSON for the extension
        if gitimpact.is_repo(root):
            touched = gitimpact.changed_lines(root, a.diff)
            gitimpact.mark_changed(idx, touched)
            gitimpact.blast_radius(idx)
            idx.diff_ref = a.diff
    elif a.diff is not None:
        if not gitimpact.is_repo(root):
            raise SystemExit(f"{root} is not a git repository — cannot diff")
        touched = gitimpact.changed_lines(root, a.diff)
        n = gitimpact.mark_changed(idx, touched)
        dist = gitimpact.blast_radius(idx)
        idx.diff_ref = a.diff
        hops = {}
        for sid, d in dist.items():
            hops[d] = hops.get(d, 0) + 1
        print(f"diff vs {a.diff}: {n} symbol(s) changed in "
              f"{len(touched)} file(s)")
        for d in sorted(hops):
            label = "changed" if d == 0 else f"{d} hop{'s' if d > 1 else ''} away"
            print(f"  {label:<14} {hops[d]}")
    if a.emit_json:
        print(json.dumps(idx.to_dict()))
        return

    out = Path(a.out) if a.out else HERE / "out" / f"{root.name}.html"
    render(idx, out)

    s = idx.stats()
    langs = sorted(set(idx.langs.values())) or [idx.language]
    print(f"indexed {root.name} [{', '.join(langs)}]")
    print(f"  {s['symbols']} symbols · {s['references']} references · {s['edges']} edges")
    print(f"  linked: {s['by_target'].get('project', 0)}   "
          f"unresolved: {s['by_target'].get('unresolved', 0)}")
    if a.json:
        j = out.with_suffix(".index.json")
        j.write_text(json.dumps(idx.to_dict(), indent=2), encoding="utf8")
        print(f"  index -> {j}")
    print(f"  view  -> {out}")


if __name__ == "__main__":
    main()

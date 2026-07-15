"""Python front-end: Jedi does the name resolution, we just shape the output."""
from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path

import jedi

import dataflow_py
from schema import Edge, Index, Param, Reference, Span, Symbol, read_source, source_files

DEF_KINDS = {"function": "function", "class": "class", "statement": "variable"}


def _span(path, name):
    line, col = name.line, name.column
    return Span(str(path), line, col, line, col + len(name.name))


def _unparse(node):
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _signature(fn):
    """Render a real signature, including *, /, *args, **kwargs and defaults."""
    a = fn.args
    parts, params = [], []

    def add(arg, default=None, prefix=""):
        ann = _unparse(arg.annotation) if arg.annotation else ""
        dflt = _unparse(default) if default is not None else ""
        txt = prefix + arg.arg
        if ann:
            txt += f": {ann}"
        if dflt:
            txt += f" = {dflt}" if ann else f"={dflt}"
        parts.append(txt)
        params.append(Param(arg.arg, ann, dflt))

    posonly = list(getattr(a, "posonlyargs", []))
    args = list(a.args)
    defaults = list(a.defaults)
    pad = [None] * ((len(posonly) + len(args)) - len(defaults))
    dmap = pad + defaults

    for i, arg in enumerate(posonly):
        add(arg, dmap[i])
    if posonly:
        parts.append("/")
    for i, arg in enumerate(args):
        add(arg, dmap[len(posonly) + i])
    if a.vararg:
        add(a.vararg, None, "*")
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        add(arg, d)
    if a.kwarg:
        add(a.kwarg, None, "**")

    ret = f" -> {_unparse(fn.returns)}" if getattr(fn, "returns", None) else ""
    return f"{fn.name}({', '.join(parts)}){ret}", params


def build_index(root: Path) -> Index:
    root = root.resolve()
    project = jedi.Project(root)
    # read each file exactly once; an undecodable file is skipped, not fatal
    texts = {}
    for f in source_files(root, ".py"):
        t = read_source(f)
        if t is not None:
            texts[f] = t
    files = sorted(texts)
    idx = Index(language="python", root=root.name)
    for f in files:
        idx.files[f.relative_to(root).as_posix()] = texts[f]

    # ---- pass 1: definitions (from ast — authoritative about what THIS file defines)
    # Jedi's get_names(definitions=True) also reports imported names as definitions,
    # which manufactures a phantom `log_step` symbol in every file that imports it.
    sym_by_loc = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            tree = ast.parse(texts[f])
        except SyntaxError:
            continue
        src_lines = texts[f].split("\n")

        def visit(node, prefix, cls=None):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    is_cls = isinstance(child, ast.ClassDef)
                    kind = "class" if is_cls else ("method" if cls else "function")
                    qual = f"{prefix}.{child.name}" if prefix else child.name
                    sid = f"{rel}::{qual}"

                    kw = "class " if is_cls else ("async def " if isinstance(child, ast.AsyncFunctionDef) else "def ")
                    name_col = child.col_offset + len(kw)
                    name_span = Span(rel, child.lineno, name_col,
                                     child.lineno, name_col + len(child.name))

                    body_start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    body = Span(rel, body_start, 0, child.end_lineno or child.lineno, 0)
                    doc = (ast.get_docstring(child) or "").strip().split("\n")[0]

                    if is_cls:
                        sig, params = child.name, []
                    else:
                        sig, params = _signature(child)
                    snippet = "\n".join(src_lines[body_start - 1:child.end_lineno])
                    chash = hashlib.sha1(
                        re.sub(r"\s+", " ", snippet).strip().encode()).hexdigest()[:12]

                    # A decorated function is called BY THE FRAMEWORK. Reporting
                    # `@app.get("/api/videos")` as "no callers / dead code" is worse
                    # than useless — it is confidently wrong.
                    entry = bool(child.decorator_list) and not is_cls

                    idx.symbols.append(Symbol(
                        sid, child.name, qual, kind, name_span, body, doc, entry,
                        signature=sig, params=params, content_hash=chash))
                    # jedi's goto() reports the identifier position — key on that
                    sym_by_loc[(str(f), child.lineno, name_col)] = sid
                    visit(child, qual, cls=is_cls or cls)

                elif isinstance(child, ast.Assign) and node is tree:
                    for t in child.targets:
                        if isinstance(t, ast.Name):
                            sid = f"{rel}::{t.id}"
                            sp = Span(rel, t.lineno, t.col_offset,
                                      t.lineno, t.col_offset + len(t.id))
                            idx.symbols.append(Symbol(sid, t.id, t.id, "variable", sp, sp, ""))
                            sym_by_loc[(str(f), t.lineno, t.col_offset)] = sid

        visit(tree, "")

    # ---- pass 2: references --------------------------------------------------
    for f in files:
        script = jedi.Script(path=str(f), project=project)
        rel_s = f.relative_to(root).as_posix()
        # enclosing function = innermost ast symbol whose body contains the line
        scopes = [
            (s.body.start_line, s.body.end_line, s.id)
            for s in idx.symbols
            if s.body and s.span.file == rel_s and s.kind in ("function", "method")
        ]

        def enclosing_of(line):
            hits = [(a, sid) for a, b, sid in scopes if a <= line <= b]
            return max(hits, key=lambda t: t[0])[1] if hits else None

        for n in script.get_names(all_scopes=True, definitions=False, references=True):
            if n.is_definition():
                continue
            ref = Reference(
                # rel_s, not f.relative_to(root): str(WindowsPath) yields
                # backslashes, and every other file key in the index is POSIX.
                # On Windows the mismatch orphaned every reference span.
                span=_span(rel_s, n),
                text=n.name,
                enclosing=enclosing_of(n.line),
            )
            try:
                targets = n.goto(follow_imports=True, follow_builtin_imports=False)
            except Exception:
                targets = []

            if targets:
                t = targets[0]
                tp = t.module_path
                if tp and root in Path(tp).resolve().parents or (tp and Path(tp).resolve() == f):
                    key = (str(Path(tp).resolve()), t.line, t.column)
                    sid = sym_by_loc.get(key)
                    if sid:
                        ref.resolves_to = sid
                        ref.target_kind = "project"
                        ref.confidence = 1.0
                    else:
                        # resolved inside the project but to a local/param, not a top-level symbol
                        ref.target_kind = "local"
                        ref.confidence = 0.9
                elif tp:
                    ref.target_kind = "external"   # stdlib / site-packages
                    ref.confidence = 0.8
                else:
                    ref.target_kind = "external"   # builtins
                    ref.confidence = 0.6
            idx.references.append(ref)

            if ref.target_kind == "project" and ref.enclosing and ref.resolves_to:
                tgt = next((s for s in idx.symbols if s.id == ref.resolves_to), None)
                kind = "reads" if tgt and tgt.kind == "variable" else "calls"
                prev = next((e for e in idx.edges
                             if e.from_symbol == ref.enclosing
                             and e.to_symbol == ref.resolves_to and e.kind == kind), None)
                if prev:
                    prev.call_sites.append(ref.span)      # same edge, another call site
                else:
                    e = Edge(ref.enclosing, ref.resolves_to, kind)
                    e.call_sites.append(ref.span)
                    idx.edges.append(e)

    # a symbol referenced from module level (a __main__ block, a route table, a
    # registration call) has no in-project caller but is not dead either
    module_level = {r.resolves_to for r in idx.references
                    if r.enclosing is None and r.resolves_to}
    for s in idx.symbols:
        if s.id in module_level and s.kind in ("function", "method"):
            s.entry = True

    # ---- pass 3: dataflow (datasets + columns) -------------------------------
    dataflow_py.extract(idx, root, files, {s.id for s in idx.symbols})
    return idx


if __name__ == "__main__":
    import json
    idx = build_index(Path(sys.argv[1]))
    print(json.dumps(idx.stats(), indent=2))

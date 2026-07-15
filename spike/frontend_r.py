"""R front-end: tree-sitter spans + a best-effort scope resolver.

No LSP here (no R runtime in this sandbox), which is the point: this is the
*weak* front-end. If the schema (D12) can absorb it alongside Jedi's output
without the downstream caring, the seam is real.

R's `source()`-into-global-env model is unusually kind to static resolution:
cross-file linking is just a global name table.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tree_sitter_languages import get_parser

import dataflow_r
from schema import Edge, Index, Reference, Span, Symbol

PARSER = get_parser("r")

# base R + tidyverse names we should not report as broken links
KNOWN_EXTERNAL = {
    "library", "suppressPackageStartupMessages", "c", "list", "function", "if", "else",
    "for", "return", "cat", "sprintf", "paste", "setdiff", "names", "nrow", "ncol",
    "length", "vapply", "lapply", "sapply", "is.null", "abort", "stop", "mean", "sd",
    "scale", "which.max", "max", "min", "abs", "sort", "unique", "seq_len", "sum",
    "tolower", "trimws", "gsub", "attr", "do.call", "rbind", "as.data.frame", "print",
    "read_csv", "write_csv", "clean_names", "drop_na", "all_of", "any_of", "across",
    "where", "mutate", "filter", "select", "arrange", "desc", "group_by", "summarise",
    "row_number", "pivot_longer", "everything", "reduce", "regmatches", "regexec",
    "nzchar", "complete.cases", "rownames", "is.numeric", "character", "logical",
    "numeric", "data.frame", "aggregate", "normalizePath", "file.path", "NA_character_",
    "NA_real_", "TRUE", "FALSE", "NULL", "NA", "Reduce", "as.numeric", "%>%", ".data",
}


def _txt(src, n):
    return src[n.start_byte:n.end_byte].decode("utf8", "replace")


def _span(rel, n):
    return Span(rel, n.start_point[0] + 1, n.start_point[1],
                n.end_point[0] + 1, n.end_point[1])


def _walk(n):
    yield n
    for c in n.children:
        yield from _walk(c)


def _preceding_doc(src, node, lines):
    """Grab #' roxygen comments immediately above a definition."""
    ln = node.start_point[0]
    out = []
    i = ln - 1
    while i >= 0 and lines[i].strip().startswith("#'"):
        out.insert(0, lines[i].strip().lstrip("#'").strip())
        i -= 1
    return out[0] if out else ""


NSE_VERBS = {
    "mutate", "filter", "select", "arrange", "group_by", "summarise", "summarize",
    "across", "drop_na", "pivot_longer", "all_of", "any_of", "where", "desc", "aes",
}


def _in_nse_call(src, node):
    """Tidyverse non-standard evaluation: `mutate(sepal_ratio = a / b)` names COLUMNS.

    Those names exist only in the data, never as code symbols — so they must be
    classified as `data`, not left in the unresolved bucket where they would render
    as dead links all over the UI. This is the one place R is meaningfully harder
    than Python for a static linker.
    """
    p = node.parent
    while p is not None:
        if p.type == "call" and p.children and p.children[0].type == "identifier":
            if _txt(src, p.children[0]) in NSE_VERBS:
                return True
        p = p.parent
    return False


def _is_package_arg(src, node):
    """`library(dplyr)` — dplyr is a package name, not an unresolved symbol."""
    p = node.parent
    while p is not None:
        if p.type == "call" and p.children and p.children[0].type == "identifier":
            return _txt(src, p.children[0]) in ("library", "require", "requireNamespace",
                                                "loadNamespace", "suppressPackageStartupMessages")
        p = p.parent
    return False


def build_index(root: Path) -> Index:
    root = root.resolve()
    files = sorted(root.rglob("*.R"))
    idx = Index(language="r", root=root.name)
    for f in files:
        idx.files[f.relative_to(root).as_posix()] = f.read_text(encoding="utf8")

    trees, sources = {}, {}
    for f in files:
        src = f.read_bytes()
        sources[f] = src
        trees[f] = PARSER.parse(src)

    # ---- pass 1: global definitions (R sources into one global env) -----------
    # R has no imports: source() drops everything into one environment, so a global
    # name table IS the resolver. But that assumption breaks when a project holds
    # INDEPENDENT entry points that are never sourced together — `PROCESSED` is
    # defined separately in publish.R, report.R and qa.R and they are NOT the same
    # binding. So we keep a per-file table too, and a file-local definition always
    # wins over the global one.
    globals_ = {}          # name -> Symbol.id   (last definition seen)
    per_file = {}          # relpath -> {name -> Symbol.id}
    kind_of = {}           # Symbol.id -> kind
    for f in files:
        src, tree = sources[f], trees[f]
        lines = src.decode("utf8", "replace").split("\n")
        rel = f.relative_to(root).as_posix()
        for n in tree.root_node.children:      # TOP LEVEL ONLY — see note below
            if n.type != "left_assignment":
                continue
            lhs, rhs = n.children[0], n.children[-1]
            if lhs.type != "identifier":
                continue
            name = _txt(src, lhs)
            kind = "function" if rhs.type == "function_definition" else "variable"
            sid = f"{rel}::{name}"
            # body = the whole assignment, plus any roxygen block above it
            b = _span(rel, n)
            doc_start = b.start_line
            i = n.start_point[0] - 1
            while i >= 0 and lines[i].strip().startswith("#'"):
                doc_start = i + 1
                i -= 1
            body = Span(rel, doc_start, 0, b.end_line, b.end_col)
            idx.symbols.append(
                Symbol(sid, name, name, kind, _span(rel, lhs), body,
                       _preceding_doc(src, n, lines))
            )
            globals_[name] = sid
            per_file.setdefault(rel, {})[name] = sid
            kind_of[sid] = kind

    # ---- pass 2: references, resolved against local scope then global table ---
    for f in files:
        src, tree = sources[f], trees[f]
        rel = f.relative_to(root).as_posix()

        # map each function_definition to its Symbol.id + local names (params + assigns)
        # named functions: map a function_definition back to the name it was bound to
        named = {}
        for n in _walk(tree.root_node):
            if n.type == "left_assignment" and n.children[0].type == "identifier" \
               and n.children[-1].type == "function_definition":
                named[n.children[-1].id] = f"{rel}::{_txt(src, n.children[0])}"

        # EVERY function_definition is a scope — including closures and lambdas
        fdefs = []
        for fd in _walk(tree.root_node):
            if fd.type != "function_definition":
                continue
            locals_ = set()
            for p in _walk(fd):
                if p.type == "formal_parameters":
                    for c in p.children:
                        if c.type == "identifier":
                            locals_.add(_txt(src, c))
                        elif c.type == "default_parameter" and c.children:
                            locals_.add(_txt(src, c.children[0]))
                if p.type == "left_assignment" and p.children[0].type == "identifier":
                    locals_.add(_txt(src, p.children[0]))
                if p.type in ("for", "for_statement"):
                    for c in p.children:
                        if c.type == "identifier":
                            locals_.add(_txt(src, c))
                            break
            fdefs.append((fd.start_byte, fd.end_byte, named.get(fd.id), locals_))

        def enclosing_of(node):
            best, all_locals = None, set()
            for s, e, sid, loc in fdefs:
                if s <= node.start_byte < e:
                    all_locals |= loc                       # lexical scope chain
                    if best is None or s > best[0]:
                        best = (s, sid)
            named_sid = None
            if best:
                # walk outward for the nearest *named* function
                cands = [(s, sid) for s, e, sid, _ in fdefs
                         if s <= node.start_byte < e and sid]
                named_sid = max(cands, key=lambda t: t[0])[1] if cands else None
            return named_sid, all_locals

        # definition LHS identifiers are not references
        def_lhs = set()
        for n in _walk(tree.root_node):
            if n.type == "left_assignment" and n.children[0].type == "identifier":
                def_lhs.add(n.children[0].id)
            if n.type == "formal_parameters":
                for c in n.children:
                    if c.type == "identifier":
                        def_lhs.add(c.id)
                    elif c.type == "default_parameter" and c.children:
                        def_lhs.add(c.children[0].id)
            # `f(show_col_types = FALSE)` — the key is an argument NAME, not a reference
            if n.type == "default_argument" and n.children:
                def_lhs.add(n.children[0].id)

        for n in _walk(tree.root_node):
            if n.type != "identifier" or n.id in def_lhs:
                continue
            # skip `$` field access on the rhs (rec$record) and named call args
            par = n.parent
            if par is not None and par.type in ("dollar", "extract_operator") and par.children[-1].id == n.id:
                continue

            text = _txt(src, n)
            enc_id, enc_locals = enclosing_of(n)
            ref = Reference(span=_span(rel, n), text=text, enclosing=enc_id)

            if _in_nse_call(src, n):
                ref.target_kind, ref.confidence = "data", 0.5   # NSE column, not a code symbol
            elif text in enc_locals:
                ref.target_kind, ref.confidence = "local", 0.9
            elif text in per_file.get(rel, {}):
                ref.resolves_to = per_file[rel][text]      # file-local wins
                ref.target_kind, ref.confidence = "project", 1.0
            elif text in globals_:
                ref.resolves_to = globals_[text]
                ref.target_kind, ref.confidence = "project", 0.8   # cross-file: less certain
            elif text in KNOWN_EXTERNAL or _is_package_arg(src, n):
                ref.target_kind, ref.confidence = "external", 0.8
            idx.references.append(ref)

            if ref.target_kind == "project" and ref.enclosing and ref.resolves_to:
                kind = "reads" if kind_of.get(ref.resolves_to) == "variable" else "calls"
                idx.edges.append(Edge(ref.enclosing, ref.resolves_to, kind))

    module_level = {ref.resolves_to for ref in idx.references
                    if ref.enclosing is None and ref.resolves_to}
    for s in idx.symbols:
        if s.id in module_level and s.kind == "function":
            s.entry = True

    # ---- pass 3: dataflow (datasets + columns) -------------------------------
    dataflow_r.extract(idx, root, sources, trees, _txt, _walk, _span, globals_,
                       KNOWN_EXTERNAL)
    return idx


if __name__ == "__main__":
    import json
    idx = build_index(Path(sys.argv[1]))
    print(json.dumps(idx.stats(), indent=2))

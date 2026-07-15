"""R dataflow pass — same node kinds, same edges, different extraction (D12).

R makes two things easier and one harder:
  + `source()` into a global env means no import graph to chase.
  + `df$col` is an unambiguous column reference.
  - tidyverse NSE means columns appear as BARE IDENTIFIERS inside verbs, which
    is precisely the thing the resolver already has to special-case. Here that
    liability becomes an asset: those tokens ARE the column list.
"""
from __future__ import annotations

from r_grammar import (arg_value, has_default, is_assign, is_fn_assign,
                       is_named_arg, param_idents)
from schema import CONSUMES, HAS_COL, PRODUCES, READS_COL, WRITES_COL, Edge, Span, Symbol

READERS = {"read_csv", "read.csv", "read_tsv", "read_parquet", "readRDS", "read_excel",
           "read_json", "fromJSON", "read_rds"}
WRITERS = {"write_csv", "write.csv", "write_tsv", "saveRDS", "write_parquet",
           "write_json", "write_rds"}
NSE_VERBS = {"mutate", "filter", "select", "arrange", "group_by", "summarise", "summarize",
             "count", "slice_max", "slice_min", "distinct", "rename", "transmute", "pull",
             "drop_na", "desc"}
NOT_COLUMNS = {"n", "TRUE", "FALSE", "NULL", "NA", "df", "."}


def _is_callee(node):
    """`rec$record(...)` / `mean(x)` — an identifier in FUNCTION position is not a column.

    NOTE: tree-sitter mints a fresh Python object on every `.children` access, so
    `a is b` between two Nodes is ALWAYS false. Compare `.id`. (This bit me twice.)
    """
    p = node.parent
    while p is not None and p.type in ("dollar", "extract_operator", "namespace_operator"):
        node, p = p, p.parent
    return (p is not None and p.type == "call" and p.children
            and p.children[0].id == node.id)


def _locals_of(body, txt, walk, src):
    """Params and local assignments — a local variable is not a column."""
    out = set()
    for p in walk(body):
        if p.type == "parameters":
            for ident in param_idents(p):
                out.add(txt(src, ident))
        if is_assign(p) and p.children[0].type == "identifier":
            out.add(txt(src, p.children[0]))
        if p.type in ("for", "for_statement"):
            for c in p.children:
                if c.type == "identifier":
                    out.add(txt(src, c))
                    break
    return out


def extract(idx, root, sources, trees, txt, walk, span, globals_, known_external=frozenset()):
    seen = {}

    def is_column(nm, node, locals_):
        return not (
            nm in NOT_COLUMNS
            or nm.startswith(".")            # .data, .groups, .x — tidyverse pronouns
            or nm in globals_                # a project function
            or nm in NSE_VERBS
            or nm in known_external           # base R / tidyverse (mean, where, across…)
            or nm in locals_                  # a parameter or local variable
            or _is_callee(node)               # in function position
        )

    def node(kind, key, name, sp, detail=""):
        sid = f"{kind}::{key}"
        if sid not in seen:
            seen[sid] = True
            idx.symbols.append(Symbol(sid, name, key, kind, sp, sp, detail))
        return sid

    edge_keys = {(e.from_symbol, e.to_symbol, e.kind) for e in idx.edges}

    def add(a, b, kind, detail=""):
        k = (a, b, kind)
        if k not in edge_keys:
            edge_keys.add(k)
            idx.edges.append(Edge(a, b, kind, detail))

    # top-level string constants: PROCESSED <- "processed_iris.csv"
    consts = {}
    for f, tree in trees.items():
        src = sources[f]
        for n in tree.root_node.children:
            if is_assign(n) and n.children[0].type == "identifier":
                rhs = n.children[-1]
                if rhs.type == "string":
                    v = txt(src, rhs).strip('"\'')
                    if "." in v:
                        consts[txt(src, n.children[0])] = v.split("/")[-1]

    def filename(src, arg_node, defaults):
        # `arguments` wraps every entry in an `argument` node, so unwrap to the
        # value before matching — the old grammar handed us the value directly.
        arg_node = arg_value(arg_node)
        if arg_node.type == "string":
            v = txt(src, arg_node).strip('"\'')
            return v.split("/")[-1] if "." in v else None
        if arg_node.type == "identifier":
            nm = txt(src, arg_node)
            return defaults.get(nm) or consts.get(nm)
        return None

    for f, tree in trees.items():
        src = sources[f]
        rel = f.relative_to(root).as_posix()

        for fd in walk(tree.root_node):
            if not is_fn_assign(fd):
                continue
            fn_name = txt(src, fd.children[0])
            fn_sid = f"{rel}::{fn_name}"
            body = fd.children[-1]
            sp = Span(rel, fd.start_point[0] + 1, 0, fd.start_point[0] + 1, 0)
            locals_ = _locals_of(body, txt, walk, src)

            # parameter defaults that point at a filename constant
            defaults = {}
            for p in walk(body):
                # `parameter` covers both `df` and `path = "x.csv"`; only the
                # latter has an `=` and a value to read a filename out of.
                if has_default(p):
                    nm = txt(src, p.children[0])
                    fnm = filename(src, p.children[-1], {})
                    if fnm:
                        defaults[nm] = fnm

            for n in walk(body):
                if n.type == "call" and n.children and n.children[0].type == "identifier":
                    callee = txt(src, n.children[0])
                    args = [c for c in walk(n) if c.parent and c.parent.type == "arguments"]

                    # ---- file datasets --------------------------------------
                    if callee in WRITERS:
                        for a in args:
                            fnm = filename(src, a, defaults)
                            if fnm:
                                add(fn_sid, node("dataset", fnm, fnm, sp, "file"),
                                    PRODUCES, "writes file")
                                break
                    if callee in READERS:
                        for a in args:
                            fnm = filename(src, a, defaults)
                            if fnm:
                                add(node("dataset", fnm, fnm, sp, "file"), fn_sid,
                                    CONSUMES, "reads file")
                                break

                    # ---- NSE columns ----------------------------------------
                    if callee in NSE_VERBS:
                        for a in walk(n):
                            if a.type != "identifier" or a.id == n.children[0].id:
                                continue
                            nm = txt(src, a)
                            named = (is_named_arg(a.parent)
                                     and a.parent.children[0].id == a.id)
                            if named and callee in ("mutate", "transmute", "rename"):
                                # `mutate(sepal_ratio = ...)` CREATES a column
                                if nm not in NOT_COLUMNS and not nm.startswith("."):
                                    add(fn_sid, node("column", nm, nm, sp), WRITES_COL)
                                continue
                            if named:
                                continue          # `n = 5`, `.groups = "drop"` — argument names
                            if not is_column(nm, a, locals_):
                                continue
                            add(fn_sid, node("column", nm, nm, sp), READS_COL)

                # ---- df$col ---------------------------------------------------
                if n.type in ("dollar", "extract_operator") and len(n.children) >= 3:
                    rhs = n.children[-1]
                    if rhs.type == "identifier":
                        nm = txt(src, rhs)
                        if is_column(nm, rhs, locals_ - {"df"}):
                            add(fn_sid, node("column", nm, nm, sp), READS_COL)

                # ---- named frames handed between functions --------------------
                if is_assign(n) and n.children[0].type == "identifier":
                    rhs = n.children[-1]
                    if rhs.type == "call" and rhs.children and rhs.children[0].type == "identifier":
                        callee = txt(src, rhs.children[0])
                        if callee in globals_:
                            var = txt(src, n.children[0])
                            d = node("dataset", f"{rel}::{fn_name}::{var}", var, sp, "frame")
                            add(globals_[callee], d, PRODUCES, "returns")

                if n.type == "call" and n.children and n.children[0].type == "identifier":
                    callee = txt(src, n.children[0])
                    if callee in globals_:
                        for a in walk(n):
                            if a.type == "identifier" and a.id != n.children[0].id:
                                key = f"dataset::{rel}::{fn_name}::{txt(src, a)}"
                                if key in seen:
                                    add(key, globals_[callee], CONSUMES, "argument")

    producers, fn_cols = {}, {}
    for e in idx.edges:
        if e.kind == PRODUCES:
            producers.setdefault(e.to_symbol, set()).add(e.from_symbol)
        if e.kind in (READS_COL, WRITES_COL):
            fn_cols.setdefault(e.from_symbol, set()).add(e.to_symbol)
    # sorted: set iteration order varies with hash randomization across runs,
    # and edge order must not
    for dataset, fns in producers.items():
        for fn_sid in sorted(fns):
            for col in sorted(fn_cols.get(fn_sid, ())):
                add(dataset, col, HAS_COL)

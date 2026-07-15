"""Python dataflow pass — datasets and columns as first-class nodes.

A named frame and a file on disk are the SAME relationship: a data product with
one producer and N consumers. The only difference is whether it crosses a
process boundary. So both become `dataset` nodes.

What is statically sound here, and what is not:
  - sound: a literal filename in a read/write call; a column named by a string
    literal; a local frame assigned from a call and passed onward.
  - NOT sound: following an unnamed frame across module boundaries. That needs
    the runtime trace (design notes D10), not the static index.
"""
from __future__ import annotations

import ast

import embedded_sql
from schema import CONSUMES, HAS_COL, PRODUCES, READS_COL, WRITES_COL, Edge, Span, Symbol

READERS = {"read_csv", "read_parquet", "read_json", "read_excel", "read_table", "read_feather"}
WRITERS = {"to_csv", "to_parquet", "to_excel", "to_json", "to_feather"}
# pandas methods whose string arguments name columns
COL_ARG_METHODS = {"sort_values", "groupby", "nlargest", "nsmallest", "drop_duplicates",
                   "set_index", "dropna", "value_counts", "sort_index", "unique"}
# methods that only exist on a dataframe-like object — seeing one is evidence
DF_METHODS = {"head", "tail", "describe", "merge", "join", "pivot", "pivot_table",
              "reset_index", "assign", "query", "iterrows", "itertuples", "melt",
              "to_dict", "copy", "fillna", "astype", "apply", "agg", "sample"}


def _filename_literals(node):
    """Pull any filename-looking string constant out of an expression tree."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "." in n.value:
            tail = n.value.replace("\\", "/").split("/")[-1]
            if "." in tail and not tail.startswith("."):
                out.append(tail)
    return out


class _Scope:
    """Resolves a Name to a filename, via module constants or parameter defaults."""

    def __init__(self, tree):
        self.module = {}
        self.strings = {}          # module constants that hold a string (maybe SQL)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                       and isinstance(node.value.value, str):
                        self.strings[t.id] = node.value.value
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        lits = _filename_literals(node.value)
                        if lits:
                            self.module[t.id] = lits[-1]
        self.params = {}

    def enter(self, fn):
        self.params = {}
        args = fn.args
        defaults = dict(zip([a.arg for a in args.args][-len(args.defaults):] if args.defaults else [],
                            args.defaults))
        for name, d in defaults.items():
            lits = _filename_literals(d)
            if lits:
                self.params[name] = lits[-1]
            elif isinstance(d, ast.Name) and d.id in self.module:
                self.params[name] = self.module[d.id]

    def filename(self, node):
        lits = _filename_literals(node)
        if lits:
            return lits[-1]
        if isinstance(node, ast.Name):
            return self.params.get(node.id) or self.module.get(node.id)
        return None


def _cols_from_subscript(sl):
    """df["a"]  ->  ["a"]        df[["a","b"]]  ->  ["a","b"]"""
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return [sl.value]
    if isinstance(sl, (ast.List, ast.Tuple)):
        return [e.value for e in sl.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _data_evidence(fn):
    """Which local names show actual evidence of being DATA?

    Real code broke the old rule. `conn = connect()`, `c = cur()`, `n = count()`,
    `txt = render()` all became "data products" simply because they were assigned
    from a call. A name is only a data product if we OBSERVED data behaviour on it:
    a column subscript, a dataframe method, or a trip through a reader/writer.
    Assignment from a call is not evidence of anything.
    """
    ev = set()
    for n in ast.walk(fn):
        # x["col"]  /  x[["a","b"]]
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
            if _cols_from_subscript(n.slice):
                ev.add(n.value.id)
        # x.groupby(...) / x.sort_values(...) / x.to_csv(...)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
           and isinstance(n.func.value, ast.Name):
            if n.func.attr in COL_ARG_METHODS or n.func.attr in WRITERS \
               or n.func.attr in DF_METHODS:
                ev.add(n.func.value.id)
        # x = pd.read_csv(...)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
           and isinstance(n.value.func, ast.Attribute) \
           and n.value.func.attr in READERS:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    ev.add(t.id)
    return ev


def _data_functions(trees):
    """Functions that demonstrably HANDLE data — the seed for propagation.

    A function is data-handling if it does column work on one of its own
    parameters (`def f(df): df["x"]`). Evidence then propagates along calls:
    what flows into a data function, and what comes out of one, is data too.
    That is what keeps a clean pipeline (`cleaned = clean_dataset(raw)`) visible
    without readmitting `conn = connect()`.
    """
    data_fns = set()
    for rel, tree in trees.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in fn.args.args}
            ev = _data_evidence(fn)
            if ev & params:
                data_fns.add(fn.name)
    return data_fns


def _propagate(fn, data_fns, seed):
    """A local is data if it came OUT of a data function, or goes INTO one."""
    ev = set(seed)
    for _ in range(3):                       # tiny fixed point; chains are short
        grew = False
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
               and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                f = n.value.func
                nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if nm in data_fns and n.targets[0].id not in ev:
                    ev.add(n.targets[0].id); grew = True
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if nm in data_fns:
                    for a in n.args:
                        if isinstance(a, ast.Name) and a.id not in ev:
                            ev.add(a.id); grew = True
        if not grew:
            break
    return ev


def extract(idx, root, files, sym_ids):
    """Mutate `idx` in place: add dataset + column symbols and their edges."""
    seen = {}

    def node(kind, key, name, span, detail=""):
        sid = f"{kind}::{key}"
        if sid not in seen:
            seen[sid] = True
            idx.symbols.append(Symbol(sid, name, key, kind, span, span, detail))
        return sid

    def add(a, b, kind, detail=""):
        e = Edge(a, b, kind, detail)
        if not any(x.from_symbol == a and x.to_symbol == b and x.kind == kind for x in idx.edges):
            idx.edges.append(e)

    # pass 0: which functions handle data at all?
    trees = {}
    for f in files:
        try:
            trees[f.relative_to(root).as_posix()] = ast.parse(f.read_text(encoding="utf8"))
        except SyntaxError:
            pass
    data_fns = _data_functions(trees)

    for f in files:
        rel = f.relative_to(root).as_posix()
        tree = trees.get(rel)
        if tree is None:
            continue
        scope = _Scope(tree)

        def sql_of(arg):
            """The query text behind an argument: a literal, or a module constant."""
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
            if isinstance(arg, ast.Name):
                return scope.strings.get(arg.id)
            if isinstance(arg, ast.JoinedStr):        # f-string: take the static parts
                return "".join(v.value for v in arg.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str))
            return None

        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            fn_sid = next((s for s in sym_ids
                           if s.endswith(f"::{fn.name}") and s.startswith(rel)), None)
            if not fn_sid:
                continue
            scope.enter(fn)
            sp = Span(rel, fn.lineno, 0, fn.lineno, 0)
            evidence = _propagate(fn, data_fns, _data_evidence(fn))

            for n in ast.walk(fn):
                # ---- SQL hiding in a string literal --------------------------
                if isinstance(n, ast.Call):
                    fname_ = n.func.attr if isinstance(n.func, ast.Attribute) else \
                        (n.func.id if isinstance(n.func, ast.Name) else None)
                    if fname_ in embedded_sql.SQL_SINKS:
                        # to_sql("table", conn) names its target table directly
                        hint = None
                        if fname_ == "to_sql" and n.args and isinstance(n.args[0], ast.Constant):
                            hint = n.args[0].value
                            embedded_sql.index_query(
                                idx, "SELECT 1", fn_sid, sp, node, add, table_hint=hint)
                        for a in list(n.args) + [k.value for k in n.keywords]:
                            q = sql_of(a)
                            if q and embedded_sql.looks_like_sql(q):
                                embedded_sql.index_query(idx, q, fn_sid, sp, node, add)

                # ---- file datasets ------------------------------------------
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                    m = n.func.attr
                    if m in WRITERS and n.args:
                        fname = scope.filename(n.args[0])
                        if fname:
                            d = node("dataset", fname, fname, sp, "file")
                            add(fn_sid, d, PRODUCES, "writes file")
                    if m in READERS and n.args:
                        fname = scope.filename(n.args[0])
                        if fname:
                            d = node("dataset", fname, fname, sp, "file")
                            add(d, fn_sid, CONSUMES, "reads file")
                    # ---- columns named as string args -----------------------
                    if m in COL_ARG_METHODS:
                        for a in n.args:
                            for c in _cols_from_subscript(a):
                                add(fn_sid, node("column", c, c, sp), READS_COL)
                        for kw in n.keywords:
                            if kw.arg in ("subset", "by", "columns"):
                                for c in _cols_from_subscript(kw.value):
                                    add(fn_sid, node("column", c, c, sp), READS_COL)

                # ---- columns via subscript ----------------------------------
                if isinstance(n, ast.Subscript):
                    for c in _cols_from_subscript(n.slice):
                        add(fn_sid, node("column", c, c, sp), READS_COL)

                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Subscript):
                            for c in _cols_from_subscript(t.slice):
                                add(fn_sid, node("column", c, c, sp), WRITES_COL)

                    # ---- in-memory named frames (handoff between functions) --
                    if isinstance(n.value, ast.Call) and len(n.targets) == 1 \
                       and isinstance(n.targets[0], ast.Name):
                        var = n.targets[0].id
                        if var not in evidence:
                            continue          # no data behaviour observed — not a data product
                        callee = n.value.func
                        cname = callee.id if isinstance(callee, ast.Name) else \
                            (callee.attr if isinstance(callee, ast.Attribute) else None)
                        produced_by = next((s for s in sym_ids if s.endswith(f"::{cname}")), None)
                        if produced_by:
                            d = node("dataset", f"{rel}::{fn.name}::{var}", var, sp, "frame")
                            add(produced_by, d, PRODUCES, "returns")

                # ---- a named frame passed onward = consumption ---------------
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    callee_sid = next((s for s in sym_ids
                                       if s.endswith(f"::{n.func.id}")), None)
                    if callee_sid:
                        for a in n.args:
                            if isinstance(a, ast.Name):
                                key = f"{rel}::{fn.name}::{a.id}"
                                if f"dataset::{key}" in seen:
                                    add(f"dataset::{key}", callee_sid, CONSUMES, "argument")

    # ---- a dataset carries the columns its producer touches ------------------
    producers = {}
    for e in idx.edges:
        if e.kind == PRODUCES:
            producers.setdefault(e.to_symbol, set()).add(e.from_symbol)
    fn_cols = {}
    for e in idx.edges:
        if e.kind in (READS_COL, WRITES_COL):
            fn_cols.setdefault(e.from_symbol, set()).add(e.to_symbol)
    for dataset, fns in producers.items():
        for fn_sid in fns:
            for col in fn_cols.get(fn_sid, ()):
                add(dataset, col, HAS_COL)

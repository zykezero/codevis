"""Python dataflow pass — datasets and columns as first-class nodes.

A named frame and a file on disk are the SAME relationship: a data product with
one producer and N consumers. The only difference is whether it crosses a
process boundary. So both become `dataset` nodes.

What is statically sound here, and what is not:
  - sound: a literal filename in a read/write call (pandas readers/writers, and
    json/pickle/numpy file IO — including through a handle from `open("x.json")`);
    a column named by a string literal; a local frame assigned from a call and
    passed onward; a plain container (list/tuple/dict/set/ndarray) built from a
    literal or constructor, or mutated with append/extend/insert/update.
  - NOT sound: following an unnamed frame across module boundaries. That needs
    the runtime trace (design notes D10), not the static index.
"""
from __future__ import annotations

import ast

import embedded_sql
from schema import CONSUMES, HAS_COL, PRODUCES, READS_COL, WRITES_COL, Edge, Span, Symbol

READERS = {"read_csv", "read_parquet", "read_json", "read_excel", "read_table", "read_feather"}
WRITERS = {"to_csv", "to_parquet", "to_excel", "to_json", "to_feather"}
# module-qualified calls whose argument names a data FILE:
#   json.load(open("x.json")), pickle.dump(obj, fh), np.save("x.npy", arr)
MOD_READERS = {("json", "load"), ("pickle", "load"),
               ("np", "load"), ("numpy", "load"),
               ("np", "loadtxt"), ("numpy", "loadtxt"),
               ("np", "genfromtxt"), ("numpy", "genfromtxt")}
MOD_WRITERS = {("json", "dump"), ("pickle", "dump"),
               ("np", "save"), ("numpy", "save"),
               ("np", "savetxt"), ("numpy", "savetxt")}
# no file involved, but assignment FROM these is still data evidence
MOD_PARSERS = MOD_READERS | {("json", "loads"), ("pickle", "loads")}
# pandas methods whose string arguments name columns
COL_ARG_METHODS = {"sort_values", "groupby", "nlargest", "nsmallest", "drop_duplicates",
                   "set_index", "dropna", "value_counts", "sort_index", "unique"}
# methods that only exist on a dataframe-like object — seeing one is evidence
DF_METHODS = {"head", "tail", "describe", "merge", "join", "pivot", "pivot_table",
              "reset_index", "assign", "query", "iterrows", "itertuples", "melt",
              "to_dict", "copy", "fillna", "astype", "apply", "agg", "sample"}
# plain containers are data products too: a list of rows, a dict of records, a
# tuple of frames, an ndarray. Mutating one, or building one from a literal or
# constructor, is observed data behaviour — `conn = connect()` still is not.
CONTAINER_CTORS = {"list", "tuple", "dict", "set", "array", "asarray",
                   "DataFrame", "Series"}
CONTAINER_METHODS = {"append", "extend", "insert", "update"}
CONTAINER_LITERALS = (ast.List, ast.Tuple, ast.Dict, ast.Set,
                      ast.ListComp, ast.DictComp, ast.SetComp)


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


def _open_handles(fn, scope):
    """Local names bound to a file handle over a literal path:
    `with open("x.json") as f:` or `f = open("x.json")`. Lets json.load(f) /
    pickle.dump(obj, f) resolve to the file the handle was opened on."""
    out = {}

    def bind(name_node, call):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
           and call.func.id == "open" and call.args \
           and isinstance(name_node, ast.Name):
            fname = scope.filename(call.args[0])
            if fname:
                out[name_node.id] = fname

    for h in ast.walk(fn):
        if isinstance(h, (ast.With, ast.AsyncWith)):
            for item in h.items:
                bind(item.optional_vars, item.context_expr)
        if isinstance(h, ast.Assign) and len(h.targets) == 1:
            bind(h.targets[0], h.value)
    return out


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
        # x["col"]  /  x[["a","b"]]  (also a dict subscripted by string keys)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
            if _cols_from_subscript(n.slice):
                ev.add(n.value.id)
        # x.groupby(...) / x.to_csv(...) / x.append(...) / x.extend(...)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
           and isinstance(n.func.value, ast.Name):
            if n.func.attr in COL_ARG_METHODS or n.func.attr in WRITERS \
               or n.func.attr in DF_METHODS or n.func.attr in CONTAINER_METHODS:
                ev.add(n.func.value.id)
        if isinstance(n, ast.Assign):
            targets = [t.id for t in n.targets if isinstance(t, ast.Name)]
            # x = [...] / (...) / {...} — a literal container IS data
            if isinstance(n.value, CONTAINER_LITERALS):
                ev.update(targets)
            if isinstance(n.value, ast.Call):
                f = n.value.func
                # x = pd.read_csv(...) / json.load(...) / pickle.loads(...)
                if isinstance(f, ast.Attribute) and (
                        f.attr in READERS
                        or (isinstance(f.value, ast.Name)
                            and (f.value.id, f.attr) in MOD_PARSERS)):
                    ev.update(targets)
                # x = list(...) / dict(...) / np.array(...)
                nm = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if nm in CONTAINER_CTORS:
                    ev.update(targets)
    return ev


def _data_functions(trees):
    """Functions that demonstrably HANDLE data — the seed for propagation.

    A function is data-handling if it does column work on one of its own
    parameters (`def f(df): df["x"]`), or RETURNS a local it demonstrably
    treated as data (`rows = []; rows.append(...); return rows`). Evidence then
    propagates along calls: what flows into a data function, and what comes out
    of one, is data too. That is what keeps a clean pipeline
    (`cleaned = clean_dataset(raw)`) visible without readmitting
    `conn = connect()`.
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
                continue
            for r in ast.walk(fn):
                if isinstance(r, ast.Return) and isinstance(r.value, ast.Name) \
                   and r.value.id in ev:
                    data_fns.add(fn.name)
                    break
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


def _functions_with_quals(tree):
    """[(fn_node, qualname)] — the same qualname construction as the front-end's
    pass 1, so a method's id (`file.py::Class.method`) can be looked up exactly
    instead of suffix-matched (which silently missed every method)."""
    out = []

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if not isinstance(child, ast.ClassDef):
                    out.append((child, qual))
                visit(child, qual)

    visit(tree, "")
    return out


def extract(idx, root, files, sym_ids):
    """Mutate `idx` in place: add dataset + column symbols and their edges."""
    seen = {}

    def node(kind, key, name, span, detail=""):
        sid = f"{kind}::{key}"
        if sid not in seen:
            seen[sid] = True
            idx.symbols.append(Symbol(sid, name, key, kind, span, span, detail))
        return sid

    edge_keys = {(e.from_symbol, e.to_symbol, e.kind) for e in idx.edges}

    def add(a, b, kind, detail=""):
        k = (a, b, kind)
        if k not in edge_keys:
            edge_keys.add(k)
            idx.edges.append(Edge(a, b, kind, detail))

    # code symbol ids are "<relpath>::<qualname>" — index them two ways:
    # exact (relpath, qualname) for the enclosing-function lookup, and by bare
    # name for callee resolution. A bare name that maps to MORE than one symbol
    # links to nothing: an arbitrary winner from set iteration order made the
    # same codebase index differently across runs, and a false link is worse
    # than no link.
    by_qual, by_name = {}, {}
    for s in sym_ids:
        if "::" not in s:
            continue
        rel_, qual = s.split("::", 1)
        by_qual[(rel_, qual)] = s
        by_name.setdefault(qual.rsplit(".", 1)[-1], set()).add(s)

    def unique(name):
        hits = by_name.get(name)
        return next(iter(hits)) if hits and len(hits) == 1 else None

    # pass 0: which functions handle data at all?
    trees = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            trees[rel] = ast.parse(idx.files.get(rel) or f.read_text(encoding="utf8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
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

        for fn, fn_qual in _functions_with_quals(tree):
            fn_sid = by_qual.get((rel, fn_qual))
            if not fn_sid:
                continue
            scope.enter(fn)
            sp = Span(rel, fn.lineno, 0, fn.lineno, 0)
            evidence = _propagate(fn, data_fns, _data_evidence(fn))
            handles = _open_handles(fn, scope)

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
                    # json.load(f) / pickle.dump(obj, f) / np.save("x.npy", a):
                    # the file is named by a literal, a constant, or a handle
                    # opened on one
                    if isinstance(n.func.value, ast.Name):
                        pair = (n.func.value.id, m)
                        if pair in MOD_WRITERS or pair in MOD_READERS:
                            fname = None
                            for a in list(n.args) + [k.value for k in n.keywords]:
                                fname = scope.filename(a) or \
                                    (handles.get(a.id) if isinstance(a, ast.Name) else None)
                                if fname:
                                    break
                            if fname:
                                d = node("dataset", fname, fname, sp, "file")
                                if pair in MOD_WRITERS:
                                    add(fn_sid, d, PRODUCES, "writes file")
                                else:
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
                        produced_by = unique(cname) if cname else None
                        if produced_by:
                            d = node("dataset", f"{rel}::{fn.name}::{var}", var, sp, "frame")
                            add(produced_by, d, PRODUCES, "returns")

                # ---- a named frame passed onward = consumption ---------------
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    callee_sid = unique(n.func.id)
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
    # sorted: set iteration order varies with hash randomization across runs,
    # and edge order must not
    for dataset, fns in producers.items():
        for fn_sid in sorted(fns):
            for col in sorted(fn_cols.get(fn_sid, ())):
                add(dataset, col, HAS_COL)

"""R grammar adapters — the ONE place that knows tree-sitter-r's node names.

These exist because the R grammar RENAMED the nodes this front-end reads, and
the rename is a SILENT failure: nothing raises, no node matches, and the R index
comes out empty. An R project would simply appear to contain no code.

    old grammar (tree_sitter_languages)   current grammar (tree_sitter_language_pack)
    -----------------------------------   -------------------------------------------
    left_assignment                       binary_operator, with a `<-` operator child
    formal_parameters                     parameters, wrapping each entry in `parameter`
    default_parameter                     parameter, with an `=` child
    default_argument                      argument, with an `=` child
    dollar                                extract_operator

`tree_sitter_languages` was pinned because it bundled the grammar, but its last
release predates Python 3.12 — it cannot be installed on any current
interpreter, so R was effectively dead code. `tree_sitter_language_pack` is the
maintained successor.

Both frontend_r and dataflow_r read these nodes, so the adapters live here
rather than in either: importing them from frontend_r made a cycle
(frontend_r -> dataflow_r -> frontend_r).
"""
from __future__ import annotations


def is_assign(n):
    """`x <- expr`.

    The grammar models this as a binary_operator whose middle child is the `<-`
    token. binary_operator ALSO covers `a + b`, so checking the type alone would
    match arithmetic — the operator child is what makes it an assignment.
    """
    return (n.type == "binary_operator" and len(n.children) >= 3
            and n.children[1].type == "<-")


def is_fn_assign(n):
    """`f <- function(...) ...` — an assignment whose RHS is a function."""
    return is_assign(n) and n.children[-1].type == "function_definition"


def param_idents(params_node):
    """The identifier node of each parameter in a `parameters` list.

    Every entry is wrapped in a `parameter` node whether or not it has a default
    (`df` and `n = 5` are both `parameter`), so one branch covers both. `...`
    wraps a `dots` node instead of an identifier and is deliberately skipped.
    """
    for c in params_node.children:
        if c.type == "parameter" and c.children and c.children[0].type == "identifier":
            yield c.children[0]


def has_default(param_node):
    """`n = 5` — a parameter carrying a default value."""
    return (param_node.type == "parameter" and len(param_node.children) >= 3
            and param_node.children[1].type == "=")


def is_named_arg(arg_node):
    """`f(show = FALSE)` — `show` is an argument NAME, not a reference to a
    symbol called `show`."""
    return (arg_node is not None and arg_node.type == "argument"
            and len(arg_node.children) >= 2 and arg_node.children[1].type == "=")


def arg_value(arg_node):
    """The value inside an `argument` wrapper; anything else unchanged.

    The old grammar handed call arguments straight to the caller as strings and
    identifiers. The current one wraps each in an `argument` node, so consumers
    that match on `string`/`identifier` need to unwrap first.
    """
    if arg_node.type == "argument" and arg_node.children:
        return arg_node.children[-1]
    return arg_node

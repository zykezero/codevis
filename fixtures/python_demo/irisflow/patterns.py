"""Regression fixture: source text that is hostile to naive HTML assembly.

Every string below is legal Python that a real project would plausibly contain.
Each one is also a JavaScript `String.replace` replacement pattern. If a renderer
substitutes source into a template with a replacement STRING instead of a
replacement FUNCTION, these silently corrupt the output.

This is not hypothetical: `re.compile(r'[-_]\d{4,}$')` in a real project produced
`$'` once JSON-encoded, which spliced the template into its own data and left the
viewer rendering an empty shell.
"""
import re

TRAIL_ID = re.compile(r"[-_]\d{4,}$")        # -> yields $' when JSON-encoded
LEADING = re.compile(r"^\s+")
PRICE = re.compile(r"\$\d+\.\d{2}")           # a literal dollar amount
BACKREF = re.compile(r"(\w+)\s+\1")           # a regex backreference

DOLLAR_AMP = "$&"
DOLLAR_TICK = "$`"
DOLLAR_QUOTE = "$'"
DOLLAR_DOLLAR = "$$"
DOLLAR_ONE = "$1"
SHELL = "echo ${HOME} && echo $0"


def normalise_id(value):
    """Strip a trailing numeric id — the exact pattern that broke the viewer."""
    return TRAIL_ID.sub("", value.strip())


def swap_pair(text):
    """A real replacement that legitimately uses $1-style groups."""
    return re.sub(r"(\w+),\s*(\w+)", r"\2 \1", text)

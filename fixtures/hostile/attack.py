"""Adversarial fixture: source text that attacks the HTML assembly itself.

Every string below corresponds to a real failure mode of splicing indexed
source into an HTML page. If any of them regresses, test_render.py fails on
this fixture — the page either stops parsing, stops booting, or the round-trip
comparison catches the corruption.
"""

# The HTML parser terminates a <script> element at the literal "</script>"
# regardless of JS string context. Unescaped, this breaks out of the inline
# INDEX block and executes.
BREAKOUT = "</script><script>window.__pwned = true</script>"

# String.replace with a replacement STRING expands these inside the
# replacement. A replacer function must be used instead.
DOLLAR_TRICKS = "$& $' $` $1"

# The template's own substitution tokens. If assembly rescans substituted
# content (chained .replace) instead of one pass, __APP__ inside this data is
# replaced and the viewer JS is spliced into the middle of the JSON.
TOKENS = "__APP__ __INDEX__ __ROOT__ __NONCE__"

# A regex that ends in $' — the exact shape that shipped a blank panel once.
TRAILING = "re.compile(r'[a-z]+$')"


def hostile_strings():
    """Return every attack string, so the fixture has a real function node."""
    return [BREAKOUT, DOLLAR_TRICKS, TOKENS, TRAILING]


def summarize():
    """A second symbol, so the call edge below exists."""
    return len(hostile_strings())

"""The language-neutral index schema (design notes D12).

Both front-ends emit *only* these records. Nothing downstream of here is
allowed to know whether it is looking at Python or R.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass(frozen=True)
class Span:
    file: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int


# Edge kinds. The web view colours by these and filters on them.
CALLS = "calls"                 # function -> function
READS = "reads"                 # function -> variable/constant
PRODUCES = "produces"           # function -> dataset   (returns it, or writes it to disk)
CONSUMES = "consumes"           # dataset  -> function  (passed in, or read from disk)
READS_COL = "reads_column"      # function -> column
WRITES_COL = "writes_column"    # function -> column
HAS_COL = "has_column"          # dataset  -> column


@dataclass
class Param:
    name: str
    annotation: str = ""
    default: str = ""


@dataclass
class Symbol:
    id: str                 # stable: "<file>::<qualname>"  — IDENTITY, never version
    name: str
    qualname: str
    kind: str               # function | class | method | variable | dataset | column
    span: Span              # the NAME token (where the cursor lands)
    body: Optional[Span] = None   # the WHOLE definition (what a card renders)
    doc: str = ""
    entry: bool = False     # invoked by a framework or at module level, not by project code
    changed: bool = False   # git: the diff touches this symbol's body
    impact: int = -1        # git: hops from the nearest changed symbol (0 = itself, -1 = untouched)

    signature: str = ""     # "fun_predict(x, model, *, normalize=True)"
    params: list = field(default_factory=list)   # [Param]

    # VERSION, deliberately NOT part of `id`.
    #
    # The feature spec proposed `id = qualified_name + content_hash`. That is a
    # bug: it makes identity change on every edit, so every annotation pinned to a
    # symbol dies the moment someone touches its body — the exact opposite of what
    # D8 wants ("annotations must survive agent regeneration").
    #
    # Identity answers "WHICH function". The hash answers "WHICH VERSION of it".
    # Annotations key on `id` and survive edits. Cached LLM explanations key on
    # (id, hash) and correctly invalidate when the body changes (spec B.5).
    content_hash: str = ""


@dataclass
class Reference:
    span: Span              # where the token sits
    text: str               # the token as written
    resolves_to: Optional[str] = None   # Symbol.id, or None — a first-class outcome
    target_kind: str = "unresolved"     # project | local | external | unresolved
    confidence: float = 0.0
    enclosing: Optional[str] = None     # Symbol.id of the function containing this ref


@dataclass
class Edge:
    from_symbol: str
    to_symbol: str
    kind: str               # see the edge-kind constants above
    detail: str = ""        # e.g. the argument name, or the file path behind a dataset
    call_sites: list = field(default_factory=list)   # [Span] where the call appears


@dataclass
class Index:
    language: str
    root: str = ""
    symbols: list = field(default_factory=list)
    references: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    files: dict = field(default_factory=dict)   # relpath -> source text (cards slice this)
    langs: dict = field(default_factory=dict)   # relpath -> language (a project can be mixed)
    diff_ref: str = ""                          # git ref this index was diffed against

    def fingerprint(self):
        """Identity of the GRAPH SHAPE, not the source text.

        A saved layout stays valid as long as the nodes are the same. Editing a
        function body must NOT invalidate it; adding or removing a symbol must.
        """
        import hashlib
        key = "\n".join(sorted(s.id for s in self.symbols))
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_dict(self):
        return {
            "language": self.language,
            "root": self.root,
            "symbols": [asdict(s) for s in self.symbols],
            "references": [asdict(r) for r in self.references],
            "edges": [asdict(e) for e in self.edges],
            "files": self.files,
            "langs": self.langs,
            "fingerprint": self.fingerprint(),
            "diff_ref": self.diff_ref,
        }

    def stats(self):
        total = len(self.references)
        by = {}
        for r in self.references:
            by[r.target_kind] = by.get(r.target_kind, 0) + 1
        linkable = by.get("project", 0)
        return {
            "symbols": len(self.symbols),
            "references": total,
            "edges": len(self.edges),
            "by_target": by,
            # the number that matters: refs that become a clickable cross-file link
            "project_link_rate": round(linkable / total, 3) if total else 0.0,
        }

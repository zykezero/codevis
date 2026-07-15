# Spike findings — is static resolution good enough to be the product?

Run: `cd spike && python3 run_spike.py`
Date: 2026-07-13. Answers §8 of the design notes ("the make-or-break test").

## Verdict: **GO.** Link quality is sufficient in both languages, by different means.

| | Python (Jedi) | R (tree-sitter + scope walker) |
|---|---|---|
| Symbols indexed | 104 | 26 |
| References found | 307 | 315 |
| Call edges | 133 | 41 |
| **Unresolved** | **44 (14.3%)** | **4 (1.3%)** |
| Resolution rate (code refs) | 85.7% | 98.5% |

*(Revised after the false-link fix below — an earlier run reported 139 Python "project" links. That number was inflated by the same bug found in R: Jedi reports local assignments like `df = df.copy()` as definitions, so a parameter named `df` resolved to a phantom cross-file symbol. Symbols are now taken from `ast`, which is authoritative about what a file actually defines. 54 is the honest count.)*

**Do not read the two columns as a ranking.** They fail differently, and both failure modes are benign — that is the actual finding.

## Python — Jedi passed every case D2 called hard

All four deliberately-planted traps resolved at confidence 1.0:

- **aliased module import** (`from . import utils as u` → `u.log_step(...)`) — resolved
- **method on an inferred type** (`rec = make_recorder()` → `rec.record(...)`, no annotation anywhere) — resolved to `StepRecorder.record`
- **function passed as a value** (`default_steps()` returning `[clean_dataset, add_ratios, ...]`) — resolved
- **cross-file factory** (`make_recorder` imported and called from two modules) — resolved

All 44 unresolved references are **pandas methods on un-annotated `df` parameters** (`df.copy`, `df.drop`, `.mean`, `.std`). They are third-party attribute access, not project symbols — so **they cost zero links in the graph**. Annotating `df: pd.DataFrame` would resolve them; pyright would likely get them without the annotation. Not worth the LSP complexity yet.

→ **Jedi is enough for the MVP.** Revisit pyright only if real agent code (not this fixture) shows project-symbol misses.

## R — the problem is not what I expected

There is no R runtime available here, so R got the *weak* front-end on purpose: tree-sitter spans plus a hand-written scope resolver. It still hit 98.5%, for a reason specific to the language:

**R's `source()`-into-global-env model makes cross-file linking trivial** — there are no imports to follow, so a global name table *is* the resolver. R is structurally *easier* to link than Python, not harder.

Three real bugs surfaced, all now fixed, all worth remembering:

1. **R has no `def`.** `x <- ...` looks identical at top level and inside a function body, so local assignments leaked into the global symbol table and produced **false cross-file links** (`clean_dataset → transform.R::df`, where `df` is a parameter). A false link is worse than a missing one — it actively lies to the reader. Fix: only depth-1 assignments are global definitions.
2. **Closures are scopes too.** `make_recorder()` returns a list of lambdas; their parameters are not visible if you only treat named functions as scopes. Fix: every `function_definition` node opens a scope, and the lexical chain accumulates outward.
3. **Tidyverse NSE is the one genuinely hard part.** `mutate(sepal_ratio = sepal_length / sepal_width)` references *data columns*, which exist nowhere in the source as symbols. Left alone they land in the unresolved bucket and render as dead links across the whole UI. Fix: a new `data` target kind — 48 references (15.2%) are columns, not code, and the UI must style them differently (or resolve them against the frame's schema, which is a *runtime* fact — a natural place for the trace layer to feed the static view).

## D12 confirmed: one consumer, two languages

Both front-ends emit the same records (`Symbol` / `Reference` / `Edge` in `spike/schema.py`). The proof: asked independently, each index reports `log_step` being called from **the same 6 files and the same 13 functions** — the "change one helper, light up everything downstream" case from the project brief.

Nothing downstream of the schema knows which language it is looking at. The seam holds.

## What this changes in the plan

- **Adopt the schema as the contract before building any UI.** It is ~60 lines (`spike/schema.py`) and it is the thing that keeps a Python-shaped resolver out of the core.
- **`resolves_to: null` and `confidence` are load-bearing**, not error handling. 15% of R references are data columns, ~14% of Python references are library attributes. The UI must render *three* states — hard link, soft/low-confidence link, plain text — or it will be full of dead links.
- **Skip LSP for now.** Jedi + tree-sitter clears the bar. LSP remains the escape hatch (D12) if real agent output degrades.

## Postscript: the same bug existed in Python

The R false-link bug (local assignment leaking into the global symbol table) was **also present in Python**, hidden behind Jedi: `get_names(definitions=True)` reports every local assignment *and every imported name* as a definition, so `from .utils import log_step` manufactured a phantom `log_step` symbol in each importing file, and `df = df.copy()` manufactured a phantom `df`.

Generalisation worth keeping: **a resolver's idea of "definition" is not the tool's idea of "definition."** The front-end must decide what counts as a symbol (here: `ast` for Python, depth-1 assignments for R) and use the resolver *only* for bindings. That is D2's split — spans are mechanical, bindings are hard — enforced one level up.

## Caveats

- The fixtures are **synthetic and clean** — hand-written to contain known traps. They are a lower bound on difficulty, not a sample of real agent output. The next honest test is a real multi-file agent-generated project.
- No R runtime was available in the sandbox, so the R front-end is **parse-verified but not execution-verified**; `Rscript run.R` has not been run. The tidyverse dependencies (dplyr, readr, tidyr, purrr, janitor, rlang) are declared but untested.
- Iris data is synthetic (network fetch blocked); it is structurally iris-shaped, not the real measurements. Irrelevant to resolution, relevant if anyone reads the numbers.

---

# Real-code run — `demo_project` (1,506 LOC, FastAPI + SQLite + embeddings)

The fixtures were mine, and seeded with traps I chose. This is the first run against code nobody wrote for the indexer. **It found two bugs the fixtures could not have surfaced, and both were the dangerous kind: confidently wrong, not visibly missing.**

## Scorecard (after the fixes below)

| | |
|---|---|
| symbols / references / edges | 185 · 2,529 · 631 |
| resolved to project | 236 |
| **false links** | **0** |
| unresolved | 257 (10%) |
| DB tables recovered from `cursor.execute()` strings | 9 |
| columns recovered | 64 |
| entry points | 31 |
| genuine dead code found | 1 |
| index time | ~7s |

**Zero false links on real code.** The 10% unresolved are all attribute access on untyped objects — `cur.execute`, `resp.get`, `s.strip`, `conn.commit` — i.e. library calls, not project symbols. They render as plain text, cost no links, and are exactly the failure mode we chose. The honest-failure design held.

## Bug 1 — framework entry points were reported as dead code

30 of 74 functions had "no callers". They were **FastAPI routes** (`@app.get('/api/videos')`). The framework calls them; the call graph cannot see it. Telling a reviewer that a live HTTP endpoint is unreferenced is not a gap, it is a lie — and it is the sort of lie that gets code deleted.

**Fix:** a decorated function, or one referenced from module level (a `__main__` block, a registration call, a route table), is an **entry point** — a first-class state, not an absence. The card says "entry point — invoked by a framework or at module level"; the graph rings them; the outline marks them.

After the fix: 31 entry points, and **1 true orphan** — `_iso` in `migrate_excel.py`, defined and referenced nowhere. Verified by hand. **That is the first thing the tool has told us that we did not already know.**

## Bug 2 — the `dataset` heuristic was far too loose

D13 said "a variable assigned from a call is a data product". On a clean pipeline that reads beautifully. On real code it produced **72 "data products"**, including `conn = connect()` (a DB connection), `c = cursor()`, `n = count()`, `txt = render()`, `url`, `key`, `m`. The data layer was mostly noise.

The error was treating *assignment from a call* as evidence. It is evidence of nothing.

**Fix — the evidence rule.** A name is a data product only if data behaviour was **observed** on it: a column subscript (`x["col"]`), a dataframe-only method (`.groupby`, `.merge`, `.iterrows`), or a trip through a reader/writer. Evidence then **propagates one hop along calls**: a function that does column work on its own parameter is a *data function*, so what flows into it and out of it is data too. That keeps the legitimate chain (`cleaned = clean_dataset(raw)` → `featured` → `scaled`) while refusing `conn = connect()`.

Result: **72 → 3** frame nodes on real code, with the fixture's pipeline chain fully intact. It also silently deleted the two false nodes I had already flagged as known imprecision in D13 (`recorder`, `rec`) — they were the same bug, visible in miniature.

## What the SQL extractor did unprompted

`demo_project` has no `.sql` files. Every query is a Python string in `cursor.execute(...)`. The embedded extractor **recovered the entire database schema anyway** — 9 tables with correct producers and consumers:

```
videos        produced by paste, add_manual, migrate   consumed by ensure_embeddings, _load, score_embeddings…
actors        produced by cleanup, get_or_create_actor consumed by videos_by_ids, actor_info…
embeddings    produced by ensure_embeddings            consumed by _load, delete_video
term_scores   produced by recompute                    consumed by leaderboard
```

This was the strongest result of the run, and it was not designed for — it fell out of D14 being right that a query string is *code*, not text.

## Generalisation worth keeping

Both bugs share a shape: **the tool inferred a strong claim from weak evidence, and stated it with full confidence.** "No callers" from an incomplete call graph. "Data product" from a bare assignment. The fix in both cases was the same — *require evidence proportional to the strength of the claim, and give the absence of evidence its own honest state* (`entry point`, not `dead`; `not data`, not `data`).

That is the same principle as `resolves_to: null` and the `SELECT *` handling. It keeps earning its keep.

## Still not tested

- No R or SQL *files* in this project — the R front-end remains parse-verified but never executed.
- 1.5k LOC is small. Index time ~7s is already noticeable; Jedi does a `goto` per reference, so this will not scale linearly. Caching needed before a 50k-LOC repo.
- No classes with deep inheritance, no metaprogramming, no dynamic imports in this codebase. Those remain untested.

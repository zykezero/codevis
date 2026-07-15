# The A/B eval: does the graph beat handing the model the files?

## The claim under test

Not "the repo is too big to fit". `demo_project` is ~14k tokens — it fits fine.
The claim is narrower and more interesting:

> **Fitting in the context window is not the same as attending to it.**
> A model asked about `scoring.py` has no reason to open `migrate_excel.py`.
> But `recompute()` is defined in `scoring.py` and called from `migrate_excel.py`.
> The index makes that edge explicit, so it cannot be skipped.

Every question is built to require reading a file you had no reason to open.

## Why this is not rigged

- **The answer key never touches the index.** `eval/facts.py` recomputes every
  answer from source with stdlib `ast` and text search. Where it disagrees with
  the index, the index is wrong.
- **Answers are sets; scoring is precision/recall/F1.** Arithmetic, not taste.
- **The index arm gets ~5x LESS text** (3.2k tokens vs 14.5k) and no source
  bodies at all. If it wins, the win came from structure, not volume.
- **Same model, same questions, both arms.**

## Run it

```
python eval/facts.py demo_project        # answer key, from source only
python eval/build_arms.py demo_project   # both context bundles
```
Then in VS Code: **`codevis: Run A/B eval (index vs raw files)`**.

Writes `eval/RESULTS.md` (scores + exactly what each arm missed or invented) and
`eval/results.json`.

## What would falsify the whole project

If `raw` matches or beats `index` on the caller questions, the index is not
earning its place at this scale — and that is worth knowing for the cost of one
eval rather than after building on the assumption.

## The questions

| id | needs |
|---|---|
| `dead_code` | checking every file for every function; one skipped file yields a false "dead" claim |
| `callers_*` | opening files with no topical link to the question |
| `sql_tables` | reading SQL that only exists inside string literals |
| `entry_points` | knowing a call graph cannot see framework-invoked routes |

# Testing the codevis extension

## Prerequisite: Python (the only real one)

The indexer is Python — Jedi is Python-only, and Jedi is why resolution works.
The extension shells out to it. **Install into the interpreter VS Code uses:**

```
pip install jedi sqlglot
```

That is everything you need for Python and SQL projects, which includes
`demo_project`.

**R is optional and currently unavailable on Python 3.12+.** Only the R front-end
uses tree-sitter, and it loads lazily — a Python/SQL project indexes fine without
it. If you want R and you are on Python 3.11 or older:

```
pip install tree_sitter==0.21.3 tree_sitter_languages
```

On 3.12+ `tree_sitter_languages` has no wheel (its last release predates 3.12).
Indexing an R project on 3.12+ fails with an explicit message rather than
mysteriously; a Python-only project is unaffected.

Check it works *before* touching VS Code — if this prints JSON, the hard part is done:

```
python codevis.py demo_project --emit-json
```

If that fails, nothing in the extension will work, and the error will be clearer here.

## Run it

1. Open the **`extension`** folder in VS Code (not the repo root — the launch
   config lives there).
2. Press **F5** → pick **"codevis: demo_project"**.
   A second VS Code window opens (the Extension Development Host) with
   `demo_project` loaded. That window has the extension; your original does not.
3. In the new window: **Ctrl+Shift+P** → `codevis: Open view`.

No `npm install` needed to run: `out/` is pre-compiled and the extension has zero
third-party runtime dependencies. You only need npm if you edit the TypeScript —
then run `npm install && npm run compile` and use the *"recompile + run"* config.

## What to try, in order of what is most likely to be broken

| # | Action | Expected | Notes |
|---|--------|----------|-------|
| 1 | `codevis: Open view` | Panel opens, outline lists ~185 symbols | If this fails, it's the Python subprocess — check the error, it names the fix |
| 2 | Click a function in the outline | Card with signature, params table, source | |
| 3 | Click a blue token in the card | Second card opens, curve links them | |
| 4 | **Flow** tab | Top-down chart of one script; the dropdown switches scripts | Try `selection.py` (4 tiers) or `scoring.py` |
| 5 | **Hover a flow node** | The *real editor* scrolls to that function and flashes it | **This is the VS Code payoff — the thing a browser cannot do** |
| 6 | **Web** tab | Whole-project graph; drag a file box; toggle filters | |
| 7 | **✦ contextualize** on a card | Model explains it; mentioned symbols become clickable | First use triggers a VS Code consent prompt |
| 8 | `codevis: Show change blast radius` | Needs a git repo. `demo_project` is not one yet | |

## Known untested

Everything above #4 has only been tested outside VS Code. The webview handshake,
the `vscode.lm` consent flow, and interpreter discovery have **never actually
run** — there was no VS Code in the environment they were built in. Expect
breakage in exactly those three places first.

## Choosing the model

Contextualize uses whatever you set in **`codevis.model`** (Settings → search
`codevis`). The value is `vendor/family`, e.g.:

```
anthropic/claude-sonnet-4
```

Two ways to set it:

- **Settings UI** — Settings → `codevis` → *Model*. It is a normal setting: visible,
  editable by hand, commitable to a workspace.
- **`codevis: Select language model`** (command palette) — lists the models actually
  registered on your machine and writes your choice into that setting.

The **status bar** (bottom right) always shows which model is live, because a
model choice you cannot see is the bug this replaced: the old code took
`selectChatModels()[0]` — whatever VS Code listed first — with no picker and no
visibility. With Copilot installed that meant Copilot, always, by accident.

codevis never handles your API key. The model list is whatever you have registered
with VS Code (Copilot, or your own key via **Chat: Manage Language Models**).

If the configured model disappears (key revoked, extension removed, typo), codevis
**warns and tells you what it used instead** rather than quietly answering with a
different model than the one you chose.

## Failure modes worth recognising

- **"No Python interpreter found"** → set `codevis.pythonPath` to a full path.
- **"dependencies are missing"** → the message contains the exact pip line. The
  interpreter VS Code picked is probably not the one you installed into.
- **Blank panel** → a webview JS error. Help → Toggle Developer Tools in the
  *Extension Development Host* window, look at the console. (`test_render.py`
  boot-tests this on every build, but the webview bridge is not covered by it.)

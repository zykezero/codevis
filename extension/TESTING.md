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
| 8 | `codevis: Show change blast radius` | Edit a function in `demo_project`, then run it vs `HEAD` | `demo_project` is a subdirectory of the codevis repo, not a repo itself — `git diff --relative` is what makes that work |

## Known untested

The webview handshake and interpreter discovery were built in an environment
with no VS Code and are still lightly exercised. `test_render.py` boot-tests the
viewer JS on every build, but the webview *bridge* is not covered by it.

The `vscode.lm` path **has** now run end to end (Copilot, A/B eval, 12 requests).
What that first real run cost us is recorded under "Choosing the model" below —
every item there is a bug we hit, not a hypothetical.

## Choosing the model

Contextualize uses whatever you set in **`codevis.model`** (Settings → search
`codevis`). The value is `vendor/family`, e.g.:

```
copilot/claude-haiku-4.5
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

### What the model list does not tell you

`selectChatModels()` returns things that look usable and are not. All three of
these cost a failed run before the picker learned to handle them:

- **Advertised ≠ served.** Copilot lists `copilot/copilot-utility` and
  `copilot/copilot-utility-small`, then answers every extension request with
  `400 model_not_supported`. Nothing in the model object predicts this, so the
  picker cannot hide them — but `chat()` now names the provider restriction
  instead of surfacing a raw 400 blob that reads like a codevis bug.
- **Context windows vary by 10×.** `copilot/gpt-4o-mini` reports 12,078 input
  tokens — too small for the eval's raw arm (~14.5k) and marginal for Describe
  on a large function. The picker sorts biggest-first, flags anything under
  20k, and the eval refuses to run rather than report a win by truncation.
- **Names are not identifying.** Two entries are labelled **"Auto"**:
  `copilot/claude-haiku-4.5` (fine) and `copilotcli/` (0 input tokens, unusable).
  The picker now leads with the setting string, not the display name, and hides
  0-token models.

**With the setting unset**, codevis picks the **largest-context** model, not
`models[0]`. "First in the list" is arbitrary, and on a stock Copilot install
the arbitrary choice was a 12k model that could not hold codevis's own prompts —
the default path was the broken path.

**Anthropic sidebar users:** the `anthropic.claude-code` extension registers no
`vscode.lm` provider (no `languageModels` contribution, no
`registerChatModelProvider` call), so its model is not available to this or any
other extension. Copilot is the supported path.

## Failure modes worth recognising

- **"No Python interpreter found"** → set `codevis.pythonPath` to a full path.
- **"dependencies are missing"** → the message contains the exact pip line. The
  interpreter VS Code picked is probably not the one you installed into.
- **Blank panel** → a webview JS error. Help → Toggle Developer Tools in the
  *Extension Development Host* window, look at the console. (`test_render.py`
  boot-tests this on every build, but the webview bridge is not covered by it.)

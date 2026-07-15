#!/usr/bin/env bash
# Assemble the extension from the single source of truth in the repo root: the
# viewer JS/HTML, the Python indexer, and the eval scripts are the SAME code the
# standalone CLI uses. The extension is a host, not a fork.
#
# indexer/, media/app.js, media/template.html and eval/ are BUILD OUTPUTS
# (gitignored). This script rebuilds them from scratch every run — the copy
# targets are removed first, so a stale file can never masquerade as source and
# `cp -r` can never nest a copy inside a previous run's directory.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

# --- clean the copy targets (NOT host.js or src/, which are real source) ------
rm -rf "$HERE/indexer" "$HERE/eval"
rm -f  "$HERE/media/app.js" "$HERE/media/template.html"

# --- viewer -> media/ (consumed by panel.ts) ----------------------------------
mkdir -p "$HERE/media"
cp "$ROOT/viewer/template.html" "$ROOT/viewer/app.js" "$HERE/media/"

# --- indexer -> indexer/ (the Python CLI the extension shells out to) ---------
# codevis.py's render() reads HERE/viewer/, so the CLI needs its own viewer copy
# for the standalone Export HTML command. Copy the two files, not the directory,
# so there is nothing to nest.
mkdir -p "$HERE/indexer/spike" "$HERE/indexer/viewer"
cp "$ROOT/codevis.py" "$HERE/indexer/"
cp "$ROOT/spike/"*.py "$HERE/indexer/spike/"
cp "$ROOT/viewer/template.html" "$ROOT/viewer/app.js" "$HERE/indexer/viewer/"

# --- eval scripts -> eval/ (the A/B command) ----------------------------------
mkdir -p "$HERE/eval"
cp "$ROOT/eval/facts.py" "$ROOT/eval/build_arms.py" "$ROOT/eval/README.md" "$HERE/eval/"

cd "$HERE"
# Prefer the locally-installed compiler; `npx tsc` can resolve to a network
# fetch when the local bin isn't on PATH (and fails offline / on Windows).
# No bundler: the extension has zero runtime dependencies, so tsc output IS
# the shippable artifact and `out/` can be committed pre-compiled.
node node_modules/typescript/bin/tsc -p ./
echo "built: out/extension.js + media/ + indexer/ + eval/"

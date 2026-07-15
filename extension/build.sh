#!/usr/bin/env bash
# Assemble the extension: the viewer JS and the Python indexer are the SAME code
# the standalone CLI uses. The extension is a host, not a fork.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"

mkdir -p "$HERE/media" "$HERE/indexer/spike"
cp "$ROOT/viewer/template.html" "$ROOT/viewer/app.js" "$HERE/media/"
cp "$ROOT/codevis.py" "$HERE/indexer/"
cp "$ROOT/spike/"*.py "$HERE/indexer/spike/"
cp -r "$ROOT/viewer" "$HERE/indexer/viewer"
mkdir -p "$HERE/eval"
cp "$ROOT/eval/facts.py" "$ROOT/eval/build_arms.py" "$ROOT/eval/README.md" "$HERE/eval/"

cd "$HERE"
npx tsc -p ./
echo "built: out/extension.js + media/ + indexer/"

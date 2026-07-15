#!/usr/bin/env python3
"""Regression tests. Run: python test_render.py

The invariants that matter for a code viewer:
  -1. THE EXTENSION'S HTML ASSEMBLY IS INTACT. The suite tested the Python
     renderer and never the extension's, so it stayed green while the extension
     shipped a blank panel: JS String.replace expands `$'`/`$&`/"$`" inside a
     replacement STRING, and indexed source contains those routinely (a regex
     ending in `$'` spliced the template into its own data). Python's str.replace
     does not do this — hence one path worked and the other silently did not.
  0. THE PAGE BOOTS. `node --check` only validates syntax — it cannot catch a
     temporal-dead-zone error ("Cannot access 'X' before initialization"), which
     throws at load and leaves a completely blank page. That shipped twice.
  1. Rendered text is byte-identical to the source (no markup leaks, nothing dropped).
  2. Every reference span lands exactly on the token it claims (code symbols only —
     dataset/column nodes are synthetic and have no source token).
  3. Every link points at a symbol that exists.
  4. Every edge references nodes that exist, and dataflow edges connect the right kinds.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "spike"))
import codevis  # noqa: E402

FIXTURES = [HERE / "fixtures" / "python_demo",
            HERE / "fixtures" / "r_demo",
            HERE / "fixtures" / "warehouse_demo",
            HERE / "fixtures" / "hostile",   # source that attacks the HTML assembly
            HERE / "demo_project"]        # REAL code — the fixtures are too kind

EXT_HARNESS = r"""
// Assemble the page the way the VS Code extension does (panel.ts), and prove the
// result is intact. Guards against: `$`-expansion in replacement strings,
// "</script>" breakout from indexed source, and template tokens inside the
// payload being substituted (chained-replace rescanning).
// KEEP THE ASSEMBLY IN SYNC WITH panel.ts html().
const fs = require('fs'), vm = require('vm');
const [tplP, appP, hostP, idxP] = process.argv.slice(2);
const tpl = fs.readFileSync(tplP, 'utf8');
const app = fs.readFileSync(appP, 'utf8');
const host = fs.existsSync(hostP) ? fs.readFileSync(hostP, 'utf8') : '';
const g = JSON.parse(fs.readFileSync(idxP, 'utf8'));

const parts = {
  __NONCE__: 'testnonce',
  __ROOT__: g.root.replace(/&/g, '&amp;').replace(/</g, '&lt;'),
  __INDEX__: JSON.stringify(g).replace(/</g, '\\u003c'),
  __APP__: app + '\n' + host,
};
const html = tpl.replace(/__(?:NONCE|ROOT|INDEX|APP)__/g, m => parts[m]);

// 1. every script block must still parse — a "</script>" breakout or a spliced
//    replacement corrupts at least one of them
const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
let bad = 0;
for (const b of blocks) { try { new vm.Script(b); } catch (e) { bad++; } }

// 2. the app block must be present VERBATIM at its slot — proves __APP__ in the
//    template was substituted, not an occurrence inside the JSON payload
const appOk = html.includes(parts.__APP__) ? 0 : 1;

// 3. the embedded INDEX must round-trip byte-identically — proves no splicing,
//    no $-expansion, and that < escaping did not alter the data
let roundtrip = 1;
try {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(blocks[0] + '\n; globalThis.INDEX = INDEX;', ctx);
  roundtrip = JSON.stringify(ctx.INDEX) === JSON.stringify(g) ? 0 : 1;
} catch (e) { roundtrip = 1; }

console.log(JSON.stringify({ syntax: bad, appOk, roundtrip, blocks: blocks.length }));
"""

BOOT_HARNESS = r"""
// Execute the viewer's real JS top-to-bottom in a stub DOM. Any exception at
// load time — TDZ, bad reference, wrong declaration order — fails the test.
const fs=require('fs'), vm=require('vm');
const store={};
const mk=(tag)=>({ tag, innerHTML:'', dataset:{}, className:'', style:{}, attrs:{},
  classList:{toggle(){},add(){},remove(){},contains:()=>false},
  setAttribute(){}, getAttribute(){}, appendChild(){}, addEventListener(){},
  removeEventListener(){}, querySelector:()=>mk(), querySelectorAll:()=>[],
  closest:()=>null, after(){}, insertBefore(){},
  getBoundingClientRect:()=>({left:0,top:0,width:0,height:0}),
  clientWidth:1200, clientHeight:800, remove(){}, offsetWidth:1, contains:()=>false });
// A stub faithful enough that a card genuinely exists after openRoot.
const made = [];
const document={ getElementById:(id)=>(store[id]||=mk(id)),
  createElement:(t)=>{ const e=mk(t); made.push(e); return e; }, createElementNS:mk,
  querySelectorAll:(sel)=> sel && sel.includes('.card') ? made.filter(e=>/card/.test(e.className)) : [],
  querySelector:(sel)=> sel && sel.includes('.card')
      ? made.find(e=>/card/.test(e.className)) || null : mk(),
  addEventListener(){},
  body:{...mk('body'), contains:()=>true, appendChild(){}, dataset:{}} };
const ctx={ console:{log(){},warn(){},error(){}}, document,
  localStorage:{getItem:()=>null,setItem(){},removeItem(){}},
  window:{addEventListener(){},innerWidth:1400,innerHeight:900},
  requestAnimationFrame:()=>1, Blob:function(){},
  URL:{createObjectURL:()=>'',revokeObjectURL(){}}, FileReader:function(){} };
vm.createContext(ctx);
try {
  vm.runInContext(fs.readFileSync(process.argv[2],'utf8'), ctx);
  // exercise the view the user actually lands on, and the graph
  // Exercise every view AND actually open a card. The old test only switched
  // views; setView("cards") with nothing selected renders the welcome screen and
  // never calls cardHTML — so an undefined `CALLS` inside cardHTML shipped, and
  // no card ever rendered. A smoke test that never touches the main surface is
  // not a smoke test.
  vm.runInContext(`
    setView("web"); setView("flow"); setView("cards");
    const fn = INDEX.symbols.find(s => s.kind === "function" || s.kind === "method");
    if (!fn) throw new Error("no function to open a card for");
    openRoot(fn.id);
    if (!document.querySelector(".card")) throw new Error("openRoot produced no card");
    const html = cardHTML(fn, null);
    if (!html || html.length < 50) throw new Error("cardHTML returned nothing");
    const linked = INDEX.references.find(r => r.resolves_to && r.enclosing === fn.id);
    if (linked) openCard(linked.resolves_to, fn.name, 100, 100, null);
  `, ctx);
  console.log(JSON.stringify({boot: "ok"}));
} catch (e) {
  console.log(JSON.stringify({boot: "FAILED", error: e.message}));
}
"""

NODE_HARNESS = r"""
const fs=require('fs'), vm=require('vm');
const strip = h => h.replace(/<pre class="code">/g,'')
  .replace(/<div class="ln"><div class="no">\d+<\/div><div class="src">/g,'')
  .replace(/<\/div><\/div>/g,'\n').replace(/<\/pre>/g,'').replace(/<[^>]+>/g,'')
  .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&nbsp;/g,'');
const ctx={console}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8') +
  '\n; globalThis.INDEX=INDEX; globalThis.renderSpan=renderSpan;', ctx);
let bad=0, leaks=0;
for (const s of ctx.INDEX.symbols) {
  if (!s.body) continue;
  if (s.kind === 'dataset' || s.kind === 'column') continue;   // synthetic
  const b=s.body;
  const seen = strip(ctx.renderSpan(b.file,b.start_line,b.end_line)).replace(/\s+$/,'');
  const want = ctx.INDEX.files[b.file].split('\n')
                 .slice(b.start_line-1,b.end_line).join('\n').replace(/\s+$/,'');
  if (seen !== want) { bad++; console.log('MISMATCH ' + s.id); }
  if (/class="|<span/.test(seen)) { leaks++; console.log('LEAK ' + s.id); }
}
console.log(JSON.stringify({bad, leaks, symbols: ctx.INDEX.symbols.length}));
"""


def main():
    failures = 0
    for fx in FIXTURES:
        try:
            idx = codevis.build(fx)
        except SystemExit as e:
            # A language front-end's optional dependency is missing on this
            # machine (codevis itself already skips-and-warns per language; a
            # single-language fixture then has nothing left). Same rule here:
            # skip loudly rather than fail the whole suite.
            print(f"[SKIP] {fx.name:<14} {e}")
            continue
        lang = idx.language

        # --- determinism: the same source must index identically across runs ---
        # (an arbitrary winner pulled out of a set once made produces/consumes
        # edges flip between same-named functions). Two COLD processes, because
        # that is what re-running actually is — and each gets a fresh hash seed,
        # which is exactly the thing that reorders unsorted set iteration. An
        # in-process rebuild would test the wrong thing: jedi's warm caches
        # legitimately resolve a few more external names on a second pass.
        runs = []
        for _ in range(2):
            r = subprocess.run([sys.executable, str(HERE / "codevis.py"),
                                str(fx), "--emit-json"],
                               capture_output=True, text=True)
            runs.append(r.stdout)
        deterministic = bool(runs[0].strip()) and runs[0] == runs[1]

        # --- 2. span alignment -------------------------------------------------
        misaligned = 0
        for r in idx.references:
            line = idx.files[r.span.file].split("\n")[r.span.start_line - 1]
            if line[r.span.start_col:r.span.end_col] != r.text:
                misaligned += 1
        # Span-text equality only applies to symbols whose NAME is a literal token
        # in the source. Three kinds are exempt:
        #   - dataset/column nodes are synthetic (they name a data product)
        #   - `module` nodes are synthetic (the file's import-time body; "<module>"
        #     is not a token anywhere)
        #   - SQL statements are named by what they CREATE
        #     ("create_table_processed_iris"), which is not a token in the file.
        for s in idx.symbols:
            if s.kind in ("dataset", "column", "module"):
                continue
            if idx.langs.get(s.span.file) == "sql":
                continue
            line = idx.files[s.span.file].split("\n")[s.span.start_line - 1]
            if line[s.span.start_col:s.span.end_col] != s.name:
                misaligned += 1

        # --- 3. no dangling links ---------------------------------------------
        ids = {s.id for s in idx.symbols}
        dangling = sum(1 for r in idx.references
                       if r.target_kind == "project" and r.resolves_to not in ids)
        dangling += sum(1 for e in idx.edges
                        if e.from_symbol not in ids or e.to_symbol not in ids)

        # --- 0 + 1. boot, then rendered text == source (the viewer's real JS) ---
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "v.html"
            codevis.render(idx, out)
            scripts = re.findall(r"<script[^>]*>(.*?)</script>",
                                 out.read_text(encoding="utf8"), re.S)

            # -1. the EXTENSION's assembly (panel.ts), not just the Python one
            idxf = Path(td) / "idx.json"
            idxf.write_text(json.dumps(idx.to_dict()), encoding="utf8")
            eh = Path(td) / "ext.js"
            eh.write_text(EXT_HARNESS, encoding="utf8")
            # template/app from viewer/ (the source of truth — extension/media is
            # a gitignored build copy); host.js is real source and lives in media.
            viewer = HERE / "viewer"
            host_js = HERE / "extension" / "media" / "host.js"
            er = subprocess.run(
                ["node", str(eh), str(viewer / "template.html"), str(viewer / "app.js"),
                 str(host_js), str(idxf)],
                capture_output=True, text=True)
            try:
                ext = json.loads(er.stdout.strip().split("\n")[-1])
            except Exception:
                ext = {"syntax": 99, "appOk": 99, "roundtrip": 99}

            # 0. does the page even boot?
            whole = Path(td) / "whole.js"
            whole.write_text(scripts[0] + "\n" + scripts[1], encoding="utf8")
            bh = Path(td) / "boot.js"
            bh.write_text(BOOT_HARNESS, encoding="utf8")
            br = subprocess.run(["node", str(bh), str(whole)], capture_output=True, text=True)
            boot = json.loads(br.stdout.strip().split("\n")[-1]) if br.stdout.strip() \
                else {"boot": "FAILED", "error": br.stderr.strip()[:120]}

            # 1. rendering fidelity
            js = Path(td) / "idx.js"
            js.write_text(scripts[0], encoding="utf8")
            harness = Path(td) / "h.js"
            harness.write_text(NODE_HARNESS, encoding="utf8")
            res = subprocess.run([ "node", str(harness), str(js) ],
                                 capture_output=True, text=True)
            tail = res.stdout.strip().split("\n")[-1]
            render = json.loads(tail)

        # --- 4. dataflow edges connect sane node kinds -------------------------
        kind_of = {s.id: s.kind for s in idx.symbols}
        EXPECT = {
            # `module` is a legitimate caller: code at the top of a file runs on
            # import and really does call things.
            "calls":         ({"function", "method", "class", "module"}, {"function", "method", "class"}),
            "reads":         ({"function", "method", "class", "module"}, {"variable"}),
            "produces":      ({"function", "method"},          {"dataset"}),
            "consumes":      ({"dataset"},                     {"function", "method"}),
            "reads_column":  ({"function", "method"},          {"column"}),
            "writes_column": ({"function", "method"},          {"column"}),
            "has_column":    ({"dataset"},                     {"column"}),
        }
        badedge = 0
        for e in idx.edges:
            exp = EXPECT.get(e.kind)
            if not exp:
                continue
            if kind_of.get(e.from_symbol) not in exp[0] or kind_of.get(e.to_symbol) not in exp[1]:
                badedge += 1

        ext_ok = ext["syntax"] == 0 and ext.get("appOk") == 0 and ext.get("roundtrip") == 0
        ok = (misaligned == 0 and dangling == 0 and badedge == 0
              and render["bad"] == 0 and render["leaks"] == 0
              and boot["boot"] == "ok" and ext_ok and deterministic)
        failures += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {fx.name:<14} {lang:<7} "
              f"{render['symbols']:>3} symbols | boot: {boot['boot']:<6} | "
              f"ext-html: {'ok' if ext_ok else 'BROKEN'} | "
              f"deterministic: {'yes' if deterministic else 'NO'} | "
              f"spans: {misaligned} | dangling: {dangling} | "
              f"edges: {badedge} | text: {render['bad']} | leaks: {render['leaks']}")
        if boot["boot"] != "ok":
            print(f"         BOOT ERROR: {boot.get('error')}")

    print("\n" + ("all green" if not failures else f"{failures} fixture(s) failing"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

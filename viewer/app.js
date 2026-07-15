// Declared FIRST: `const` is hoisted but not initialised, so any use above this
// line throws "Cannot access before initialization" and blanks the whole page.
// This exact bug shipped twice. There is now a boot smoke-test in test_render.py.
const HAS_DIFF = INDEX.symbols.some(s => s.changed) || !!INDEX.diff_ref;
let IMPACT_ONLY = false;      // dim everything outside the blast radius

// Every SITE a symbol is referenced from — not just which function, but where.
// (CALLERS answers "which functions"; this answers "which lines".)
const REF_SITES = {};
for (const r of INDEX.references) {
  if (r.resolves_to) (REF_SITES[r.resolves_to] ||= []).push(r);
}

// ---------- outline ---------------------------------------------------------
// The outline used to be a flat list under each file, which quietly assumed the
// reader already knew what this tool counts as a "symbol". Name the groups and the
// structure explains itself — no legend required.
const KIND_MARK = { function: 'ƒ', method: 'm', class: 'C', variable: '=',
                    dataset: '▤', column: '│', module: '▭' };

const KIND_GROUP = [
  { kind: 'class',    label: 'classes',   hint: 'class definitions in this file' },
  { kind: 'function', label: 'functions', hint: 'functions defined at the top level of this file' },
  { kind: 'method',   label: 'methods',   hint: 'functions defined inside a class' },
  { kind: 'variable', label: 'constants', hint: 'module-level values other code reads' },
];

// Data products are GLOBAL BY NAME, not file-scoped: a table written in one file
// and read in three others is one thing, so it cannot sit under a file heading.
// That is exactly what makes them connective — and why they get their own section.
const DATA_GROUPS = [
  { kind: 'dataset', label: 'data products',
    hint: 'tables and files — written by one place, read by others' },
  { kind: 'column',  label: 'columns',
    hint: 'fields referenced in code or SQL, shared across files' },
];

const byFile = {};
for (const s of INDEX.symbols) {
  if (s.kind === 'dataset' || s.kind === 'column') continue;   // global, not per-file
  // A file's `<module>` node exists so import-time calls have a caller to hang
  // off. It is a graph fixture, not something to read — it has no body span,
  // so an outline row for it would open an empty card.
  if (s.kind === 'module') continue;
  (byFile[s.span.file] ||= []).push(s);
}

function symRow(s) {
  const n = CALLERS[s.id] ? CALLERS[s.id].size : 0;
  const dead = !n && !s.entry && (s.kind === 'function' || s.kind === 'method');
  const impMark = !HAS_DIFF ? '' :
    s.changed ? '<span class="chgdot" title="changed">●</span>'
              : (s.impact > 0 ? `<span class="impdot i${Math.min(s.impact, 4)}"
                   title="${s.impact} hop(s) from a change">●</span>` : '');
  const el = document.createElement('div');
  el.className = 'sym';
  el.dataset.sym = s.id;
  el.innerHTML =
    `<span class="k" title="${s.kind}">${KIND_MARK[s.kind] || '·'}</span>` +
    `<span class="n">${s.name}</span>` + impMark +
    (s.entry ? '<span class="entrydot" title="entry point — invoked by a framework or at import time, not called by project code">▸</span>' : '') +
    (dead ? '<span class="deaddot" title="nothing in this project references it">·dead?</span>' : '') +
    (n ? `<span class="callers" title="${n} caller${n > 1 ? 's' : ''}">${n}↩</span>` : '');
  el.onclick = () => openRoot(s.id);
  return el;
}

function subGroup(label, hint, syms) {
  if (!syms.length) return null;
  const g = document.createElement('div');
  g.className = 'kind-group';
  g.innerHTML = `<div class="kind-name" title="${hint}">${label}
                   <span class="kind-n">${syms.length}</span></div>`;
  syms.forEach(s => g.appendChild(symRow(s)));
  return g;
}

function buildOutline(filter = '') {
  const tree = document.getElementById('tree');
  tree.innerHTML = '';
  const f = filter.trim().toLowerCase();
  const match = s => !f || s.name.toLowerCase().includes(f);
  let shown = 0;

  // ---- per file: grouped BY KIND, each group named ------------------------
  for (const file of Object.keys(byFile).sort()) {
    const syms = byFile[file].filter(match);
    if (!syms.length) continue;
    shown += syms.length;

    const g = document.createElement('div');
    g.className = 'file-group';
    g.innerHTML = `<div class="file-name" data-file="${file}"
                        title="open the flowchart for this script">
                     <span class="fico">⌥</span>${file}
                   </div>`;
    for (const grp of KIND_GROUP) {
      const of = syms.filter(s => s.kind === grp.kind)
                     .sort((a, b) => a.span.start_line - b.span.start_line);
      const el = subGroup(grp.label, grp.hint, of);
      if (el) g.appendChild(el);
    }
    g.querySelector('.file-name').addEventListener('click', () => {
      FLOW_FILE = file;
      setView('flow');
      markFlowFile();
    });
    tree.appendChild(g);
  }

  // ---- data products: global, so they get their own section --------------
  for (const grp of DATA_GROUPS) {
    const syms = INDEX.symbols.filter(s => s.kind === grp.kind).filter(match)
                              .sort((a, b) => a.name.localeCompare(b.name));
    if (!syms.length) continue;
    shown += syms.length;
    const g = document.createElement('div');
    g.className = 'file-group data-group';
    g.innerHTML = `<div class="file-name is-data" title="${grp.hint}">
                     <span class="fico">◇</span>${grp.label}
                   </div>`;
    syms.forEach(s => {
      const el = symRow(s);
      el.onclick = () => { const sym = SYMS.get(s.id); if (sym) showDataPanel(sym); };
      g.appendChild(el);
    });
    tree.appendChild(g);
  }

  if (f) {
    const n = document.createElement('div');
    n.className = 'filter-count';
    n.textContent = shown ? `${shown} match${shown === 1 ? '' : 'es'}` : 'no matches';
    tree.prepend(n);
  }
  markFlowFile();
}

/** Show which script the Flow view is currently charting. */
function markFlowFile() {
  document.querySelectorAll('.file-name').forEach(el =>
    el.classList.toggle('charting',
      document.body.dataset.view === 'flow' && el.dataset.file === FLOW_FILE));
}

document.getElementById('q').addEventListener('input', e => buildOutline(e.target.value));

// ---------- cards -----------------------------------------------------------
const stage = document.getElementById('stage');
const WELCOME = stage.innerHTML;
let z = 10, cascade = 0;

// One card per symbol. Opening ten copies of log_step is exactly the
// "lost my place" problem this tool exists to prevent.
const OPEN = new Map();      // symbol id -> card element

const Z_TETHER = 90000;      // the thread layer sits above every card

function surface(card) {
  if (z > Z_TETHER - 1000) z = 10;     // cards must never climb over the threads
  card.style.zIndex = ++z;
  card.classList.remove('pulse');
  void card.offsetWidth;              // force reflow so the animation replays
  card.classList.add('pulse');
}

function cardHTML(sym, fromName) {
  const b = sym.body || sym.span;
  const callers = CALLERS[sym.id] ? [...CALLERS[sym.id]] : [];
  const nSites = (REF_SITES[sym.id] || []).length;
  const nFiles = new Set((REF_SITES[sym.id] || []).map(r => r.span.file)).size;
  // "no callers" is a lie for a framework route. Say what is actually true.
  const callerTxt = callers.length
    ? `called by ${callers.map(c => (SYMS.get(c) || {}).name || c.split('::').pop()).join(', ')}`
    : sym.entry
      ? '<span class="entrytag">entry point</span> invoked by a framework or at module level — not by project code'
      : '<span class="deadtag">no callers</span> nothing in this project references it';
  const deps = (CALLS[sym.id] || []).map(id => SYMS.get(id)).filter(Boolean);
  const dependents = callers.map(id => SYMS.get(id)).filter(Boolean);
  const chips = (list, label) => list.length ? `
      <div class="chiprow"><span class="chiplbl">${label}</span>
        ${list.map(s => `<span class="chip ${s.kind}" data-chip="${s.id}">${s.name}</span>`).join('')}
      </div>` : '';

  return `
    <div class="card-head">
      <span class="t">${sym.name}</span>
      <span class="p">${b.file}:${b.start_line}</span>
      ${fromName ? `<span class="from">← from ${fromName}</span>` : ''}
      <span class="usedby" data-refs
            title="every location this is referenced from">
        used by ${nSites}${nFiles > 1 ? ` · ${nFiles} files` : ''}
      </span>
      <span class="editbtn" data-edit title="edit this function and write it back to the file">✎ edit</span>
      <span class="ctxbtn" data-ctx title="describe what this does and how it affects the caller — uses the resolved graph as context">✦ describe</span>
      <span class="x" data-close>✕</span>
    </div>
    ${sym.signature ? `<div class="card-sig">${esc(sym.signature)}</div>` : ''}
    ${sym.doc ? `<div class="card-doc">${sym.doc}</div>` : ''}
    ${impactBanner(sym)}
    ${paramTable(sym)}
    <div id="${ctxBoxId(sym.id)}" class="ctxbox"></div>
    ${chips(deps, 'calls')}
    ${chips(dependents, 'called by')}
    <div class="card-body">${renderSpan(b.file, b.start_line, b.end_line)}</div>`;
}

/** The card's source, verbatim — what an edit starts from and diffs against. */
function sourceOf(sym) {
  const b = sym.body || sym.span;
  const src = INDEX.files[b.file];
  if (src === undefined) return '';
  return src.split('\n').slice(b.start_line - 1, b.end_line).join('\n');
}

function paramTable(sym) {
  if (!sym.params || !sym.params.length) return '';
  const rows = sym.params.map(p => `<tr>
      <td class="pn">${esc(p.name)}</td>
      <td class="pa">${p.annotation ? esc(p.annotation) : '<span class="dim">—</span>'}</td>
      <td class="pd">${p.default ? esc(p.default) : '<span class="dim">—</span>'}</td>
    </tr>`).join('');
  return `<div class="ptabwrap"><table class="ptab">
    <tr><th>param</th><th>type</th><th>default</th></tr>${rows}</table></div>`;
}

function impactBanner(sym) {
  if (!HAS_DIFF) return '';
  if (sym.changed)
    return `<div class="card-doc impact changed">changed vs
              <code>${INDEX.diff_ref || 'HEAD'}</code> — this edit is the source</div>`;
  if (sym.impact > 0)
    return `<div class="card-doc impact hop${Math.min(sym.impact, 4)}">
              in the blast radius · ${sym.impact} hop${sym.impact > 1 ? 's' : ''} from a change</div>`;
  return '';
}

// Every location this symbol is referenced from, as clickable rows.
function openRefPanel(sym) {
  const sites = (REF_SITES[sym.id] || [])
    .slice()
    .sort((a, b) => (a.span.file + a.span.start_line).localeCompare(b.span.file + b.span.start_line));

  const rows = sites.map((r, i) => {
    const src = (INDEX.files[r.span.file] || '').split('\n')[r.span.start_line - 1] || '';
    const before = esc(src.slice(0, r.span.start_col));
    const tok = esc(src.slice(r.span.start_col, r.span.end_col));
    const after = esc(src.slice(r.span.end_col));
    const encl = r.enclosing ? (SYMS.get(r.enclosing) || {}).name : null;
    return `<div class="refrow" data-i="${i}">
        <div class="refloc">
          <span class="mono">${r.span.file}:${r.span.start_line}</span>
          ${encl ? `<span class="refin">in ${encl}</span>` : '<span class="refin">module level</span>'}
        </div>
        <div class="refsrc mono">${before.trimStart()}<b>${tok}</b>${after}</div>
      </div>`;
  }).join('') || '<div class="refrow"><div class="refloc">no references found</div></div>';

  const id = 'refs::' + sym.id;
  const existing = OPEN.get(id);
  if (existing && document.body.contains(existing)) { surface(existing); return; }

  const c = document.createElement('div');
  c.className = 'card refs';
  c.dataset.sym = id;
  c.style.left = Math.min(window.innerWidth - 620, 400) + 'px';
  c.style.top = '110px';
  c.innerHTML = `
    <div class="card-head">
      <span class="t">${sym.name}</span>
      <span class="p">used by · ${sites.length} reference${sites.length === 1 ? '' : 's'}
        in ${new Set(sites.map(s => s.span.file)).size} file(s)</span>
      <span class="x" data-close>✕</span>
    </div>
    <div class="card-body">${rows}</div>`;
  document.body.appendChild(c);
  OPEN.set(id, c);
  wire(c);
  surface(c);

  // clicking a row opens the code AT that call site
  c.querySelectorAll('.refrow').forEach(row => {
    row.addEventListener('click', ev => {
      const r = sites[+row.dataset.i];
      if (!r) return;
      if (r.enclosing && SYMS.get(r.enclosing)) {
        const card = openCard(r.enclosing, sym.name, ev.clientX, ev.clientY, row);
        if (card) flashLine(card, r.span.start_line);
      } else {
        openSnippet(r.span, sym.name, ev.clientX, ev.clientY, row);
      }
    });
  });
}

// scroll a card to a line and flash it
function flashLine(card, line) {
  const rows = card.querySelectorAll('.ln');
  for (const el of rows) {
    if (el.querySelector('.no')?.textContent.trim() === String(line)) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      el.classList.remove('flash');
      void el.offsetWidth;
      el.classList.add('flash');
      return;
    }
  }
}

// a reference outside any function (module level) — show it in context
function openSnippet(span, fromName, x, y, originEl) {
  const id = 'snip::' + span.file + ':' + span.start_line;
  const existing = OPEN.get(id);
  if (existing && document.body.contains(existing)) { surface(existing); return; }
  const a0 = Math.max(1, span.start_line - 3);
  const b0 = span.start_line + 3;
  const c = document.createElement('div');
  c.className = 'card';
  c.dataset.sym = id;
  c.style.left = Math.max(280, Math.min(x + 24, window.innerWidth - 640)) + 'px';
  c.style.top = Math.max(56, y + 12) + 'px';
  c.innerHTML = `
    <div class="card-head">
      <span class="t">${span.file}</span>
      <span class="p">line ${span.start_line}</span>
      <span class="from">← from ${fromName}</span>
      <span class="x" data-close>✕</span>
    </div>
    <div class="card-body">${renderSpan(span.file, a0, b0)}</div>`;
  document.body.appendChild(c);
  OPEN.set(id, c);
  wire(c);
  surface(c);
  if (originEl) { setOrigin(c, originEl); startTethers(); }
  flashLine(c, span.start_line);
}

function openRoot(symId) {
  const sym = SYMS.get(symId);
  if (!sym) return;
  // Opening a card IS the card view. Without this, clicking a symbol while the
  // Flow or Web tab is active silently replaced the chart with a card while the
  // tab still read "Flow" — the view and the content disagreed.
  if (document.body.dataset.view !== 'cards') setView('cards', symId);
  stage.innerHTML = '';
  cascade = 0;
  const c = document.createElement('div');
  c.className = 'card root';
  c.dataset.sym = symId;
  c.innerHTML = cardHTML(sym, null);
  stage.appendChild(c);
  OPEN.set(symId, c);
  document.querySelectorAll('.sym').forEach(e =>
    e.classList.toggle('active', e.dataset.sym === symId));
  wire(c);
}

// D4: a pop-up card re-runs the SAME reference pass, so links inside it
// behave identically. Nesting needs no special case.
function openCard(symId, fromName, x, y, originEl) {
  const sym = SYMS.get(symId);
  if (!sym) return;

  const existing = OPEN.get(symId);
  if (existing && document.body.contains(existing)) {
    surface(existing);                 // already open — resurface, don't duplicate
    if (originEl) { setOrigin(existing, originEl); startTethers(); }
    return existing;
  }

  const c = document.createElement('div');
  c.className = 'card';
  c.style.zIndex = ++z;
  cascade = (cascade + 1) % 8;
  const sx = Math.min(x + 24, window.innerWidth - 640);
  const sy = Math.min(y + 12 + cascade * 6, window.innerHeight - 220);
  c.style.left = Math.max(280, sx) + 'px';
  c.style.top = Math.max(56, sy) + 'px';
  c.innerHTML = cardHTML(sym, fromName);
  c.dataset.sym = symId;
  // remember the caller: Contextualize explains the target IN THE CALLER'S context
  const originCard = originEl?.closest?.('.card');
  if (originCard?.dataset.sym) c.dataset.openedFrom = originCard.dataset.sym;
  document.body.appendChild(c);
  OPEN.set(symId, c);
  wire(c);
  surface(c);
  if (originEl) { setOrigin(c, originEl); startTethers(); }
  return c;
}

function wire(card) {
  card.querySelector('[data-close]')?.addEventListener('click', () => {
    if (card.dataset.sym) OPEN.delete(card.dataset.sym);
    clearOrigin(card);
    card.remove();
    drawTethers();
  });

  card.addEventListener('mousedown', () => { card.style.zIndex = ++z; });

  card.querySelectorAll('[data-chip]').forEach(el => el.addEventListener('click', ev => {
    ev.stopPropagation();
    openCard(el.dataset.chip, card.querySelector('.card-head .t')?.textContent,
             ev.clientX, ev.clientY, el);
  }));
  wireContextualize(card);
  wireEdit(card);

  card.querySelector('[data-refs]')?.addEventListener('click', () => {
    const s = SYMS.get(card.dataset.sym || '');
    if (s) openRefPanel(s);
  });

  card.querySelectorAll('.r-project').forEach(el => {
    el.addEventListener('click', ev => {
      ev.stopPropagation();
      const owner = card.querySelector('.card-head .t')?.textContent;
      openCard(el.dataset.sym, owner, ev.clientX, ev.clientY, el);   // el = the tether origin
    });
  });

  // tooltips for the non-link states — so nothing looks broken
  const tip = document.getElementById('tip');
  card.querySelectorAll('[data-why]').forEach(el => {
    el.addEventListener('mouseenter', ev => {
      tip.textContent = el.dataset.why;
      tip.style.left = (ev.clientX + 12) + 'px';
      tip.style.top = (ev.clientY + 16) + 'px';
      tip.style.opacity = 1;
    });
    el.addEventListener('mouseleave', () => { tip.style.opacity = 0; });
  });

  // drag by the header (floating cards only)
  const head = card.querySelector('.card-head');
  if (card.classList.contains('root') || !head) return;
  let dx = 0, dy = 0, dragging = false;
  head.addEventListener('mousedown', e => {
    if (e.target.hasAttribute('data-close')) return;
    dragging = true;
    dx = e.clientX - card.offsetLeft;
    dy = e.clientY - card.offsetTop;
    head.style.cursor = 'grabbing';
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    card.style.left = (e.clientX - dx) + 'px';
    card.style.top = (e.clientY - dy) + 'px';
  });
  window.addEventListener('mouseup', () => { dragging = false; head.style.cursor = 'grab'; });
}

// esc closes the topmost floating card
window.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const cards = [...document.querySelectorAll('.card:not(.root)')];
  const top = cards.sort((a, b) => (+a.style.zIndex || 0) - (+b.style.zIndex || 0)).pop();
  if (top) {
    if (top.dataset.sym) OPEN.delete(top.dataset.sym);
    clearOrigin(top);
    top.remove();
    drawTethers();
  }
});

// ============================================================================
// EDITING — change a function in its card, write it back to the real file.
//
// The card shows a snapshot taken at index time. The file is the truth. So the
// extension re-hashes what is actually at that span before writing and refuses on
// a mismatch — a stale span would silently overwrite whatever is there now.
// Outside VS Code there is no file to write to, and the button says so.
// ============================================================================
const EDITING = new Map();   // card element -> original text

function wireEdit(card) {
  const btn = card.querySelector('[data-edit]');
  if (!btn) return;
  btn.addEventListener('click', () => {
    if (EDITING.has(card)) { cancelEdit(card); return; }
    if (!IN_VSCODE) {
      const box = document.getElementById(ctxBoxId(card.dataset.sym));
      if (box) setCtxState(card.dataset.sym, 'error',
        'Editing writes to files, which this standalone HTML cannot do. ' +
        'Open the workspace in VS Code with the codevis extension.');
      return;
    }
    beginEdit(card);
  });
}

function beginEdit(card) {
  const sym = SYMS.get(card.dataset.sym);
  if (!sym) return;
  const body = card.querySelector('.card-body');
  const original = sourceOf(sym);
  EDITING.set(card, { original, html: body.innerHTML });

  const b = sym.body || sym.span;
  body.innerHTML = `
    <div class="editwrap">
      <div class="editbar">
        <span class="editfile">${b.file}:${b.start_line}–${b.end_line}</span>
        <span class="editnote">writes back to the file · undoable in the editor</span>
        <span class="ebtn cancel" data-cancel>cancel</span>
        <span class="ebtn save" data-save>save to file</span>
      </div>
      <textarea class="editta" spellcheck="false"></textarea>
    </div>`;
  const ta = body.querySelector('.editta');
  ta.value = original;                       // .value, never innerHTML — no escaping bugs
  ta.focus();
  card.classList.add('editing');
  card.querySelector('[data-edit]').textContent = '✕ cancel edit';

  const save = () => {
    if (ta.value === original) { cancelEdit(card); return; }
    body.querySelector('.editbar').classList.add('saving');
    window.dispatchEvent(new CustomEvent('codevis:applyEdit', {
      detail: { id: card.dataset.sym, text: ta.value }
    }));
  };
  body.querySelector('[data-save]').addEventListener('click', save);
  body.querySelector('[data-cancel]').addEventListener('click', () => cancelEdit(card));

  // Ctrl/Cmd+S is the reflex — honour it rather than making people hunt the button
  ta.addEventListener('keydown', ev => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 's') { ev.preventDefault(); save(); }
    if (ev.key === 'Escape') { ev.stopPropagation(); cancelEdit(card); }
  });
}

function cancelEdit(card) {
  const st = EDITING.get(card);
  if (!st) return;
  const body = card.querySelector('.card-body');
  body.innerHTML = st.html;
  EDITING.delete(card);
  card.classList.remove('editing');
  const btn = card.querySelector('[data-edit]');
  if (btn) btn.textContent = '✎ edit';
  wire(card);
}

function editApplied(id) {
  // Do not patch the card by hand: the extension re-indexes and re-renders the
  // whole panel, so the view comes back from the file rather than from our guess
  // about what the file now says.
  for (const [card] of EDITING) {
    if (card.dataset.sym === id) EDITING.delete(card);
  }
}

function editFailed(id, msg) {
  const card = [...document.querySelectorAll('.card')].find(c => c.dataset.sym === id);
  if (!card) return;
  const bar = card.querySelector('.editbar');
  if (bar) {
    bar.classList.remove('saving');
    bar.classList.add('failed');
    let n = bar.querySelector('.editerr');
    if (!n) { n = document.createElement('div'); n.className = 'editerr'; bar.after(n); }
    n.textContent = msg;
  }
}

// ---------- boot ------------------------------------------------------------
const nRefs = INDEX.references.length;
const nLinks = INDEX.references.filter(r => r.target_kind === 'project').length;
if (HAS_DIFF) {
  const n = INDEX.symbols.filter(s => s.changed).length;
  const r = INDEX.symbols.filter(s => s.impact > 0).length;
  const b = document.createElement('div');
  b.className = 'diffbtn';
  b.innerHTML = `<span class="chgdot">●</span> ${n} changed · ${r} downstream`;
  b.onclick = openImpactPanel;
  document.querySelector('header .vtabs')?.after(b);
}

const langList = [...new Set(Object.values(INDEX.langs || {}))].join(' + ') || INDEX.language;
document.getElementById('meta').textContent =
  `${INDEX.root} · ${langList} · ${INDEX.symbols.length} symbols · ` +
  `${INDEX.edges.length} edges · ${nLinks}/${nRefs} refs linked`;
// boot moved to the end of this file — see note there


// ============================================================================
// WEB VIEW — the holistic graph.
// Layout is file-clustered on purpose: files are the unit people navigate by,
// so the graph keeps the nav bar's grouping and shows the lines BETWEEN scripts.
// ============================================================================
const EDGE_KINDS = {
  calls:         { color: '#6fa8ff', label: 'calls',        on: true },
  reads:         { color: '#8b93a7', label: 'reads const',   on: false },
  produces:      { color: '#57d9a3', label: 'produces',     on: true },
  consumes:      { color: '#d9a35a', label: 'consumes',     on: true },
  reads_column:  { color: '#b48ce3', label: 'reads column', on: true },
  writes_column: { color: '#e0637d', label: 'writes column',on: true },
  has_column:    { color: '#4a5164', label: 'has column',   on: false }
};
const NODE_KINDS = {
  function: { on: true }, method: { on: true }, class: { on: true },
  dataset:  { on: true }, column: { on: true }, variable: { on: false },
  // A file's import-time body. Off by default — it is structural, and one per
  // file would clutter the web view — but toggleable, because "who actually
  // calls this?" sometimes answers "the top of that script, on import".
  module:   { on: false }
};
const SHOW = { frames: true };   // in-memory frames vs. files on disk

let SOLO = null;    // {group, key} — one filter isolated
let PREV = null;    // the flag snapshot to restore when un-soloing

const snapshot = () => ({
  edges: Object.fromEntries(Object.entries(EDGE_KINDS).map(([k, v]) => [k, v.on])),
  nodes: Object.fromEntries(Object.entries(NODE_KINDS).map(([k, v]) => [k, v.on])),
  show: { ...SHOW }
});
const restore = s => {
  for (const k in s.edges) EDGE_KINDS[k].on = s.edges[k];
  for (const k in s.nodes) NODE_KINDS[k].on = s.nodes[k];
  Object.assign(SHOW, s.show);
};

// Click a row to SOLO it: that one on, every other one in its group off.
// Click the same row again to put everything back the way you had it.
function soloFilter(group, key) {
  if (SOLO && SOLO.group === group && SOLO.key === key) {
    restore(PREV);
    SOLO = null;
  } else {
    if (!SOLO) PREV = snapshot();
    const set = group === 'edge' ? EDGE_KINDS : NODE_KINDS;
    for (const k in set) set[k].on = (k === key);
    if (group === 'edge') {
      // a soloed edge kind is useless without the nodes it connects
      for (const k in NODE_KINDS) NODE_KINDS[k].on = true;
      SHOW.frames = true;
    }
    SOLO = { group, key };
  }
  buildFilters();
  drawGraph();
}

// The graph measures its own labels, and the measurement must match what the
// browser actually paints. This was hardcoded as 5.6 in six places, so the layout,
// the de-overlap pass and the click target could each disagree with the CSS and
// with each other — which is what put the hit areas out of line with the text.
//
// Keep NODE_FONT_PX in step with `.nlabel { font-size }` in the template.
const NODE_FONT_PX = 12;
const CHAR_W = NODE_FONT_PX * 0.60;   // ui-monospace advance width ~= 0.6em
const DOT_W = 18;                     // the marker plus its gap before the text

/** Width of a node as DRAWN: marker + label. The one measurement everything uses. */
const nodeW = n => DOT_W + n.name.length * CHAR_W;

let ZOOM = 1;
let FIT_PENDING = true;   // fit-to-window on first entry to the Web view
let FIT_TRIES = 0;        // bounded — see drawGraph
let FULL = null;       // every node + edge, laid out once
let POS = null;        // id -> {x,y}  — STABLE across filter changes
let SEL = null;        // sticky selection (click)
let DEPTH = 1;         // how many hops out to highlight
let LAST = null;       // last drawn {nodes, edges} — so highlight can re-run without relayout

const groupOf = s =>
  s.kind === 'column' ? '· columns' :
  s.kind === 'dataset' ? '· data' : s.span.file;

// EVERY node, ignoring filters. Layout is computed from this and ONLY this.
function fullGraph() {
  const nodes = INDEX.symbols.map(s => ({ ...s, file: groupOf(s) }));
  const ids = new Set(nodes.map(n => n.id));
  const edges = INDEX.edges.filter(e => ids.has(e.from_symbol) && ids.has(e.to_symbol));
  return { nodes, edges };
}

// What the filters currently allow — used to SHOW/HIDE, never to re-layout.
function visibleSets() {
  const nodeIds = new Set(INDEX.symbols
    .filter(s => NODE_KINDS[s.kind]?.on)
    .filter(s => !(s.kind === 'dataset' && s.doc === 'frame' && !SHOW.frames))
    .map(s => s.id));
  const edgeKey = e => `${e.from_symbol}|${e.to_symbol}|${e.kind}`;
  const edges = new Set(INDEX.edges
    .filter(e => EDGE_KINDS[e.kind]?.on)
    .filter(e => nodeIds.has(e.from_symbol) && nodeIds.has(e.to_symbol))
    .map(edgeKey));
  return { nodeIds, edges, edgeKey };
}

// graphData() is what selection traverses: the VISIBLE subgraph.
function graphData() {
  const { nodeIds, edges: vis, edgeKey } = visibleSets();
  const nodes = FULL.nodes.filter(n => nodeIds.has(n.id));
  const edges = FULL.edges.filter(e => vis.has(edgeKey(e)));
  return { nodes, edges };
}

// --- force layout, clustered by file ---------------------------------------
function layout(nodes, edges, W, H) {
  const files = [...new Set(nodes.map(n => n.file))].sort();

  // Anchors used to sit on a UNIFORM grid, so a 64-node cluster and a 1-node file
  // got identical cells. The big one physically could not fit, cluster gravity
  // pinned it there, and no amount of relaxation could resolve the overlaps —
  // adding passes was treating the symptom. Size each cell to its contents.
  const area = {};
  for (const f of files) {
    const ns = nodes.filter(n => n.file === f);
    const labelArea = ns.reduce((s, n) => s + nodeW(n), 0) * (NODE_FONT_PX + 4);
    area[f] = labelArea * 3.2 + 6000;      // labels + breathing room + the title
  }

  // Shelf-pack the cells left to right, wrapping into rows. Crude, but it gives
  // every cluster room proportional to what it actually has to draw.
  const cellW = f => Math.sqrt(area[f] * 1.5);
  const cellH = f => area[f] / cellW(f);
  const rowW = Math.max(W, Math.sqrt(files.reduce((s, f) => s + area[f], 0) * 1.6));

  const anchors = {};
  let x = 0, y = 0, rowH = 0;
  for (const f of files) {
    const w = cellW(f), h = cellH(f);
    if (x + w > rowW && x > 0) { x = 0; y += rowH + 40; rowH = 0; }
    anchors[f] = { x: x + w / 2, y: y + h / 2 };
    x += w + 40;
    rowH = Math.max(rowH, h);
  }
  nodes.forEach((n, i) => {
    const a = anchors[n.file];
    n.x = a.x + (Math.cos(i * 2.4) * 40);
    n.y = a.y + (Math.sin(i * 2.4) * 40);
    n.vx = n.vy = 0;
    n.deg = 0;
  });
  const byId = new Map(nodes.map(n => [n.id, n]));
  edges.forEach(e => {
    const a = byId.get(e.from_symbol), b = byId.get(e.to_symbol);
    if (a && b) { a.deg++; b.deg++; }
  });

  for (let it = 0; it < 320; it++) {
    const k = 1 - it / 320;
    // repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const same = a.file === b.file;
        const rep = (same ? 900 : 2600) / d2;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * rep; a.vy += dy * rep;
        b.vx -= dx * rep; b.vy -= dy * rep;
      }
    }
    // springs
    for (const e of edges) {
      const a = byId.get(e.from_symbol), b = byId.get(e.to_symbol);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const target = a.file === b.file ? 70 : 190;
      const f = (d - target) * 0.012;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    // cluster gravity — this is what keeps files legible as groups
    for (const n of nodes) {
      const a = anchors[n.file];
      n.vx += (a.x - n.x) * 0.035;
      n.vy += (a.y - n.y) * 0.035;
      n.x += Math.max(-18, Math.min(18, n.vx)) * k;
      n.y += Math.max(-18, Math.min(18, n.vy)) * k;
      n.vx *= 0.82; n.vy *= 0.82;
      n.x = Math.max(30, Math.min(W - 30, n.x));
      n.y = Math.max(30, Math.min(H - 30, n.y));
    }
  }
  // --- cluster de-overlap ----------------------------------------------------
  // Nodes were relaxed against each other, but whole FILE BOXES could still sit on
  // top of one another. Push entire clusters apart as rigid bodies, then let the
  // node-level pass fine-tune inside them.
  const clusterBox = f => {
    const pts = nodes.filter(n => n.file === f);
    if (!pts.length) return null;
    const w = nodeW;
    return {
      f,
      x0: Math.min(...pts.map(p => p.x)) - 26,
      x1: Math.max(...pts.map(p => p.x + w(p))) + 14,
      y0: Math.min(...pts.map(p => p.y)) - 30,
      y1: Math.max(...pts.map(p => p.y)) + 22
    };
  };

  for (let pass = 0; pass < 90; pass++) {
    const boxes = files.map(clusterBox).filter(Boolean);
    let moved = 0;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const A = boxes[i], B = boxes[j];
        const ox = Math.min(A.x1, B.x1) - Math.max(A.x0, B.x0);
        const oy = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0);
        if (ox <= 0 || oy <= 0) continue;
        moved++;
        // Move the ANCHOR with the cluster. Without this the next pass pulls every
        // node back toward where the cluster used to be, so the two relaxation
        // passes fight and neither converges — which is why collisions survived
        // both more passes and more canvas.
        const shift = (dx, dy) => {
          nodes.forEach(n => { if (n.file === A.f) { n.x += dx; n.y += dy; } });
          nodes.forEach(n => { if (n.file === B.f) { n.x -= dx; n.y -= dy; } });
          if (anchors[A.f]) { anchors[A.f].x += dx; anchors[A.f].y += dy; }
          if (anchors[B.f]) { anchors[B.f].x -= dx; anchors[B.f].y -= dy; }
        };
        if (ox < oy) shift(((ox / 2) + 6) * (A.x0 < B.x0 ? -1 : 1), 0);
        else         shift(0, ((oy / 2) + 6) * (A.y0 < B.y0 ? -1 : 1));
      }
    }
    if (!moved) break;
  }

  // --- de-overlap pass -------------------------------------------------------
  // The force sim treats a node as a POINT, but on screen it is a point plus a
  // label — a wide box. So the sim can converge happily while the labels sit on
  // top of each other. Relax the LABEL BOXES apart, along the shorter axis of
  // overlap, with a bias toward horizontal separation (labels are wide, not tall).
  const LH = NODE_FONT_PX + 4;                    // label box height, tracks the font
  const box = n => {
    const w = nodeW(n);
    return { x0: n.x - 9, x1: n.x + w, y0: n.y - LH / 2, y1: n.y + LH / 2 };
  };

  for (let pass = 0; pass < 500; pass++) {
    let moved = 0;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const A = box(a), B = box(b);
        const ox = Math.min(A.x1, B.x1) - Math.max(A.x0, B.x0);
        const oy = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0);
        if (ox <= 0 || oy <= 0) continue;         // no overlap
        moved++;
        if (oy < ox * 0.45) {                     // separate vertically
          const push = (oy / 2 + 0.5) * (a.y < b.y ? -1 : 1);
          a.y += push; b.y -= push;
        } else {                                  // separate horizontally
          const push = (ox / 2 + 0.5) * (a.x < b.x ? -1 : 1);
          a.x += push; b.x -= push;
        }
      }
    }
    // ANNEAL the cluster gravity to zero.
    //
    // Gravity and separation are opposed: gravity pulls nodes toward their
    // cluster centre, separation pushes overlapping labels apart. Run both at
    // constant strength and they fight forever — every pass, gravity undid the
    // separation, so collisions survived more passes, more canvas, bigger cells
    // and anchor tracking alike. Measured: with gravity off the same relaxation
    // converges in 72 passes to ZERO overlaps.
    //
    // So: gravity does its job early (form the clusters), then fades out and lets
    // separation finish unopposed.
    const grav = 0.004 * Math.max(0, 1 - pass / 120);
    for (const n of nodes) {
      if (grav > 0) {
        const anc = anchors[n.file];
        n.x += (anc.x - n.x) * grav;
        n.y += (anc.y - n.y) * grav;
      }
      n.x = Math.max(30, n.x);
      n.y = Math.max(34, n.y);
    }
    if (!moved) break;
  }

  // The anchor is a means, not a truth: re-seat each one on its cluster's real
  // centroid so any later pass (and a restored layout) pulls toward where the
  // cluster IS rather than where the packer first guessed.
  for (const f of files) {
    const pts = nodes.filter(n => n.file === f);
    if (!pts.length) continue;
    anchors[f] = { x: pts.reduce((s, p) => s + p.x, 0) / pts.length,
                   y: pts.reduce((s, p) => s + p.y, 0) / pts.length };
  }

  return { anchors, files };
}

// BFS out from a node over the VISIBLE edges. Undirected, but we remember which
// side each hop came from so upstream/downstream can be styled differently.
function neighbourhood(id, depth) {
  const up = new Set(), down = new Set(), all = new Set([id]);
  let frontier = [id];
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = [];
    for (const cur of frontier) {
      for (const e of LAST.edges) {
        if (e.from_symbol === cur && !all.has(e.to_symbol)) {
          all.add(e.to_symbol); down.add(e.to_symbol); next.push(e.to_symbol);
        } else if (e.to_symbol === cur && !all.has(e.from_symbol)) {
          all.add(e.from_symbol); up.add(e.from_symbol); next.push(e.from_symbol);
        }
      }
    }
    frontier = next;
  }
  return { all, up, down };
}

function applyHighlight() {
  const svg = document.getElementById('web');
  if (!svg) return;
  const badge = document.getElementById('selinfo');

  if (!SEL) {
    svg.querySelectorAll('.edge').forEach(l => {
      l.style.strokeOpacity = ''; l.style.strokeWidth = '';
    });
    svg.querySelectorAll('.node').forEach(g => {
      g.style.opacity = 1;
      g.classList.remove('sel', 'up', 'down', 'faded');
    });
    if (badge) badge.innerHTML = '';
    return;
  }

  const { all, up, down } = neighbourhood(SEL, DEPTH);

  svg.querySelectorAll('.edge').forEach(l => {
    const hot = all.has(l.dataset.a) && all.has(l.dataset.b);
    l.style.strokeOpacity = hot ? 1 : .04;
    l.style.strokeWidth = hot ? 2.2 : 1;
  });
  // NOTE: this used to re-append the highlighted nodes so they drew on top.
  // Don't. appendChild on an element already in the DOM detaches and reinserts it,
  // which restarts the browser's click-count for dblclick detection — so the
  // double-click to open a card became unreliable, and the graph visibly twitched.
  //
  // Promotion turned out to be unnecessary anyway: faded nodes drop to opacity
  // .08 and lose their labels entirely, so there is nothing left to be buried
  // under. Nothing in the DOM moves on selection now.
  svg.querySelectorAll('.node').forEach(g => {
    const id = g.dataset.id;
    const hot = all.has(id);
    g.classList.toggle('sel', id === SEL);
    g.classList.toggle('up', up.has(id));
    g.classList.toggle('down', down.has(id));
    g.classList.toggle('faded', !hot);      // CSS hides the label of a faded node
    g.style.opacity = hot ? 1 : .08;
  });

  const s = SYMS.get(SEL);
  const nm = s ? s.name : SEL;
  if (badge) {
    badge.innerHTML =
      `<b>${nm}</b> · ${all.size - 1} connected within ${DEPTH === 99 ? 'all' : DEPTH} hop${DEPTH === 1 ? '' : 's'}` +
      ` · <span class="up-t">${up.size} depend on it</span>` +
      ` · <span class="down-t">${down.size} it depends on</span>` +
      ` <span class="clr">clear</span>`;   // handled by the delegated click listener (CSP)
  }
}

function computeLayout() {
  FULL = fullGraph();
  // Area must scale with LABEL area, not node count — a 20% wider font needs
  // ~20% more room per axis or the relaxation cannot converge and gives up.
  const avgW = FULL.nodes.reduce((s, n) => s + nodeW(n), 0) / (FULL.nodes.length || 1);
  const need = Math.sqrt(FULL.nodes.length * avgW * (NODE_FONT_PX + 4)) * 3.2;
  const W0 = Math.max(stage.clientWidth, 900, need);
  const H0 = Math.max(stage.clientHeight, 640, need * 0.72);
  const { anchors, files } = layout(FULL.nodes, FULL.edges, W0 - 40, H0 - 40);

  // clusters were allowed to spread past the starting canvas — size to fit them
  const w = nodeW;
  const W = Math.max(W0, Math.max(...FULL.nodes.map(n => n.x + w(n))) + 40);
  const H = Math.max(H0, Math.max(...FULL.nodes.map(n => n.y)) + 50);
  POS = { W, H, anchors, files };
}

function enterWeb() {
  computeLayout();                       // fresh automatic layout as the baseline
  const store = loadStored();
  if (!store || !store.pos) { drawGraph(); return; }

  const res = applyStored(store);
  if (store.zoom) FIT_PENDING = false;     // the user set this; don't override it
  const total = FULL.nodes.length;

  // If the graph is now mostly different, a saved arrangement is meaningless —
  // say so plainly and start fresh rather than pretending the old one still fits.
  if (res.stale && (res.added.length + res.removed.length) > total * 0.4) {
    POS = null; ZOOM = 1;
    computeLayout();
    drawGraph();
    notice(`The code changed substantially since this layout was saved
            (${res.added.length} node(s) added, ${res.removed.length} removed).
            <b>A new arrangement was generated.</b>`, 'warn');
    saveLayout();
    return;
  }

  drawGraph();
  if (res.stale) {
    notice(`The code changed since you arranged this view
            (${res.added.length} added, ${res.removed.length} removed).
            <b>Your positions were kept</b>; new nodes were placed automatically.
            <span class="nact">re-arrange from scratch</span>`, 'warn');
  }
}

function drawGraph() {
  // Layout is computed ONCE, from the full node set. Toggling a filter must not
  // move anything — a graph that reshuffles on every click can't be read.
  if (!POS) computeLayout();
  const { W, H, anchors, files } = POS;

  const { nodeIds, edges: visE, edgeKey } = visibleSets();
  const nodes = FULL.nodes;
  const edges = FULL.edges;
  LAST = graphData();                       // selection traverses only what is visible
  const byId = new Map(nodes.map(n => [n.id, n]));

  // Every kind in INDEX.symbols needs an entry: fullGraph() lays out the FULL
  // node set regardless of the visibility filters, so a missing kind here is an
  // undefined lookup and a blank page, not a hidden node.
  const NODE_STYLE = {
    function: { r: 6,  fill: '#6fa8ff' }, method: { r: 5, fill: '#6fa8ff' },
    class:    { r: 7,  fill: '#c678dd' },
    dataset:  { r: 8,  fill: '#57d9a3' }, column: { r: 4, fill: '#b48ce3' },
    variable: { r: 4,  fill: '#7b8497' }, module: { r: 5, fill: '#8a94a6' }
  };

  const eSVG = edges.map(e => {
    const a = byId.get(e.from_symbol), b = byId.get(e.to_symbol);
    const cross = a.file !== b.file;
    const off = visE.has(edgeKey(e)) ? '' : ' dim';
    return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"
      stroke="${(EDGE_KINDS[e.kind] || {}).color || '#555'}" stroke-width="${cross ? 1.6 : 1}"
      stroke-opacity="${cross ? .75 : .35}" ${cross ? '' : 'stroke-dasharray="3 2"'}
      data-a="${e.from_symbol}" data-b="${e.to_symbol}" class="edge${off}" />`;
  }).join('');

  // File boxes come from the FULL node set and are always drawn. Filtering out
  // every symbol in a file should remove its CONNECTIONS, not erase the file.
  const hulls = files.map(f => {
    const pts = nodes.filter(n => n.file === f);
    if (!pts.length) return '';
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    const x0 = Math.min(...xs) - 26, x1 = Math.max(...xs) + 26;
    const y0 = Math.min(...ys) - 30, y1 = Math.max(...ys) + 22;
    const isData = f.startsWith('·');
    return `<g class="hull" data-file="${f}">
              <rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" rx="12"
                fill="${isData ? 'rgba(87,217,163,.045)' : 'rgba(255,255,255,.025)'}"
                stroke="${isData ? 'rgba(87,217,163,.25)' : 'var(--line)'}" stroke-width="1"/>
              <rect class="hull-grip" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"
                rx="12" fill="transparent"/>
              <text x="${x0 + 10}" y="${y0 + 17}" class="hull-label">${f} ⠿</text>
            </g>`;
  }).join('');

  // A node is "live" if the current connection filter still touches it. Anything
  // the filter leaves stranded gets greyed — present, but quiet.
  const live = new Set();
  for (const e of edges) {
    if (!visE.has(edgeKey(e))) continue;
    live.add(e.from_symbol);
    live.add(e.to_symbol);
  }
  const allEdgesOn = Object.values(EDGE_KINDS).every(v => v.on);

  const nSVG = nodes.map(n => {
    const st = NODE_STYLE[n.kind];
    const shape = n.kind === 'dataset'
      ? `<rect x="${n.x - st.r}" y="${n.y - st.r}" width="${st.r * 2}" height="${st.r * 2}" rx="2"
           fill="${st.fill}" />`
      : `<circle cx="${n.x}" cy="${n.y}" r="${st.r + Math.min(3, n.deg / 4)}" fill="${st.fill}"
           ${n.entry ? 'class="entrynode"' : ''} />`;
    // The visible dot is ~6px. Clicking a 6px target is a usability bug, and the
    // label was pointer-events:none, so clicking the NAME did nothing at all.
    // An invisible hit circle covers the dot AND the label sits above it.
    const w = n.name.length * CHAR_W;
    const stranded = !allEdgesOn && !live.has(n.id);
    const outside = IMPACT_ONLY && n.impact < 0;
    const off = (nodeIds.has(n.id) && !stranded && !outside) ? '' : ' dim';
    const imp = n.changed ? ' changed' : (n.impact > 0 ? ` imp${Math.min(n.impact, 4)}` : '');
    return `<g class="node${off}${imp}" data-id="${n.id}" data-kind="${n.kind}">
        <rect class="hit" x="${n.x - st.r - 6}" y="${n.y - 10}"
              width="${st.r * 2 + 14 + w}" height="20" fill="transparent" />
        ${shape}
        <text x="${n.x + st.r + 5}" y="${n.y + 4}" class="nlabel">${n.name}</text>
      </g>`;
  }).join('');

  stage.innerHTML =
    `<div id="selinfo"></div>
     <div id="zoombar">
       <span class="zb" data-zoom="out">−</span>
       <span class="zb" data-zoom="in">+</span>
       <span class="zb wide" data-zoom="fit">fit</span>
       <span class="zb wide" data-zoom="reset">100%</span>
       <span class="zlevel" id="zlevel"></span>
     </div>
     <div id="canvas">
       <svg id="web" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
         <rect x="0" y="0" width="${W}" height="${H}" fill="transparent" id="bg"/>
         <g id="pan">${hulls}${eSVG}${nSVG}</g>
       </svg>
     </div>`;

  const canvas = document.getElementById('canvas');
  const svgEl = document.getElementById('web');

  const applyZoom = () => {
    svgEl.setAttribute('width', W * ZOOM);
    svgEl.setAttribute('height', H * ZOOM);
    const z = document.getElementById('zlevel');
    if (z) z.textContent = Math.round(ZOOM * 100) + '%';
  };
  const fitZoom = () =>
    Math.min(canvas.clientWidth / W, canvas.clientHeight / H) * 0.96;

  // Open fitted, not at 100%. A whole-project graph at 1:1 drops you into the
  // middle of it with no idea what you are looking at. FIT_PENDING is set once,
  // on entry, and only when the user has no saved zoom of their own.
  if (FIT_PENDING) {
    const z = fitZoom();
    if (isFinite(z) && z > 0) {
      ZOOM = Math.min(1, z);        // fit, but never zoom IN past 100%
      FIT_PENDING = false;
    } else if (FIT_TRIES < 3) {
      // The canvas was created microseconds ago and may not be laid out yet, so
      // clientWidth reads 0 and the fit silently does nothing — which is how it
      // shipped opening at 100%. Retry after the browser lays out.
      //
      // BOUNDED, deliberately. An unbounded rAF retry is an infinite loop the
      // moment the canvas never gets a size (a hidden webview, a collapsed
      // panel) — re-running the whole draw at 60fps forever. Three attempts,
      // then give up and render at 100%: a bad zoom beats a spinning CPU.
      FIT_TRIES++;
      requestAnimationFrame(() => drawGraph());
    } else {
      FIT_PENDING = false;
    }
  }
  applyZoom();

  document.querySelectorAll('[data-zoom]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.zoom;
    if (k === 'in')  ZOOM = Math.min(3, ZOOM * 1.25);
    if (k === 'out') ZOOM = Math.max(0.15, ZOOM / 1.25);
    if (k === 'reset') ZOOM = 1;
    if (k === 'fit') ZOOM = fitZoom();
    applyZoom();
    saveLayout();
  }));

  // ctrl/cmd + wheel zooms around the cursor; plain wheel scrolls
  canvas.addEventListener('wheel', ev => {
    if (!ev.ctrlKey && !ev.metaKey) return;
    ev.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left + canvas.scrollLeft;
    const my = ev.clientY - rect.top + canvas.scrollTop;
    const before = ZOOM;
    ZOOM = Math.max(0.15, Math.min(3, ZOOM * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
    applyZoom();
    const k = ZOOM / before;
    canvas.scrollLeft = mx * k - (ev.clientX - rect.left);
    canvas.scrollTop  = my * k - (ev.clientY - rect.top);
  }, { passive: false });

  // drag empty space to pan
  let panning = false, sx = 0, sy = 0, sl = 0, st_ = 0;
  canvas.addEventListener('mousedown', ev => {
    if (ev.target.closest('.node')) return;
    panning = true; sx = ev.clientX; sy = ev.clientY;
    sl = canvas.scrollLeft; st_ = canvas.scrollTop;
    canvas.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', ev => {
    if (!panning) return;
    canvas.scrollLeft = sl - (ev.clientX - sx);
    canvas.scrollTop = st_ - (ev.clientY - sy);
  });
  window.addEventListener('mouseup', () => { panning = false; canvas.style.cursor = ''; });

  // --- drag a FILE BOX to move its whole cluster ---------------------------
  // The auto-layout is a starting point, not an opinion. Let people arrange the
  // graph the way their mental model already looks.
  svgEl.querySelectorAll('.hull').forEach(g => {
    const grip = g.querySelector('.hull-grip');
    grip.addEventListener('mousedown', ev => {
      ev.preventDefault();
      ev.stopPropagation();
      const file = g.dataset.file;
      const members = FULL.nodes.filter(n => n.file === file);
      const start = { x: ev.clientX, y: ev.clientY };
      const orig = members.map(n => ({ n, x: n.x, y: n.y }));
      const move = e2 => {
        const dx = (e2.clientX - start.x) / ZOOM;
        const dy = (e2.clientY - start.y) / ZOOM;
        orig.forEach(o => { o.n.x = Math.max(30, o.x + dx); o.n.y = Math.max(34, o.y + dy); });
        drawGraph();
      };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        saveLayout();
        notice('Layout saved.', 'ok');
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    });
  });

  // click empty space to clear the selection
  document.getElementById('bg').addEventListener('click', () => {
    SEL = null; applyHighlight();
  });

  // hover: isolate a node's neighbourhood
  const svg = document.getElementById('web');
  svg.querySelectorAll('.node').forEach(g => {
    // hover is only a PREVIEW — it must not fight a sticky selection
    g.addEventListener('mouseenter', () => {
      if (SEL) return;
      const id = g.dataset.id;
      const near = new Set([id]);
      svg.querySelectorAll('.edge').forEach(l => {
        const hit = l.dataset.a === id || l.dataset.b === id;
        l.style.strokeOpacity = hit ? 1 : .05;
        l.style.strokeWidth = hit ? 2.4 : 1;
        if (hit) { near.add(l.dataset.a); near.add(l.dataset.b); }
      });
      svg.querySelectorAll('.node').forEach(o => {
        const hot = near.has(o.dataset.id);
        o.style.opacity = hot ? 1 : .15;
        o.classList.toggle('faded', !hot);
      });
    });
    g.addEventListener('mouseleave', () => {
      if (SEL) return;
      svg.querySelectorAll('.edge').forEach(l => {
        l.style.strokeOpacity = ''; l.style.strokeWidth = '';
      });
      svg.querySelectorAll('.node').forEach(o => {
        o.style.opacity = 1; o.classList.remove('faded');
      });
    });

    // single click = select + highlight the neighbourhood (sticky)
    g.addEventListener('click', ev => {
      ev.stopPropagation();
      SEL = (SEL === g.dataset.id) ? null : g.dataset.id;
      applyHighlight();
    });

    // double click = open it
    g.addEventListener('dblclick', ev => {
      ev.stopPropagation();
      const s = SYMS.get(g.dataset.id);
      if (!s) return;
      if (s.kind === 'column' || s.kind === 'dataset') showDataPanel(s);
      else openCard(s.id, null, ev.clientX, ev.clientY, g);
    });
  });

  applyHighlight();   // survive a re-filter / relayout
}

// clicking a dataset/column shows who touches it — the coupling, in words
function showDataPanel(sym) {
  const touching = INDEX.edges.filter(e =>
    e.to_symbol === sym.id || e.from_symbol === sym.id);
  const rows = touching.map(e => {
    const other = e.to_symbol === sym.id ? e.from_symbol : e.to_symbol;
    const o = SYMS.get(other);
    if (!o) return '';
    const where = o.span ? o.span.file : '';
    return `<tr><td style="color:${EDGE_KINDS[e.kind]?.color || '#888'}">${e.kind}</td>
            <td class="mono">${o.name}</td><td class="mono dim">${where}</td></tr>`;
  }).join('');
  const c = document.createElement('div');
  c.className = 'card';
  c.style.zIndex = ++z;
  c.style.left = '340px'; c.style.top = '90px'; c.style.width = '520px';
  c.innerHTML = `
    <div class="card-head">
      <span class="t">${sym.name}</span>
      <span class="p">${sym.kind}${sym.doc ? ' · ' + sym.doc : ''}</span>
      <span class="x" data-close>✕</span>
    </div>
    <div class="card-body" style="padding:10px 12px">
      <table class="dtab">${rows}</table>
    </div>`;
  document.body.appendChild(c);
  wire(c);
}

// --- controls ---------------------------------------------------------------
function buildFilters() {
  const box = document.getElementById('filters');
  const soloed = (g, k) => SOLO && SOLO.group === g && SOLO.key === k;
  box.innerHTML =
    `<div class="fhead">Connections
       <span class="allbtn" data-all="1">${SOLO ? 'reset' : 'all'}</span></div>` +
    Object.entries(EDGE_KINDS).map(([k, v]) => `
      <div class="fitem ${soloed('edge', k) ? 'soloed' : ''}" data-solo-edge="${k}"
           title="click to isolate — click again to restore">
        <input type="checkbox" data-edge="${k}" ${v.on ? 'checked' : ''}>
        <i class="key" style="background:${v.color}"></i><span class="lbl">${v.label}</span>
        <span class="cnt">${INDEX.edges.filter(e => e.kind === k).length}</span>
      </div>`).join('') +
    '<div class="fhead">Nodes</div>' +
    Object.entries(NODE_KINDS).map(([k, v]) => `
      <div class="fitem ${soloed('node', k) ? 'soloed' : ''}" data-solo-node="${k}"
           title="click to isolate — click again to restore">
        <input type="checkbox" data-node="${k}" ${v.on ? 'checked' : ''}>
        <span class="lbl">${k}</span>
        <span class="cnt">${INDEX.symbols.filter(s => s.kind === k).length}</span>
      </div>`).join('') +
    `<div class="fhead">Highlight depth</div>
     <div class="depth">
       ${[['1','direct'],['2','2 hops'],['99','all']].map(([d,l]) =>
         `<span class="dbtn ${String(DEPTH)===d?'on':''}" data-depth="${d}">${l}</span>`).join('')}
     </div>
     <div class="fhead">Data</div>
     <label class="fitem"><input type="checkbox" data-show="frames" checked>
       in-memory frames <span class="cnt">${INDEX.symbols.filter(s => s.kind === 'dataset' && s.doc === 'frame').length}</span></label>`;

  // clicking the ROW solos; clicking the CHECKBOX still toggles normally
  box.querySelectorAll('[data-solo-edge]').forEach(el => el.addEventListener('click', ev => {
    if (ev.target.tagName === 'INPUT') return;
    soloFilter('edge', el.dataset.soloEdge);
  }));
  box.querySelectorAll('[data-solo-node]').forEach(el => el.addEventListener('click', ev => {
    if (ev.target.tagName === 'INPUT') return;
    soloFilter('node', el.dataset.soloNode);
  }));
  box.querySelector('[data-all]')?.addEventListener('click', () => {
    if (SOLO) { restore(PREV); SOLO = null; }
    else {
      for (const k in EDGE_KINDS) EDGE_KINDS[k].on = true;
      for (const k in NODE_KINDS) NODE_KINDS[k].on = true;
      SHOW.frames = true;
    }
    buildFilters();
    drawGraph();
  });

  box.querySelector('#l-export')?.addEventListener('click', exportLayout);
  box.querySelector('#l-reset')?.addEventListener('click', resetLayout);
  box.querySelector('#l-import')?.addEventListener('click', () => box.querySelector('#l-file').click());
  box.querySelector('#l-file')?.addEventListener('change', e => {
    if (e.target.files[0]) importLayout(e.target.files[0]);
  });

  box.querySelectorAll('.dbtn').forEach(b => b.addEventListener('click', () => {
    DEPTH = +b.dataset.depth;
    box.querySelectorAll('.dbtn').forEach(o => o.classList.toggle('on', o === b));
    applyHighlight();
  }));

  box.querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
    if (cb.dataset.edge) EDGE_KINDS[cb.dataset.edge].on = cb.checked;
    if (cb.dataset.node) NODE_KINDS[cb.dataset.node].on = cb.checked;
    if (cb.dataset.show === 'impactonly') IMPACT_ONLY = cb.checked;
    else if (cb.dataset.show) SHOW[cb.dataset.show] = cb.checked;
    SOLO = null;
    drawGraph();          // NOT computeLayout() — positions must not move
  }));
}

function openImpactPanel() {
  const changed = INDEX.symbols.filter(s => s.changed);
  const hops = {};
  for (const s of INDEX.symbols) {
    if (s.impact > 0) (hops[s.impact] ||= []).push(s);
  }
  const total = Object.values(hops).reduce((n, a) => n + a.length, 0);

  const rows = changed.map(s => `
      <div class="refrow" data-open="${s.id}">
        <div class="refloc"><span class="mono">${s.span.file}:${s.body ? s.body.start_line : s.span.start_line}</span>
          <span class="refin">changed</span></div>
        <div class="refsrc mono"><b>${s.name}</b> ${s.doc ? '— ' + s.doc : ''}</div>
      </div>`).join('');

  const radius = Object.keys(hops).sort((a, b) => a - b).map(h => `
      <div class="hoprow">
        <span class="hopn i${Math.min(h,4)}">${h} hop${h > 1 ? 's' : ''}</span>
        <span class="hoplist">${hops[h].map(s =>
          `<span class="chip ${s.kind}" data-open="${s.id}">${s.name}</span>`).join('')}</span>
      </div>`).join('');

  const id = 'impact::panel';
  const ex = OPEN.get(id);
  if (ex && document.body.contains(ex)) { surface(ex); return; }

  const c = document.createElement('div');
  c.className = 'card refs';
  c.dataset.sym = id;
  c.style.left = '340px'; c.style.top = '90px'; c.style.width = '640px';
  c.innerHTML = `
    <div class="card-head">
      <span class="t">blast radius</span>
      <span class="p">${changed.length} changed vs <code>${INDEX.diff_ref || 'HEAD'}</code>
        · ${total} symbol(s) downstream</span>
      <span class="x" data-close>✕</span>
    </div>
    <div class="card-body">
      <div class="pnote">A change propagates along <b>calls and data</b>. An edit can reach a
        function that never calls it — through a column or a table. This is an
        <b>upper bound</b>: it shows what a change <i>could</i> touch, not what it will.</div>
      ${rows}
      <div class="pnote2">downstream</div>
      ${radius || '<div class="refrow"><div class="refloc">nothing downstream</div></div>'}
    </div>`;
  document.body.appendChild(c);
  OPEN.set(id, c);
  wire(c);
  surface(c);
  c.querySelectorAll('[data-open]').forEach(el => el.addEventListener('click', ev => {
    const s = SYMS.get(el.dataset.open);
    if (!s) return;
    if (s.kind === 'dataset' || s.kind === 'column') showDataPanel(s);
    else openCard(s.id, 'blast radius', ev.clientX, ev.clientY, el);
  }));
}

function setView(v, symId) {
  document.body.dataset.view = v;
  document.querySelectorAll('.vtab').forEach(t =>
    t.classList.toggle('on', t.dataset.view === v));
  document.querySelectorAll('.card:not(.root)').forEach(c => {
    if (c.dataset.sym) OPEN.delete(c.dataset.sym);
    clearOrigin(c);
    c.remove();
  });
  drawTethers();
  if (v === 'web') { SEL = null; POS = null; FIT_PENDING = true; FIT_TRIES = 0; buildFilters(); enterWeb(); }
  else if (v === 'flow') { drawFlow(); markFlowFile(); }
  else {
    stage.innerHTML = '';
    if (symId) return;                       // openRoot is already doing it
    const a = document.querySelector('.sym.active');
    a ? openRoot(a.dataset.sym) : (stage.innerHTML = WELCOME);
  }
}


// ============================================================================
// LAYOUT PERSISTENCE
//
// The fingerprint is a hash of the SYMBOL IDS, not the source text — so editing a
// function body keeps your layout, while adding or removing a symbol is flagged.
// That is the honest line: the layout describes the graph's shape, and only a
// change of shape can invalidate it.
// ============================================================================
const LKEY = 'codevis:layout:' + INDEX.root;

function saveLayout() {
  if (!FULL || !POS) return;
  const data = {
    fingerprint: INDEX.fingerprint,
    saved: new Date().toISOString(),
    zoom: ZOOM,
    W: POS.W, H: POS.H,
    pos: Object.fromEntries(FULL.nodes.map(n => [n.id, [Math.round(n.x), Math.round(n.y)]]))
  };
  try { localStorage.setItem(LKEY, JSON.stringify(data)); }
  catch (e) { /* file:// with storage blocked — the export button still works */ }
  return data;
}

function loadStored() {
  try { return JSON.parse(localStorage.getItem(LKEY) || 'null'); }
  catch (e) { return null; }
}

// Apply a saved layout to the current graph. Returns what had to change.
function applyStored(store) {
  const known = [], added = [];
  for (const n of FULL.nodes) {
    const p = store.pos[n.id];
    if (p) { n.x = p[0]; n.y = p[1]; known.push(n); }
    else added.push(n);
  }
  const removed = Object.keys(store.pos).filter(id => !FULL.nodes.some(n => n.id === id));

  if (added.length) {
    // Place new nodes near their file cluster, then relax ONLY THEM. Nodes the
    // user positioned are pinned — a new symbol must not rearrange your work.
    const centroid = f => {
      const pts = known.filter(n => n.file === f);
      if (!pts.length) return { x: store.W / 2, y: store.H / 2 };
      return { x: pts.reduce((s, p) => s + p.x, 0) / pts.length,
               y: pts.reduce((s, p) => s + p.y, 0) / pts.length };
    };
    added.forEach((n, i) => {
      const c = centroid(n.file);
      n.x = c.x + Math.cos(i * 2.4) * 55;
      n.y = c.y + Math.sin(i * 2.4) * 55;
    });
    const LH = NODE_FONT_PX + 4;
    const box = n => ({ x0: n.x - 9, x1: n.x + nodeW(n),
                        y0: n.y - LH / 2, y1: n.y + LH / 2 });
    for (let pass = 0; pass < 120; pass++) {
      let moved = 0;
      for (const a of added) {
        for (const b of FULL.nodes) {
          if (a.id === b.id) continue;
          const A = box(a), B = box(b);
          const ox = Math.min(A.x1, B.x1) - Math.max(A.x0, B.x0);
          const oy = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0);
          if (ox <= 0 || oy <= 0) continue;
          moved++;
          if (oy < ox * 0.45) a.y += (oy + 1) * (a.y < b.y ? -1 : 1);
          else                a.x += (ox + 1) * (a.x < b.x ? -1 : 1);
        }
      }
      if (!moved) break;
    }
  }

  const w = nodeW;
  POS.W = Math.max(store.W, Math.max(...FULL.nodes.map(n => n.x + w(n))) + 40);
  POS.H = Math.max(store.H, Math.max(...FULL.nodes.map(n => n.y)) + 50);
  ZOOM = store.zoom || 1;
  return { added, removed, stale: store.fingerprint !== INDEX.fingerprint };
}

function notice(html, kind) {
  const el = document.getElementById('notice');
  if (!el) return;
  el.className = 'notice ' + (kind || '');
  el.innerHTML = html + ' <span class="nclose">✕</span>';
  el.style.display = 'block';
  el.querySelector('.nclose').onclick = () => { el.style.display = 'none'; };
}

// ---- export / import, for when localStorage is unavailable (file:// lockdown)
function exportLayout() {
  const data = saveLayout() || {};
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${INDEX.root}.layout.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function importLayout(file) {
  const fr = new FileReader();
  fr.onload = () => {
    try {
      const store = JSON.parse(fr.result);
      const res = applyStored(store);
      drawGraph();
      saveLayout();
      notice(res.stale
        ? `Loaded a layout saved against <b>different code</b>. Kept your positions; ${res.added.length} new node(s) placed automatically.`
        : `Layout restored.`, res.stale ? 'warn' : '');
    } catch (e) { notice('That file was not a codevis layout.', 'warn'); }
  };
  fr.readAsText(file);
}

function resetLayout() {
  try { localStorage.removeItem(LKEY); } catch (e) {}
  POS = null; ZOOM = 1;
  computeLayout();
  drawGraph();
  notice('Layout reset to the automatic arrangement.');
}


// ============================================================================
// TETHERS — a soft line from the place a link was clicked to the card it opened.
//
// The whole premise of the card view is "follow a reference without losing your
// place". The tether is that promise made visible: the origin stays on screen and
// stays connected. Curves, not straight lines — a straight line reads as a wire,
// a curve reads as a thread back to where you were.
// ============================================================================
const TETHER = new Map();      // card element -> origin element

// The token that opened a card gets marked, so the thread's anchor is obvious
// even when the curve passes behind something.
function setOrigin(card, el) {
  const prev = TETHER.get(card);
  if (prev && prev !== el) prev.classList.remove('is-origin');
  TETHER.set(card, el);
  el.classList.add('is-origin');
}
function clearOrigin(card) {
  const el = TETHER.get(card);
  if (el) el.classList.remove('is-origin');
  TETHER.delete(card);
}

function tetherLayer() {
  let el = document.getElementById('tethers');
  if (!el) {
    el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    el.id = 'tethers';
    el.innerHTML = `
      <defs>
        <linearGradient id="tg" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0"   stop-color="var(--accent)" stop-opacity="0.15"/>
          <stop offset="0.5" stop-color="var(--accent)" stop-opacity="0.55"/>
          <stop offset="1"   stop-color="var(--accent)" stop-opacity="0.85"/>
        </linearGradient>
        <filter id="tglow" x="-20%" y="-40%" width="140%" height="180%">
          <feGaussianBlur stdDeviation="2.5" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g id="tpaths"></g>`;
    document.body.appendChild(el);
  }
  return el;
}

// where on the card should the thread land — the side facing the origin
function cardAnchor(rect, from) {
  const cx = rect.left + rect.width / 2;
  const leftward = from.x < cx;
  return {
    x: leftward ? rect.left : rect.right,
    y: Math.min(Math.max(from.y, rect.top + 18), rect.top + Math.min(rect.height, 120)),
    side: leftward ? -1 : 1
  };
}

// A cubic with horizontal control arms, plus a gentle sag — reads as a hanging
// thread rather than a routed wire. The sag scales with distance so short hops
// stay taut and long ones drape.
function threadPath(p0, p1) {
  const dx = p1.x - p0.x, dy = p1.y - p0.y;
  const dist = Math.hypot(dx, dy);

  // Control arms reach out horizontally from BOTH ends, so the curve leaves the
  // token sideways and meets the card square-on — no kinks at either end.
  const arm = Math.max(40, Math.min(200, Math.abs(dx) * 0.5 + dist * 0.14));
  const dir = dx >= 0 ? 1 : -1;

  // Sag scales with distance: short hops stay taut, long ones drape like a thread.
  const sag = Math.min(48, dist * 0.11);

  const c1 = { x: p0.x + arm * dir,        y: p0.y + sag };
  const c2 = { x: p1.x - arm * p1.side,    y: p1.y + sag * 0.3 };

  return `M ${p0.x.toFixed(1)} ${p0.y.toFixed(1)} ` +
         `C ${c1.x.toFixed(1)} ${c1.y.toFixed(1)}, ` +
         `${c2.x.toFixed(1)} ${c2.y.toFixed(1)}, ` +
         `${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
}

function drawTethers() {
  const layer = tetherLayer();
  const g = layer.querySelector('#tpaths');
  if (!g) return;
  if (!TETHER.size) { g.innerHTML = ''; return; }

  const parts = [];
  for (const [card, origin] of TETHER) {
    if (!document.body.contains(card) || !document.body.contains(origin)) {
      TETHER.delete(card);
      continue;
    }
    const oR = origin.getBoundingClientRect();
    const cR = card.getBoundingClientRect();
    if (!oR.width && !oR.height) continue;

    // if the origin has been scrolled out of its own container, pin the thread to
    // the container edge and fade it — the connection still exists, just off-screen
    const holder = origin.closest('.card-body') || origin.closest('#canvas') || document.body;
    const hR = holder.getBoundingClientRect();
    const clampedY = Math.min(Math.max(oR.top + oR.height / 2, hR.top + 2), hR.bottom - 2);
    const hidden = Math.abs(clampedY - (oR.top + oR.height / 2)) > 1;

    const p0 = { x: oR.left + oR.width / 2, y: clampedY };
    const p1 = cardAnchor(cR, p0);
    const active = card.classList.contains('pulse') ? 1 : 0;

    parts.push(`
      <path d="${threadPath(p0, p1)}" class="tether ${hidden ? 'muted' : ''} ${active ? 'live' : ''}"/>
      <circle cx="${p0.x}" cy="${p0.y}" r="${hidden ? 2 : 3}" class="tdot ${hidden ? 'muted' : ''}"/>
      <circle cx="${p1.x}" cy="${p1.y}" r="3.5" class="tdot end ${hidden ? 'muted' : ''}"/>`);
  }
  g.innerHTML = parts.join('');
}

let _raf = null;
function tetherLoop() {
  drawTethers();
  _raf = TETHER.size ? requestAnimationFrame(tetherLoop) : null;
}
function startTethers() { if (!_raf) tetherLoop(); }

window.addEventListener('resize', drawTethers);

// ============================================================================
// FEATURE B — "describe", in the card.
//
// (The spec calls this "contextualize"; the button says "describe" because that is
// what a reader is actually asking for. The internal names still track the spec.)
//
// On-demand only, never automatic on card open (spec B.5): tokens cost money and
// an explanation nobody asked for is spam. Outside VS Code the button explains why
// it is not available rather than failing silently.
// ============================================================================
const CTX_CACHE = new Map();     // symbol id -> rendered result (session-local mirror)

function ctxBoxId(id) { return 'ctx-' + id.replace(/[^a-z0-9]/gi, '_'); }

function setCtxState(id, state, msg) {
  const box = document.getElementById(ctxBoxId(id));
  if (!box) return;
  if (state === 'loading') {
    box.className = 'ctxbox loading';
    box.innerHTML = '<span class="spin"></span> asking the model…';
  } else if (state === 'error') {
    box.className = 'ctxbox error';
    box.innerHTML = `<b>Describe failed.</b><br>${esc(msg || '')}`;
  }
}

// minimal markdown — enough for the model's prose, no dependency
function md(s) {
  return esc(s)
    .replace(/^### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^## (.*)$/gm, '<h4>$1</h4>')
    // fence content is safe to inline verbatim: the WHOLE string went through
    // esc() above, so `c` arrives with <, > and & already entity-encoded
    .replace(/```([\s\S]*?)```/g, (_m, c) => `<pre class="ctxpre">${c.trim()}</pre>`)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/^- (.*)$/gm, '• $1')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br>');
}

function renderCtx(id, data, cached) {
  CTX_CACHE.set(id, data);
  const box = document.getElementById(ctxBoxId(id));
  if (!box) return;

  // Follow-up links: only symbols that RESOLVE to a real node become clickable.
  // Anything else stays plain text — never fabricate a link (spec B.4).
  let html = '<p>' + md(data.explanation) + '</p>';
  const links = (data.links || []).filter(l => l.id);
  const plain = (data.links || []).filter(l => !l.id);

  if (links.length) {
    html += '<div class="ctxlinks"><span class="ctxlbl">follow</span>' +
      links.map(l => `<span class="chip" data-follow="${esc(l.id)}">${esc(l.name)}</span>`).join('') +
      '</div>';
  }
  if (plain.length) {
    html += `<div class="ctxlinks"><span class="ctxlbl">mentioned</span>` +
      plain.map(l => `<span class="chip flat" title="not found in the index — not linked">${esc(l.name)}</span>`).join('') +
      '</div>';
  }
  html += `<div class="ctxfoot">${esc(data.model || '')}${cached ? ' · cached' : ''}
             <span class="rerun" data-rerun="${id}">re-run</span></div>`;

  box.className = 'ctxbox done';
  box.innerHTML = html;

  box.querySelectorAll('[data-follow]').forEach(el => el.addEventListener('click', ev => {
    ev.stopPropagation();
    openCard(el.dataset.follow, 'describe', ev.clientX, ev.clientY, el);
  }));
  box.querySelector('[data-rerun]')?.addEventListener('click', () => {
    const card = box.closest('.card');
    setCtxState(id, 'loading');
    window.dispatchEvent(new CustomEvent('codevis:contextualize', {
      detail: { id, callerId: card?.dataset.openedFrom || null, force: true }
    }));
  });
}

function wireContextualize(card) {
  const btn = card.querySelector('[data-ctx]');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const id = card.dataset.sym;
    if (!IN_VSCODE) {
      setCtxState(id, 'error',
        'Describe needs a language model, which this standalone HTML has no way to reach. ' +
        'Open this workspace in VS Code with the codevis extension to use it.');
      return;
    }
    const hit = CTX_CACHE.get(id);
    if (hit) { renderCtx(id, hit, true); return; }
    setCtxState(id, 'loading');
    window.dispatchEvent(new CustomEvent('codevis:contextualize', {
      detail: { id, callerId: card.dataset.openedFrom || null, force: false }
    }));
  });
}


// ============================================================================
// FEATURE A — the per-script flowchart.
//
// Three views, one model (see the Code Graph note): Cards = one function,
// Flow = one script, Web = the whole project. Same index, same cards, same links.
//
// The spec called for React Flow + dagre. That would cost the zero-build,
// single-file artifact, so the layering is implemented here instead — it is the
// classic Sugiyama pipeline and it is ~150 lines:
//   1. break cycles     (recursion and mutual calls are normal in real code)
//   2. rank             (longest-path layering -> the top-down tiers)
//   3. order            (barycenter sweeps -> fewer edge crossings)
//   4. position         (median placement, then collision relaxation)
// ============================================================================

let FLOW_FILE = null;

// Hover-to-reveal is great when you are exploring and intrusive when you are not
// — it hijacks the editor pane on every mouse move. Off is a legitimate default
// for someone reading the chart itself.
let FLOW_JUMP = (() => {
  try { return localStorage.getItem('codevis:flowjump') !== '0'; }
  catch (e) { return true; }
})();
function setFlowJump(on) {
  FLOW_JUMP = on;
  try { localStorage.setItem('codevis:flowjump', on ? '1' : '0'); } catch (e) {}
  if (!on) window.dispatchEvent(new CustomEvent('codevis:nodeHoverEnd'));
}

function flowFiles() {
  const files = new Set();
  for (const s of INDEX.symbols) {
    if (['function', 'method', 'class'].includes(s.kind)) files.add(s.span.file);
  }
  return [...files].sort();
}

function flowGraph(file) {
  const nodes = INDEX.symbols
    .filter(s => s.span.file === file && ['function', 'method', 'class'].includes(s.kind))
    .map(s => ({ ...s }));
  const ids = new Set(nodes.map(n => n.id));

  // Edges INSIDE this script are the flow. Calls that leave the file are shown as
  // stubs so the script's boundary is visible rather than silently cropped.
  const inner = [], outward = [];
  for (const e of INDEX.edges) {
    if (e.kind !== 'calls') continue;
    if (!ids.has(e.from_symbol)) continue;
    if (ids.has(e.to_symbol)) inner.push(e);
    else if (SYMS.get(e.to_symbol)) outward.push(e);
  }
  return { nodes, edges: inner, outward };
}

// --- 1. break cycles ---------------------------------------------------------
function breakCycles(nodes, edges) {
  const out = new Map(nodes.map(n => [n.id, []]));
  for (const e of edges) out.get(e.from_symbol)?.push(e.to_symbol);
  const state = new Map();          // 0 unseen, 1 on stack, 2 done
  const back = new Set();
  const visit = id => {
    state.set(id, 1);
    for (const t of out.get(id) ?? []) {
      const st = state.get(t) ?? 0;
      if (st === 1) back.add(id + '|' + t);      // a back edge: recursion / mutual call
      else if (st === 0) visit(t);
    }
    state.set(id, 2);
  };
  for (const n of nodes) if (!state.get(n.id)) visit(n.id);
  return back;
}

// --- 2. rank (longest path) --------------------------------------------------
function rank(nodes, edges, back) {
  const fwd = edges.filter(e => !back.has(e.from_symbol + '|' + e.to_symbol));
  const preds = new Map(nodes.map(n => [n.id, []]));
  for (const e of fwd) preds.get(e.to_symbol)?.push(e.from_symbol);

  const r = new Map();
  const depth = (id, seen = new Set()) => {
    if (r.has(id)) return r.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const p = preds.get(id) ?? [];
    const v = p.length ? Math.max(...p.map(x => depth(x, seen) + 1)) : 0;
    r.set(id, v);
    return v;
  };
  for (const n of nodes) depth(n.id);
  return r;
}

// --- 3. order within ranks (barycenter) --------------------------------------
function order(nodes, edges, ranks) {
  const layers = [];
  for (const n of nodes) {
    const r = ranks.get(n.id) ?? 0;
    (layers[r] ||= []).push(n);
  }
  const neigh = new Map(nodes.map(n => [n.id, []]));
  for (const e of edges) {
    neigh.get(e.to_symbol)?.push(e.from_symbol);
    neigh.get(e.from_symbol)?.push(e.to_symbol);
  }
  for (let sweep = 0; sweep < 6; sweep++) {
    const pos = new Map();
    layers.forEach(l => l.forEach((n, i) => pos.set(n.id, i)));
    for (const layer of layers) {
      layer.sort((a, b) => {
        const bc = n => {
          const ns = (neigh.get(n.id) ?? []).map(x => pos.get(x)).filter(v => v !== undefined);
          return ns.length ? ns.reduce((s, v) => s + v, 0) / ns.length : pos.get(n.id) ?? 0;
        };
        return bc(a) - bc(b);
      });
    }
  }
  return layers;
}

// --- 4. position -------------------------------------------------------------
const FW = 200, FH = 42, FGAPX = 30, FGAPY = 62;

function flowLayout(nodes, edges) {
  const back = breakCycles(nodes, edges);
  const ranks = rank(nodes, edges, back);
  const layers = order(nodes, edges, ranks);

  const widest = Math.max(1, ...layers.map(l => l.length));
  const W = widest * (FW + FGAPX) + FGAPX;
  layers.forEach((layer, r) => {
    const total = layer.length * (FW + FGAPX) - FGAPX;
    let x = (W - total) / 2;
    for (const n of layer) {
      n.x = x; n.y = 60 + r * (FH + FGAPY);
      n.rank = r;
      x += FW + FGAPX;
    }
  });
  const H = 60 + layers.length * (FH + FGAPY) + 40;
  return { W, H, layers, back };
}

// --- edges: orthogonal-ish curves, the Mermaid visual language ---------------
function flowEdge(a, b, isBack) {
  const x1 = a.x + FW / 2, y1 = a.y + FH;
  const x2 = b.x + FW / 2, y2 = b.y;
  if (isBack) {
    // a back edge (recursion / mutual call) loops around the side
    const side = x1 + FW / 2 + 26;
    return `M ${x1} ${y1} C ${side} ${y1 + 20}, ${side} ${y2 - 20}, ${x2} ${y2 - 2}`;
  }
  const my = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2 - 2}`;
}

function drawFlow() {
  const file = FLOW_FILE || flowFiles()[0];
  FLOW_FILE = file;
  if (!file) { stage.innerHTML = '<div id="welcome"><h2>No scripts to chart</h2></div>'; return; }

  const { nodes, edges, outward } = flowGraph(file);
  if (!nodes.length) {
    stage.innerHTML = `<div id="welcome"><h2>${file}</h2><p>No functions in this file.</p></div>`;
    return;
  }
  const { W, H, back } = flowLayout(nodes, edges);
  const byId = new Map(nodes.map(n => [n.id, n]));

  const eSVG = edges.map(e => {
    const a = byId.get(e.from_symbol), b = byId.get(e.to_symbol);
    const isBack = back.has(e.from_symbol + '|' + e.to_symbol);
    const n = e.call_sites?.length ?? 1;
    return `<path d="${flowEdge(a, b, isBack)}" class="fedge${isBack ? ' back' : ''}"
        data-a="${e.from_symbol}" data-b="${e.to_symbol}" marker-end="url(#fa)"/>` +
      (n > 1 ? `<text x="${(a.x + b.x) / 2 + FW / 2 + 6}" y="${(a.y + b.y) / 2 + FH / 2}"
        class="fcount">${n}×</text>` : '');
  }).join('');

  // calls that leave the script — dimmed, non-navigable-to-definition (spec A.4.2)
  const stubs = {};
  for (const e of outward) {
    (stubs[e.from_symbol] ||= []).push(SYMS.get(e.to_symbol));
  }

  const nSVG = nodes.map(n => {
    const cls = [
      'fnode',
      n.entry ? 'entry' : '',
      n.changed ? 'changed' : (n.impact > 0 ? `imp${Math.min(n.impact, 4)}` : '')
    ].join(' ').trim();
    const ext = (stubs[n.id] || []).length;
    const sig = (n.signature || n.name).replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
    return `<g class="${cls}" data-id="${n.id}" transform="translate(${n.x},${n.y})">
        <rect width="${FW}" height="${FH}" rx="7"/>
        <text x="11" y="17" class="fname">${n.name}</text>
        <text x="11" y="31" class="fsig">${sig.length > 30 ? sig.slice(0, 29) + '…' : sig}</text>
        ${n.entry ? `<text x="${FW - 9}" y="17" class="fentry" text-anchor="end">▸ entry</text>` : ''}
        ${ext ? `<text x="${FW - 9}" y="31" class="fext" text-anchor="end">${ext} ext</text>` : ''}
      </g>`;
  }).join('');

  const opts = flowFiles().map(f =>
    `<option value="${f}"${f === file ? ' selected' : ''}>${f}</option>`).join('');

  stage.innerHTML = `
    <div id="flowbar">
      <select id="flowfile">${opts}</select>
      <span class="fmeta">${nodes.length} nodes · ${edges.length} internal calls
        · ${outward.length} leaving the file</span>
      <label class="fjump" title="scroll the editor to a function when you hover its node">
        <input type="checkbox" id="flowjump" ${FLOW_JUMP ? 'checked' : ''}>
        jump to code on hover
      </label>
      <span class="fhint">click → card</span>
    </div>
    <div id="flowcanvas">
      <svg id="flowsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
        <defs>
          <marker id="fa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
                  markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--dim)"/>
          </marker>
        </defs>
        ${eSVG}${nSVG}
      </svg>
    </div>`;

  document.getElementById('flowjump').addEventListener('change', e => {
    setFlowJump(e.target.checked);
  });

  document.getElementById('flowfile').addEventListener('change', e => {
    FLOW_FILE = e.target.value;
    drawFlow();
    markFlowFile();
  });

  const svg = document.getElementById('flowsvg');
  svg.querySelectorAll('.fnode').forEach(g => {
    const id = g.dataset.id;

    g.addEventListener('mouseenter', () => {
      // spec A.3: highlight node + its edges, dim the rest, reveal the code
      const near = new Set([id]);
      svg.querySelectorAll('.fedge').forEach(p => {
        const hit = p.dataset.a === id || p.dataset.b === id;
        p.classList.toggle('hot', hit);
        p.classList.toggle('cold', !hit);
        if (hit) { near.add(p.dataset.a); near.add(p.dataset.b); }
      });
      svg.querySelectorAll('.fnode').forEach(o =>
        o.classList.toggle('cold', !near.has(o.dataset.id)));
      if (FLOW_JUMP) {
        window.dispatchEvent(new CustomEvent('codevis:nodeHover', { detail: { id } }));
      }
    });

    g.addEventListener('mouseleave', () => {
      svg.querySelectorAll('.fedge,.fnode').forEach(o =>
        o.classList.remove('hot', 'cold'));
      window.dispatchEvent(new CustomEvent('codevis:nodeHoverEnd'));
    });

    // A click is an explicit request, so it reveals regardless of the toggle.
    // The toggle is about HOVER, which is involuntary.
    g.addEventListener('click', ev => {
      openCard(id, null, ev.clientX, ev.clientY, g);
      window.dispatchEvent(new CustomEvent('codevis:reveal', { detail: { id } }));
    });
  });
}

function setFlowFile(f) {
  FLOW_FILE = f;
  if (document.body.dataset.view === 'flow') drawFlow();
  markFlowFile();
}


// ---------- boot ------------------------------------------------------------
// Must be the LAST thing in the file. setView() reaches the tether layer and the
// flow view, whose `const`/`let` are in the temporal dead zone until declared.
// (This exact ordering bug shipped twice; test_render.py now boots every build.)

// CSP forbids inline onclick attributes, so the view tabs and the two
// dynamically-inserted actions (.clr, .nact) are wired here instead.
document.querySelectorAll('.vtab').forEach(el =>
  el.addEventListener('click', () => setView(el.dataset.view)));
document.addEventListener('click', ev => {
  const t = ev.target;
  if (!t || !t.classList) return;
  if (t.classList.contains('clr')) { SEL = null; applyHighlight(); }
  if (t.classList.contains('nact')) resetLayout();
});

buildOutline();
setView('cards');

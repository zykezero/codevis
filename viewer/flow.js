
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
      <span class="fhint">hover → reveal in editor · click → card</span>
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

  document.getElementById('flowfile').addEventListener('change', e => {
    FLOW_FILE = e.target.value;
    drawFlow();
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
      window.dispatchEvent(new CustomEvent('codevis:nodeHover', { detail: { id } }));
    });

    g.addEventListener('mouseleave', () => {
      svg.querySelectorAll('.fedge,.fnode').forEach(o =>
        o.classList.remove('hot', 'cold'));
    });

    g.addEventListener('click', ev => {
      openCard(id, null, ev.clientX, ev.clientY, g);
      window.dispatchEvent(new CustomEvent('codevis:reveal', { detail: { id } }));
    });
  });
}

function setFlowFile(f) { FLOW_FILE = f; if (document.body.dataset.view === 'flow') drawFlow(); }

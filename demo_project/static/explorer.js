// 3D score explorer for the Visualize tab: actress × content × date, coloured by composite.
(function () {
  const cv = document.getElementById('cube'), cx = cv.getContext('2d');
  let R = [], az = 0.62, el = 0.42, zoom = 1, proj = [], hlGod = false, hlFav = false, ready = false;
  const RAMP = ['#123f7d', '#1c5cab', '#256abf', '#3987e5', '#6da7ec', '#b7d3f6'];
  const hex = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  function ramp(t) {
    t = Math.max(0, Math.min(1, t)); const s = t * (RAMP.length - 1), i = Math.floor(s), f = s - i;
    const a = hex(RAMP[i]), b = hex(RAMP[Math.min(i + 1, RAMP.length - 1)]);
    return `rgb(${a.map((x, k) => Math.round(x + (b[k] - x) * f)).join(',')})`;
  }
  const CORN = [[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]];
  const EDG = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
  function rot(p) {
    const x = p[0] - .5, y = p[1] - .5, z = p[2] - .5, ca = Math.cos(az), sa = Math.sin(az);
    let x1 = x * ca - z * sa, z1 = x * sa + z * ca, ce = Math.cos(el), se = Math.sin(el);
    return [x1, y * ce - z1 * se, y * se + z1 * ce];
  }
  function draw() {
    if (!ready) return;
    const dpr = window.devicePixelRatio || 1, rect = cv.getBoundingClientRect();
    cv.width = rect.width * dpr; cv.height = rect.height * dpr; cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = rect.height, cxp = W / 2, cyp = H / 2 + 12, sc = Math.min(W, H) * .6 * zoom;
    cx.clearRect(0, 0, W, H);
    const P = p => { const r = rot(p); return [cxp + r[0] * sc, cyp - r[1] * sc, r[2]]; };
    const cp = CORN.map(P);
    cx.strokeStyle = '#3a3f49'; cx.lineWidth = 1;
    for (const [a, b] of EDG) { cx.globalAlpha = (cp[a][2] + cp[b][2]) / 2 < 0 ? .35 : .9; cx.beginPath(); cx.moveTo(cp[a][0], cp[a][1]); cx.lineTo(cp[b][0], cp[b][1]); cx.stroke(); }
    cx.globalAlpha = 1; cx.font = '700 12px system-ui';
    const lab = (p, t, c) => { const s = P(p); cx.fillStyle = c; cx.fillText(t, s[0], s[1]); };
    lab([1.08, 0, 0], 'Actress', '#199e70'); lab([0, 1.06, 0], 'Date', '#9085e9'); lab([0, 0, 1.1], 'Content', '#d95926');
    proj = R.map((r, i) => { const s = P([r.actress || 0, r.date || 0, r.content || 0]); return { x: s[0], y: s[1], z: s[2], i }; });
    proj.sort((a, b) => a.z - b.z);
    const anyHl = hlGod || hlFav;
    for (const p of proj) {
      const r = R[p.i], hit = (hlGod && r.godtier) || (hlFav && r.favorite), depth = (p.z + .9) / 1.8;
      let col, rad;
      if (anyHl && !hit) { col = '#898781'; cx.globalAlpha = .08; rad = 1.6; }
      else if (hlFav && r.favorite) { col = '#e0b53a'; cx.globalAlpha = .98; rad = 4; }
      else if (hlGod && r.godtier) { col = '#9c7bff'; cx.globalAlpha = .95; rad = 3.6; }
      else { col = ramp(r.score || 0); cx.globalAlpha = .3 + depth * .55; rad = 2.1 + depth * 1.7; }
      cx.beginPath(); cx.arc(p.x, p.y, rad, 0, 7); cx.fillStyle = col; cx.fill();
    }
    cx.globalAlpha = 1;
  }
  let drag = false, px = 0, py = 0;
  cv.addEventListener('pointerdown', e => { drag = true; px = e.clientX; py = e.clientY; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener('pointermove', e => { if (drag) { az += (e.clientX - px) * .01; el = Math.max(-1.45, Math.min(1.45, el + (e.clientY - py) * .01)); px = e.clientX; py = e.clientY; draw(); } });
  cv.addEventListener('pointerup', () => drag = false);
  cv.addEventListener('wheel', e => { e.preventDefault(); zoom = Math.max(.5, Math.min(3, zoom * (e.deltaY < 0 ? 1.08 : .93))); draw(); }, { passive: false });
  window.addEventListener('resize', () => { if (document.getElementById('viz').classList.contains('on')) draw(); });
  const tgl = (btn, set) => btn.addEventListener('click', () => { set(btn.classList.toggle('on')); draw(); });
  tgl(document.getElementById('hl-god'), v => hlGod = v);
  tgl(document.getElementById('hl-fav'), v => hlFav = v);
  document.getElementById('viz-reset').onclick = () => { az = .62; el = .42; zoom = 1; draw(); };

  window.loadExplorer = function () {
    fetch('/api/explorer').then(r => r.json()).then(d => {
      R = d.rows; ready = true; draw();
      document.getElementById('lb-actress').innerHTML = d.actresses.map(a =>
        `<li><span>${a.name}${a.favorite ? ' ★' : ''}</span><span class="v">${Math.round(a.profile)}</span></li>`).join('');
      document.getElementById('lb-tags').innerHTML = d.tags.map(t =>
        `<li><span>${t.name}</span><span class="v">${Math.round(t.total)}</span></li>`).join('');
    });
  };
})();

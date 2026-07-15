const api = (u, opts) => fetch(u, opts).then(r => r.json());
const jpost = (u, body) => api(u, { method: body._method || 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const T = {}, loaded = {};

const ORD_COL = { title: '#', field: '_ord', width: 46, hozAlign: 'right', sorter: 'number',
  headerSortStartingDir: 'asc', formatter: c => (c.getValue() ?? 0) + 1 };
const INIT_SORT = [{ column: '_ord', dir: 'asc' }];
const ord = d => { (d || []).forEach((r, i) => r._ord = i); return d; };   // tag load order

const STATUS_VALUES = { '': 'unwatched', '1': '1 · dislike both', '2': '2 · like her/not it',
  '3': '3 · like/no rewatch', '4': '4 · love', '9': '9 · broken +', '88': '88 · dup', '94': '94 · broken-4' };

// ---------- formatters ----------
function linkFmt(cell) {
  const d = cell.getData();
  return `<a href="${d.url}" target="_blank" rel="noopener">${d.link_desc || d.url || ''}</a>`;
}
const esc = s => (s || '').replace(/"/g, '&quot;');
function actorsFmt(cell) {
  return '<span class="actors-cell">' + (cell.getValue() || []).map(a =>
    `<span class="a ${a.gender === 'male' ? 'male' : ''} ${a.favorite ? 'fav' : ''}"><a class="actor-link" data-actor="${esc(a.name)}">${a.name}</a> <span class="rk">${a.rank}</span></span>`
  ).join(' · ') + '</span>';
}
let actorFilter = null, lastTab = 'watch';
let currentActorInfo = null, currentActor = null;
function updateFavBtn() {
  const b = document.getElementById('ap-fav'), fav = currentActorInfo && currentActorInfo.favorite;
  b.textContent = fav ? '★ Favorite' : '☆ Favorite';
  b.classList.toggle('on', !!fav);
}
function gotoActor(name) {
  currentActor = name;
  const cur = document.querySelector('#tabs button.on');
  if (cur && cur.dataset.tab !== 'actorpage') lastTab = cur.dataset.tab;
  document.getElementById('actor-name').textContent = name;
  document.getElementById('ap-search').value = '';
  currentActorInfo = null;
  document.getElementById('ap-fav').textContent = '☆ Favorite';
  document.getElementById('ap-fav').classList.remove('on');
  showTab('actorpage');
  api(`/api/actor?name=${encodeURIComponent(name)}`).then(a => { currentActorInfo = a; updateFavBtn(); });
  loadActorPage(name, '');
}
document.getElementById('ap-search').oninput = debounce(
  () => loadActorPage(currentActor, document.getElementById('ap-search').value), 250);
document.getElementById('ap-fav').onclick = () => {
  if (!currentActorInfo || !currentActorInfo.id) return;
  const nv = currentActorInfo.favorite ? 0 : 1;
  jpost(`/api/actors/${currentActorInfo.id}`, { _method: 'PATCH', favorite: nv })
    .then(() => { currentActorInfo.favorite = nv; updateFavBtn(); });
};
function loadActorPage(name, q) {
  if (!name) return;
  q = q || '';
  [['favorites', 'ap-favorites', 'ap-fav-n'], ['rewatch', 'ap-rewatch', 'ap-re-n'],
   ['unwatched', 'ap-unwatched', 'ap-un-n'], ['grave', 'ap-grave', 'ap-grave-n']]
    .forEach(([cat, el, nEl]) => api(`/api/videos?category=${cat}&actor=${encodeURIComponent(name)}&q=${encodeURIComponent(q)}&limit=2000`).then(d => {
      document.getElementById(nEl).textContent = d.length;
      if (T['ap_' + cat]) T['ap_' + cat].setData(ord(d));
      else T['ap_' + cat] = makeVideoTable('#' + el, d, '260px', true);
    }));
}
document.getElementById('ap-back').onclick = () => showTab(lastTab || 'watch');
document.addEventListener('click', e => {
  const el = e.target.closest('.actor-link');
  if (el) { e.preventDefault(); gotoActor(el.dataset.actor); }
});
const score = c => { const v = c.getValue(); return v == null ? '' : `<span class="score">${(+v).toFixed(2)}</span>`; };
const pct = c => { const v = c.getValue(); return v == null ? '' : `<span class="score">${Math.round(v * 100)}%</span>`; };
const num = c => { const v = c.getValue(); return v == null ? '' : `<span class="score">${(+v).toFixed(0)}</span>`; };
function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
// date_watched cell editor: text input + a "today" button, and Ctrl+; inserts today
function dateEditor(cell, onRendered, success, cancel) {
  const wrap = document.createElement('span');
  wrap.className = 'date-edit';
  const input = document.createElement('input');
  input.value = cell.getValue() || '';
  const btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = 'today'; btn.title = 'insert today (Ctrl+;)';
  btn.onmousedown = e => e.preventDefault();               // keep focus so blur doesn't pre-commit
  btn.onclick = () => { input.value = todayStr(); success(input.value); };
  input.addEventListener('keydown', e => {
    if (e.ctrlKey && (e.key === ';' || e.key === ':')) { e.preventDefault(); input.value = todayStr(); }
    else if (e.key === 'Enter') success(input.value);
    else if (e.key === 'Escape') cancel();
  });
  input.addEventListener('blur', () => success(input.value));
  wrap.appendChild(input); wrap.appendChild(btn);
  onRendered(() => { input.focus(); input.select(); });
  return wrap;
}

function patchVideo(id, obj) { return jpost(`/api/videos/${id}`, { _method: 'PATCH', ...obj }); }
function onVideoEdit(cell, reloadWatch) {
  const f = cell.getField(), id = cell.getRow().getData().id;
  let v = cell.getValue();
  if (f === 'status') v = v === '' || v == null ? null : parseInt(v);
  if (f === 'favorite' || f === 'watchlist_flag') v = v ? 1 : 0;
  if (f === 'watch_count') v = v === '' || v == null ? 0 : parseInt(v) || 0;
  if (f === 'date_watched') v = (v || '').trim() || null;
  if (f === 'rank_override') v = v === '' || v == null ? null : parseFloat(v);
  patchVideo(id, { [f]: v }).then(() => { if (reloadWatch && f === 'status' && v !== 4 && v !== null) refreshWatch(); });
}

// ---------- watch list ----------
function watchCols(rewatch) {
  const cols = [
    { formatter: 'rowSelection', titleFormatter: 'rowSelection', hozAlign: 'center', width: 34, headerSort: false },
    ORD_COL,
    { title: 'Video', field: 'link_desc', formatter: linkFmt, widthGrow: 3, minWidth: 220 },
    { title: 'Actors', field: 'actors', formatter: actorsFmt, widthGrow: 2, minWidth: 190, headerSort: false },
    { title: 'St', field: 'status', width: 130, editor: 'list', editorParams: { values: STATUS_VALUES },
      mutatorData: v => v == null ? '' : String(v), formatter: c => STATUS_VALUES[c.getValue() ?? ''] || '' },
    { title: 'Fav', field: 'favorite', width: 50, hozAlign: 'center', editor: 'tickCross', formatter: 'tickCross' },
    { title: 'Watches', field: 'watch_count', width: 82, hozAlign: 'right', editor: 'number', formatter: num },
    { title: 'Watched', field: 'date_watched', width: 120, editor: dateEditor },
    { title: 'Actor', field: 'actress', formatter: score, hozAlign: 'right', width: 72 },
    { title: 'Content', field: 'content', formatter: score, hozAlign: 'right', width: 80 },
  ];
  cols.push({ title: 'Score', field: rewatch ? 'score_rewatch' : 'score_unwatched', formatter: score, hozAlign: 'right', width: 76 });
  return cols;
}
function makeWatch(el, data, rewatch) {
  const t = new Tabulator(el, { data, layout: 'fitColumns', selectableRows: true, height: rewatch ? '220px' : '560px', columns: watchCols(rewatch), initialSort: INIT_SORT });
  t.on('cellEdited', c => onVideoEdit(c, true));
  return t;
}
function setWatch(which, rows) {
  ord(rows);
  if (T[which]) T[which].setData(rows);
  else T[which] = makeWatch('#t-' + which, rows, which === 'rewatch');
}
function watchHint(res) {
  document.getElementById('watch-hint').textContent = `${res.rewatch.length} rewatch · ${res.unwatched.length} unwatched`;
}
function loadWatch(res) { setWatch('rewatch', res.rewatch); setWatch('unwatched', res.unwatched); watchHint(res); }
function reselectOnly(which) {
  api('/api/watchlist/reselect?which=' + which, { method: 'POST' }).then(res => {
    setWatch(which, which === 'rewatch' ? res.rewatch : res.unwatched);
    watchHint(res);
  });
}
const refreshWatch = () => api('/api/watchlist').then(loadWatch);
function selectedIds() {
  let ids = [];
  [T.rewatch, T.unwatched].forEach(t => {
    if (t) t.getRows('active').forEach(r => { if (r.isSelected()) ids.push(r.getData().id); });
  });
  return ids;   // display order (current sort), not click order
}
document.getElementById('re-rewatch').onclick = () => reselectOnly('rewatch');
document.getElementById('re-unwatched').onclick = () => reselectOnly('unwatched');
document.getElementById('open-sel').onclick = () => { const ids = selectedIds(); if (ids.length) jpost('/api/videos/open', { ids }); };
document.getElementById('mark-sel').onclick = () => { const ids = selectedIds(); if (ids.length) jpost('/api/videos/mark-watched', { ids }).then(refreshWatch); };

document.getElementById('save-now').onclick = () => {
  document.getElementById('save-status').textContent = 'saving…';
  jpost('/api/save', {}).then(r => document.getElementById('save-status').textContent = `saved ${r.at}`);
};
document.getElementById('shutdown').onclick = () => {
  if (!confirm('Turn off the app? Your data is saved first; the page will stop responding.')) return;
  jpost('/api/shutdown', {}).catch(() => {});
  document.body.innerHTML = '<div style="padding:60px;text-align:center;font:16px system-ui;color:#e8eaed;background:#14161a;min-height:100vh">App is off — your data was saved. You can close this tab and the console window.</div>';
};

// ---------- video tables (favorites / links) ----------
function videoCols(editable) {
  const c = [
    ORD_COL,
    { title: 'Video', field: 'link_desc', formatter: linkFmt, widthGrow: 3, minWidth: 220 },
    { title: 'Actors', field: 'actors', formatter: actorsFmt, widthGrow: 2, minWidth: 190, headerSort: false },
    { title: 'St', field: 'status', width: 130, editor: editable ? 'list' : false, editorParams: { values: STATUS_VALUES },
      mutatorData: v => v == null ? '' : String(v), formatter: c2 => STATUS_VALUES[c2.getValue() ?? ''] || '' },
    { title: 'Fav', field: 'favorite', width: 52, hozAlign: 'center', editor: editable ? 'tickCross' : false, formatter: 'tickCross' },
    { title: 'Later', field: 'watchlist_flag', width: 58, hozAlign: 'center', editor: editable ? 'tickCross' : false, formatter: 'tickCross' },
    { title: 'Watches', field: 'watch_count', width: 82, hozAlign: 'right', editor: editable ? 'number' : false, formatter: num },
    { title: 'Watched', field: 'date_watched', width: 120, editor: editable ? dateEditor : false },
    { title: 'Actor', field: 'actress', formatter: score, hozAlign: 'right', width: 72 },
    { title: 'Content', field: 'content', formatter: score, hozAlign: 'right', width: 80 },
    { title: 'Score', field: 'score_rewatch', formatter: score, hozAlign: 'right', width: 74 },
    { title: 'Override', field: 'rank_override', width: 84, hozAlign: 'right', editor: editable ? 'input' : false, formatter: score },
  ];
  return c;
}
function makeVideoTable(el, data, height, editable) {
  const t = new Tabulator(el, { data: ord(data), layout: 'fitColumns', height, columns: videoCols(editable), initialSort: INIT_SORT });
  if (editable) t.on('cellEdited', c => onVideoEdit(c, false));
  return t;
}
const loadFavorites = () => api('/api/favorites').then(d => T.favorites = makeVideoTable('#t-favorites', d, '75vh', true));
function loadLinks() {
  let url;
  if (actorFilter) url = `/api/videos?actor=${encodeURIComponent(actorFilter)}&limit=1000`;
  else {
    const q = document.getElementById('links-search').value, loc = document.getElementById('links-loc').value;
    url = `/api/videos?location=${loc}&q=${encodeURIComponent(q)}`;
  }
  api(url).then(d => { if (T.links) T.links.setData(ord(d)); else T.links = makeVideoTable('#t-links', d, '72vh', true); });
}

// ---------- actors ----------
function loadActors() {
  const q = document.getElementById('actors-search').value;
  api(`/api/actors?q=${encodeURIComponent(q)}`).then(d => {
    const cols = [
      { title: 'Actor', field: 'name', widthGrow: 3, minWidth: 150,
        formatter: c => `<a class="actor-link" data-actor="${esc(c.getValue())}">${c.getValue() || ''}</a>` },
      { title: 'Gender', field: 'gender', width: 100, editor: 'list', editorParams: { values: { '': '—', male: 'male', female: 'female' } } },
      { title: 'Fav', field: 'favorite', width: 56, hozAlign: 'center', editor: 'tickCross', formatter: 'tickCross' },
      { title: 'Exclude', field: 'exclude_from_score', width: 74, hozAlign: 'center', editor: 'tickCross', formatter: 'tickCross' },
      { title: 'Videos', field: 'n_videos', width: 74, hozAlign: 'right' },
      { title: 'Unwatched', field: 'n_unwatched', width: 96, hozAlign: 'right' },
      { title: '% watched', field: 'pct_watched', formatter: pct, hozAlign: 'right', width: 96 },
      { title: 'Total', field: 'total_watches', width: 76, hozAlign: 'right' },
      { title: 'Avg', field: 'avg_watches', formatter: score, hozAlign: 'right', width: 70 },
      { title: 'Last watched', field: 'last_watched', width: 116 },
      { title: 'Profile', field: 'profile', formatter: num, hozAlign: 'right', width: 82 },
    ];
    if (T.actors) { T.actors.setData(d); return; }
    T.actors = new Tabulator('#t-actors', { data: d, layout: 'fitColumns', height: '72vh', columns: cols });
    T.actors.on('cellEdited', c => {
      const f = c.getField(); let v = c.getValue();
      if (f === 'gender') v = v || null;
      if (f === 'favorite' || f === 'exclude_from_score') v = v ? 1 : 0;
      jpost(`/api/actors/${c.getRow().getData().id}`, { _method: 'PATCH', [f]: v });
    });
  });
}

// ---------- new videos ----------
document.getElementById('paste-add').onclick = () => {
  const text = document.getElementById('paste-box').value.trim(); if (!text) return;
  jpost('/api/videos/paste', { text }).then(r => {
    document.getElementById('paste-result').innerHTML =
      `<span class="ok">${r.added.length} added</span> · <span class="dup">${r.duplicates.length} duplicates</span>` +
      (r.duplicates.length ? '<br>' + r.duplicates.map(d => `↳ dup of “${d.matches}” (${d.location})`).join('<br>') : '');
    document.getElementById('paste-box').value = '';
    loadNew();
  });
};
document.getElementById('m-dw-today').onclick = () => { document.getElementById('m-dw').value = todayStr(); };
document.getElementById('m-add').onclick = () => {
  const g = id => document.getElementById(id);
  const data = { url: g('m-url').value, title: g('m-title').value, stars: g('m-stars').value, tags: g('m-tags').value,
    watch_count: g('m-wc').value, date_watched: g('m-dw').value, watchlist_flag: g('m-later').checked };
  if (!data.url.trim() && !data.title.trim()) { g('m-hint').textContent = 'enter a title or url'; return; }
  jpost('/api/videos/manual', data).then(r => {
    if (r.error) { g('m-hint').textContent = r.error; return; }
    if (r.duplicate) { g('m-hint').innerHTML = `<span style="color:var(--warn)">duplicate of “${r.matches}” (${r.location})</span>`; return; }
    g('m-hint').innerHTML = `<span style="color:var(--good)">added: ${r.link_desc}</span>`;
    ['m-url', 'm-title', 'm-stars', 'm-tags', 'm-wc', 'm-dw'].forEach(x => g(x).value = '');
    g('m-later').checked = false;
    refreshWatch();
  });
};
document.getElementById('scrape').onclick = () => {
  document.getElementById('new-hint').textContent = 'scraping…';
  jpost('/api/scrape', {}).then(r => {
    document.getElementById('new-hint').textContent =
      `${r.scraped.filter(x => !x.error).length} scraped, ${r.scraped.filter(x => x.error).length} errors`;
    loadNew(); refreshWatch();
  });
};
function loadNew() {
  api('/api/videos?location=new&limit=1000').then(d => {
    document.getElementById('new-hint').textContent = `${d.length} awaiting scrape`;
    if (T.new) { T.new.setData(d); return; }
    T.new = new Tabulator('#t-new', { data: d, layout: 'fitColumns', height: '40vh', columns: [
      { title: 'Video', field: 'link_desc', formatter: linkFmt, widthGrow: 3 },
      { title: 'Site', field: 'site', width: 110 }, { title: 'Added', field: 'date_added', width: 104 },
      { title: 'Watches', field: 'watch_count', width: 82, hozAlign: 'right', editor: 'number', formatter: num },
      { title: 'Watched', field: 'date_watched', width: 120, editor: dateEditor },
      { title: 'Remove', width: 90, hozAlign: 'center', headerSort: false,
        formatter: () => '<span class="del-btn">✕ remove</span>',
        cellClick: (e, cell) => {
          const v = cell.getRow().getData();
          if (confirm(`Remove “${v.link_desc}” from the new list?`))
            fetch('/api/videos/' + v.id, { method: 'DELETE' }).then(() => loadNew());
        } }] });
    T.new.on('cellEdited', c => onVideoEdit(c, false));
  });
}

// ---------- settings ----------
function loadSettings() {
  api('/api/settings').then(s => {
    const keys = ['w_actress', 'w_content', 'w_date', 'w_actress_unwatched', 'w_content_unwatched',
      'content_tag_weight', 'content_desc_weight', 'select_floor', 'n_unwatched', 'n_rewatch'];
    document.getElementById('weights').innerHTML = keys.map(k =>
      `<label>${k}<input id="set-${k}" value="${s[k] ?? ''}"></label>`).join('');
    const cm = document.querySelector(`input[name="cmethod"][value="${s.content_method || 'tag'}"]`);
    if (cm) cm.checked = true;
  });
  api('/api/backups').then(b => document.getElementById('backup-list').innerHTML =
    b.map(f => `<option>${f}</option>`).join(''));
}
document.getElementById('save-weights').onclick = () => {
  const patch = {};
  ['w_actress', 'w_content', 'w_date', 'w_actress_unwatched', 'w_content_unwatched',
   'content_tag_weight', 'content_desc_weight', 'select_floor', 'n_unwatched', 'n_rewatch']
    .forEach(k => { const el = document.getElementById('set-' + k); if (el) patch[k] = el.value; });
  jpost('/api/settings', { _method: 'PUT', ...patch }).then(() => { alert('saved & recomputed'); refreshWatch(); });
};
document.querySelectorAll('input[name="cmethod"]').forEach(r => r.onchange = () =>
  jpost('/api/settings', { _method: 'PUT', content_method: r.value }).then(() => { alert('content method: ' + r.value + ' (recomputed)'); refreshWatch(); }));
document.getElementById('rebuild-emb').onclick = () => {
  document.getElementById('emb-hint').textContent = 'rebuilding embeddings… (first run downloads the model + embeds all videos, can take a few minutes)';
  jpost('/api/embeddings/rebuild', {}).then(r =>
    document.getElementById('emb-hint').textContent = `embedded ${r.embedded} new · scored ${r.scored} videos`);
};
document.getElementById('restore').onclick = () => {
  const name = document.getElementById('backup-list').value;
  if (name && confirm('Restore ' + name + '? (a snapshot is taken first)'))
    jpost('/api/backups/restore', { name }).then(() => location.reload());
};

document.getElementById('links-search').oninput = debounce(() => { actorFilter = null; loadLinks(); }, 250);
document.getElementById('links-loc').onchange = loadLinks;
document.getElementById('actors-search').oninput = debounce(loadActors, 250);
function debounce(fn, ms) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }

// ---------- tabs ----------
const LOADERS = { newv: loadNew, favorites: loadFavorites, links: loadLinks, actors: loadActors,
  boards: () => { board('#t-tags', 'tags'); board('#t-terms', 'terms'); }, settings: loadSettings,
  viz: () => window.loadExplorer && window.loadExplorer() };
function board(el, kind) {
  api(`/api/leaderboard?kind=${kind}`).then(d => new Tabulator(el, { data: d, layout: 'fitColumns', height: '72vh', columns: [
    { title: kind === 'tags' ? 'Tag' : 'Term', field: 'name', widthGrow: 3 },
    { title: 'Total', field: 'total', formatter: num, hozAlign: 'right', width: 90 },
    { title: 'Videos', field: 'n_videos', width: 78, hozAlign: 'right' }] }));
}
function showTab(tab) {
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x.dataset.tab === tab));
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x.id === tab));
  if (!loaded[tab] && LOADERS[tab]) { LOADERS[tab](); loaded[tab] = true; }
}
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => showTab(b.dataset.tab));

api('/api/watchlist').then(loadWatch);

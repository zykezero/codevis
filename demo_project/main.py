"""FastAPI server for the watch-list manager. Run: python -m app  (or python app/main.py)"""
import os
import sys
import webbrowser
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import db as dbmod
import scoring
import selection
import backup
import scraping
from identity import canonicalize_url, slugify_link, desc_key, extract_site, split_stars

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP_DIR, 'static')

app = FastAPI(title='Watch-list manager')


@app.middleware('http')
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith('/static') or request.url.path == '/':
        resp.headers['Cache-Control'] = 'no-store'
    return resp


def conn():
    c = dbmod.connect()
    dbmod.init_db(c)
    return c


def videos_by_ids(c, ids):
    if not ids:
        return []
    q = ','.join('?' * len(ids))
    vids = {r['id']: dict(r) for r in c.execute(f"SELECT * FROM videos WHERE id IN ({q})", ids)}
    acts = defaultdict(list)
    for r in c.execute(
            f"""SELECT va.video_id, a.name, a.profile_pct, a.favorite, a.gender
                FROM video_actors va JOIN actors a ON a.id=va.actor_id
                WHERE va.video_id IN ({q}) ORDER BY va.position""", ids):
        acts[r['video_id']].append({
            'name': r['name'], 'rank': round((r['profile_pct'] or 0) * 100),
            'favorite': r['favorite'], 'gender': r['gender']})
    out = []
    for i in ids:
        v = vids.get(i)
        if v:
            v['actors'] = acts.get(i, [])
            out.append(v)
    return out


@app.get('/api/watchlist')
def watchlist():
    c = conn()
    un_ids, re_ids = selection.ensure(c)
    return {'unwatched': videos_by_ids(c, un_ids), 'rewatch': videos_by_ids(c, re_ids)}


@app.post('/api/watchlist/reselect')
def reselect(which: str = 'both'):
    c = conn()
    if which == 'rewatch':
        re_ids = selection.generate_rewatch(c)
        un_ids = selection.load(c, 'unwatched') or []
    elif which == 'unwatched':
        un_ids = selection.generate_unwatched(c)
        re_ids = selection.load(c, 'rewatch') or []
    else:
        un_ids, re_ids = selection.generate(c)
    return {'unwatched': videos_by_ids(c, un_ids), 'rewatch': videos_by_ids(c, re_ids)}


@app.get('/api/favorites')
def favorites():
    c = conn()
    ids = [r['id'] for r in c.execute(
        "SELECT id FROM videos WHERE favorite=1 ORDER BY score_rewatch DESC")]
    return videos_by_ids(c, ids)


CATEGORY_WHERE = {
    'favorites': 'v.favorite=1',
    'rewatch':   "v.location='watch' AND v.status=4 AND v.favorite=0",
    'unwatched': "v.location='watch' AND v.status IS NULL AND v.favorite=0",
    'grave':     "v.location='grave'",
}
_ORDER = "ORDER BY COALESCE(v.score_rewatch, v.score_unwatched) DESC LIMIT ?"


@app.get('/api/videos')
def videos(location: str = 'watch', q: str = '', actor: str = '', category: str = '', limit: int = 500):
    c = conn()
    where = CATEGORY_WHERE.get(category)
    if where or actor:
        cond = where or '1=1'
        join = ("JOIN video_actors va ON va.video_id = v.id "
                "JOIN actors a ON a.id = va.actor_id") if actor else ""
        args = [actor] if actor else []
        if actor:
            cond += " AND a.name = ?"
        if q:
            cond += " AND (v.link_desc LIKE ? OR v.description_raw LIKE ?)"
            args += [f'%{q}%', f'%{q}%']
        args.append(limit)
        ids = [r['id'] for r in c.execute(
            f"SELECT v.id FROM videos v {join} WHERE {cond} {_ORDER}", args)]
        return videos_by_ids(c, ids)
    sql = "SELECT id FROM videos v WHERE 1=1"
    args = []
    if location and location != 'all':
        sql += " AND location=?"; args.append(location)
    if q:
        sql += (" AND (link_desc LIKE ? OR description_raw LIKE ? OR EXISTS "
                "(SELECT 1 FROM video_actors va JOIN actors a ON a.id=va.actor_id "
                "WHERE va.video_id=v.id AND a.name LIKE ?))")
        args += [f'%{q}%', f'%{q}%', f'%{q}%']
    sql += " ORDER BY COALESCE(score_rewatch, score_unwatched) DESC LIMIT ?"; args.append(limit)
    ids = [r['id'] for r in c.execute(sql, args)]
    return videos_by_ids(c, ids)


@app.get('/api/actors')
def actors(q: str = '', limit: int = 500):
    c = conn()
    sql = "SELECT * FROM actors WHERE n_videos > 0"
    args = []
    if q:
        sql += " AND name LIKE ?"; args.append(f'%{q}%')
    sql += " ORDER BY profile DESC LIMIT ?"; args.append(limit)
    return [dict(r) for r in c.execute(sql, args)]


@app.get('/api/leaderboard')
def leaderboard(kind: str = 'tags', limit: int = 50):
    c = conn()
    if kind == 'tags':
        rows = c.execute("SELECT tag AS name, total, n_videos FROM tag_scores "
                         "ORDER BY total DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    # description phrases: rank real collocations by PMI (co-occurrence vs chance)
    import math
    uni = {r['term']: r['n_videos'] for r in c.execute(
        "SELECT term, n_videos FROM term_scores WHERE term NOT LIKE '% %'")}
    N = c.execute("SELECT COUNT(*) c FROM videos WHERE description_raw IS NOT NULL").fetchone()['c'] or 1
    scored = []
    for r in c.execute("SELECT term, total, n_videos FROM term_scores "
                       "WHERE term LIKE '% %' AND n_videos >= 3"):
        w = r['term'].split(' ')
        if len(w) != 2 or w[0] not in uni or w[1] not in uni:
            continue
        pmi = math.log(r['n_videos'] * N / (uni[w[0]] * uni[w[1]]))
        if pmi < 3:                          # not a real collocation (just common adjacent words)
            continue
        scored.append({'name': r['term'], 'total': r['total'], 'n_videos': r['n_videos'], 'pmi': round(pmi, 2)})
    scored.sort(key=lambda x: -x['n_videos'])   # among real phrases, most common first
    return scored[:limit]


@app.post('/api/videos/mark-watched')
def mark_watched(ids: list[int] = Body(..., embed=True)):
    c = conn()
    backup.snapshot('mark-watched')
    for vid in ids:
        # +1 watch and stamp the date; status is left for you to set afterwards
        c.execute("UPDATE videos SET watch_count=watch_count+1, date_watched=date('now') WHERE id=?", (vid,))
        c.execute("INSERT INTO watch_events(video_id) VALUES (?)", (vid,))
    c.commit()
    scoring.recompute(c)
    return {'updated': len(ids)}


@app.post('/api/videos/open')
def open_videos(ids: list[int] = Body(..., embed=True)):
    import time
    c = conn()
    url_by_id = {r['id']: r['url'] for r in c.execute(
        f"SELECT id, url FROM videos WHERE id IN ({','.join('?' * len(ids))})", ids)}
    opened = 0
    for i, vid in enumerate(ids):                 # follow the order sent (display order)
        u = url_by_id.get(vid)
        if u:
            if opened:
                time.sleep(0.4)                   # let each tab open before the next
            webbrowser.open(u)
            opened += 1
    return {'opened': opened}


@app.patch('/api/videos/{vid}')
def update_video(vid: int, patch: dict = Body(...)):
    c = conn()
    allowed = {'status', 'favorite', 'watchlist_flag', 'rank_override', 'url', 'watch_count', 'date_watched'}
    sets = {k: v for k, v in patch.items() if k in allowed}
    if not sets:
        return JSONResponse({'error': 'no valid fields'}, status_code=400)
    # auto location: status 4 or unwatched -> watch; else grave
    if 'status' in sets:
        st = sets['status']
        sets['location'] = 'watch' if (st == 4 or st is None) else 'grave'
    cols = ', '.join(f"{k}=?" for k in sets)
    c.execute(f"UPDATE videos SET {cols}, updated_at=datetime('now') WHERE id=?",
              (*sets.values(), vid))
    c.commit()
    scoring.recompute(c)
    return {'updated': vid, 'set': sets}


def get_or_create_actor(c, name):
    name = (name or '').strip()
    if not name:
        return None
    c.execute("INSERT OR IGNORE INTO actors(name) VALUES (?)", (name,))
    return c.execute("SELECT id FROM actors WHERE name=?", (name,)).fetchone()['id']


@app.post('/api/videos/paste')
def paste(text: str = Body(..., embed=True)):
    c = conn()
    added, dups = [], []
    for raw in [u.strip() for u in text.splitlines() if u.strip()]:
        url = canonicalize_url(raw)
        ld = slugify_link(url)
        key = desc_key(ld)
        if not key:
            continue
        ex = c.execute("SELECT link_desc, location FROM videos WHERE desc_key=?", (key,)).fetchone()
        if ex:
            dups.append({'url': raw, 'link_desc': ld, 'matches': ex['link_desc'], 'location': ex['location']})
            continue
        cur = c.execute("INSERT INTO videos(desc_key, link_desc, url, site, location, date_added) "
                        "VALUES (?,?,?,?, 'new', date('now'))", (key, ld, url, extract_site(url)))
        added.append({'id': cur.lastrowid, 'url': url, 'link_desc': ld})
    c.commit()
    return {'added': added, 'duplicates': dups}


@app.post('/api/videos/manual')
def add_manual(data: dict = Body(...)):
    url = canonicalize_url((data.get('url') or '').strip())
    title = (data.get('title') or '').strip()
    ld = title or slugify_link(url)
    key = desc_key(ld)
    if not key:
        return JSONResponse({'error': 'need a title or a url'}, status_code=400)
    c = conn()
    ex = c.execute("SELECT link_desc, location FROM videos WHERE desc_key=?", (key,)).fetchone()
    if ex:
        return {'duplicate': True, 'matches': ex['link_desc'], 'location': ex['location']}
    tags = (data.get('tags') or '').strip() or None
    try:
        wc = int(data.get('watch_count') or 0)
    except (TypeError, ValueError):
        wc = 0
    watchlist = 1 if data.get('watchlist_flag') else 0
    dw = (data.get('date_watched') or '').strip() or None
    cur = c.execute("""INSERT INTO videos
        (desc_key, link_desc, url, site, location, date_added, tags_raw, description_raw,
         watch_count, watchlist_flag, date_watched)
        VALUES (?,?,?,?, 'watch', date('now'), ?, ?, ?, ?, ?)""",
        (key, ld, url, extract_site(url), tags, title or ld, wc, watchlist, dw))
    vid = cur.lastrowid
    for pos, nm in enumerate(split_stars(data.get('stars'))):
        aid = get_or_create_actor(c, nm)
        if aid:
            c.execute("INSERT OR IGNORE INTO video_actors(video_id,actor_id,position) VALUES (?,?,?)",
                      (vid, aid, pos))
    c.commit()
    scoring.recompute(c)
    return {'duplicate': False, 'id': vid, 'link_desc': ld}


@app.post('/api/scrape')
def scrape(ids: list[int] = Body(default=None, embed=True)):
    c = conn()
    backup.snapshot('scrape')
    if ids:
        q = ','.join('?' * len(ids))
        rows = c.execute(f"SELECT id, url FROM videos WHERE id IN ({q})", ids).fetchall()
    else:
        rows = c.execute("SELECT id, url FROM videos WHERE location='new'").fetchall()
    results = []
    for r in rows:
        res = scraping.scrape_url(r['url'])
        if res.get('error'):
            results.append({'id': r['id'], 'error': res['error']})
            continue
        c.execute("UPDATE videos SET tags_raw=?, description_raw=COALESCE(?, description_raw), "
                  "location='watch' WHERE id=?", (res['tags'], res['description'] or None, r['id']))
        names = [p for nm in res['stars'] for p in split_stars(nm)]
        for pos, nm in enumerate(names):
            aid = get_or_create_actor(c, nm)
            if aid:
                c.execute("INSERT OR IGNORE INTO video_actors(video_id, actor_id, position) VALUES (?,?,?)",
                          (r['id'], aid, pos))
        results.append({'id': r['id'], 'stars': res['stars'], 'tags': res['tags']})
    c.commit()
    scoring.recompute(c)
    return {'scraped': results}


@app.get('/api/actor')
def actor_info(name: str):
    c = conn()
    r = c.execute("SELECT id, name, favorite, gender, n_videos FROM actors WHERE name=?", (name,)).fetchone()
    return dict(r) if r else {}


@app.patch('/api/actors/{aid}')
def update_actor(aid: int, patch: dict = Body(...)):
    c = conn()
    allowed = {'gender', 'favorite', 'exclude_from_score'}
    sets = {k: v for k, v in patch.items() if k in allowed}
    if not sets:
        return JSONResponse({'error': 'no valid fields'}, status_code=400)
    c.execute(f"UPDATE actors SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
              (*sets.values(), aid))
    c.commit()
    if 'exclude_from_score' in sets:
        scoring.recompute(c)      # exclude changes video actress averages
    return {'updated': aid, 'set': sets}


@app.delete('/api/videos/{vid}')
def delete_video(vid: int):
    c = conn()
    backup.snapshot('delete')
    c.execute("DELETE FROM video_actors WHERE video_id=?", (vid,))
    c.execute("DELETE FROM watch_events WHERE video_id=?", (vid,))
    c.execute("DELETE FROM embeddings WHERE video_id=?", (vid,))
    c.execute("DELETE FROM videos WHERE id=?", (vid,))
    c.commit()
    scoring.recompute(c)
    return {'deleted': vid}


@app.get('/api/settings')
def get_settings():
    c = conn()
    return {r['key']: r['value'] for r in c.execute("SELECT key, value FROM settings")}


@app.put('/api/settings')
def put_settings(patch: dict = Body(...)):
    c = conn()
    for k, v in patch.items():
        c.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
    c.commit()
    scoring.recompute(c)
    return {'updated': list(patch)}


@app.get('/api/explorer')
def explorer():
    c = conn()
    rows = []
    for r in c.execute("""
        SELECT id, link_desc, status, favorite, actress, content, date,
               COALESCE(score_rewatch, score_unwatched) AS score
        FROM videos WHERE location='watch'"""):
        d = dict(r)
        d['unwatched'] = 1 if d['status'] is None else 0
        rows.append(d)
    god = {r['video_id'] for r in c.execute(
        "SELECT DISTINCT va.video_id FROM video_actors va JOIN actors a ON a.id=va.actor_id WHERE a.favorite=1")}
    for d in rows:
        d['godtier'] = 1 if d['id'] in god else 0
    actresses = [dict(r) for r in c.execute(
        "SELECT name, profile, n_videos, favorite FROM actors WHERE n_videos>0 ORDER BY profile DESC LIMIT 50")]
    tags = [dict(r) for r in c.execute("SELECT tag name, total FROM tag_scores ORDER BY total DESC LIMIT 50")]
    return {'rows': rows, 'actresses': actresses, 'tags': tags}


@app.post('/api/embeddings/rebuild')
def rebuild_embeddings():
    c = conn()
    backup.snapshot('embeddings')
    import embed
    res = embed.rebuild(c)
    scoring.recompute(c)
    return res


@app.get('/api/backups')
def list_backups():
    import glob
    files = sorted(glob.glob(os.path.join(backup.BACKUP_DIR, 'library-*.db')), reverse=True)
    return [os.path.basename(f) for f in files]


@app.post('/api/backups/restore')
def restore_backup(name: str = Body(..., embed=True)):
    import shutil
    src = os.path.join(backup.BACKUP_DIR, os.path.basename(name))
    if not os.path.exists(src):
        return JSONResponse({'error': 'not found'}, status_code=404)
    backup.snapshot('pre-restore')
    shutil.copy2(src, dbmod.DB_PATH)
    return {'restored': os.path.basename(name)}


@app.post('/api/save')
def save_now():
    from datetime import datetime
    path = backup.snapshot('manual')
    return {'saved': os.path.basename(path) if path else None,
            'at': datetime.now().strftime('%H:%M:%S')}


@app.post('/api/shutdown')
def shutdown():
    import threading
    import time
    backup.snapshot('shutdown')          # final save before exiting
    def _die():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_die, daemon=True).start()
    return {'ok': True}


@app.get('/')
def index():
    return FileResponse(os.path.join(STATIC, 'index.html'))


app.mount('/static', StaticFiles(directory=STATIC), name='static')


def run():
    import uvicorn
    url = 'http://127.0.0.1:8000'
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f'Watch-list manager -> {url}')
    uvicorn.run(app, host='127.0.0.1', port=8000, log_level='warning')


if __name__ == '__main__':
    run()

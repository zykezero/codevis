"""Semi-random watch-list draws: key = randint(floor,100) * (1 + score), floored so
the score leads and the shuffle is gentle. Selections persist in display_state."""
import json
import random
from collections import defaultdict

from db import get_setting


def _draw(rows, score_key, n, floor):
    scored = []
    for r in rows:
        s = r.get(score_key) or 0.0
        scored.append((random.randint(int(floor), 100) * (1 + s), r['id']))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:n]]


def _draw_unique_actors(conn, rows, score_key, n, floor):
    """Like _draw, but no actor appears in more than one pick (falls back to fill if short)."""
    ids = [r['id'] for r in rows]
    actor_map = defaultdict(set)
    if ids:
        q = ','.join('?' * len(ids))
        for row in conn.execute(f"SELECT video_id, actor_id FROM video_actors WHERE video_id IN ({q})", ids):
            actor_map[row['video_id']].add(row['actor_id'])
    keyed = sorted(((random.randint(int(floor), 100) * (1 + (r.get(score_key) or 0.0)), r['id'])
                    for r in rows), key=lambda x: -x[0])
    picked, used = [], set()
    for _, vid in keyed:
        acts = actor_map.get(vid, set())
        if acts and (acts & used):
            continue
        picked.append(vid); used |= acts
        if len(picked) >= n:
            return picked
    for _, vid in keyed:            # too few unique-actor videos: fill remaining slots
        if vid not in picked:
            picked.append(vid)
            if len(picked) >= n:
                break
    return picked


def generate_unwatched(conn):
    floor = get_setting(conn, 'select_floor', 60)
    n_un = int(get_setting(conn, 'n_unwatched', 20))
    un = [dict(r) for r in conn.execute(
        "SELECT id, score_unwatched, watchlist_flag FROM videos "
        "WHERE location='watch' AND status IS NULL AND favorite=0").fetchall()]
    pinned = [r['id'] for r in un if r['watchlist_flag']]
    rest = [r for r in un if not r['watchlist_flag']]
    un_ids = (pinned + _draw(rest, 'score_unwatched', max(0, n_un - len(pinned)), floor))[:n_un]
    _save(conn, 'unwatched', un_ids)
    return un_ids


def generate_rewatch(conn):
    floor = get_setting(conn, 'select_floor', 60)
    n_re = int(get_setting(conn, 'n_rewatch', 5))
    re = [dict(r) for r in conn.execute(
        "SELECT id, score_rewatch FROM videos "
        "WHERE location='watch' AND status=4 AND favorite=0").fetchall()]
    re_ids = _draw_unique_actors(conn, re, 'score_rewatch', n_re, floor)
    _save(conn, 'rewatch', re_ids)
    return re_ids


def generate(conn):
    return generate_unwatched(conn), generate_rewatch(conn)


def _save(conn, name, ids):
    conn.execute("INSERT OR REPLACE INTO display_state(table_name, video_ids, generated_at) "
                 "VALUES (?,?,datetime('now'))", (name, json.dumps(ids)))
    conn.commit()


def load(conn, name):
    r = conn.execute("SELECT video_ids FROM display_state WHERE table_name=?", (name,)).fetchone()
    return json.loads(r['video_ids']) if r else None


def ensure(conn):
    """Return the saved selection, generating a fresh one on first use."""
    if load(conn, 'unwatched') is None or load(conn, 'rewatch') is None:
        generate(conn)
    return load(conn, 'unwatched'), load(conn, 'rewatch')

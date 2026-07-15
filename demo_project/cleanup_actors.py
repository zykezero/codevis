"""Split combined actor rows ("A & B") into individual actors, reassign their videos,
and remove the combined row. Idempotent. Run: python app/cleanup_actors.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import connect
from identity import split_stars
import scoring
import backup


def cleanup(conn):
    combined = conn.execute(
        "SELECT id, name FROM actors WHERE name LIKE '% & %'").fetchall()
    changed = 0
    for a in combined:
        parts = split_stars(a['name'])
        if len(parts) < 2:
            continue
        vids = [r['video_id'] for r in conn.execute(
            "SELECT video_id FROM video_actors WHERE actor_id=?", (a['id'],))]
        for name in parts:
            conn.execute("INSERT OR IGNORE INTO actors(name) VALUES (?)", (name,))
            nid = conn.execute("SELECT id FROM actors WHERE name=?", (name,)).fetchone()['id']
            for vid in vids:
                conn.execute("INSERT OR IGNORE INTO video_actors(video_id, actor_id, position) "
                             "VALUES (?,?,0)", (vid, nid))
        conn.execute("DELETE FROM video_actors WHERE actor_id=?", (a['id'],))
        conn.execute("DELETE FROM actors WHERE id=?", (a['id'],))
        changed += 1
        print(f"  split: {a['name']}  ->  {parts}")
    conn.commit()
    if changed:
        scoring.recompute(conn)
    return changed


if __name__ == '__main__':
    backup.snapshot('cleanup-actors')
    c = connect()
    n = cleanup(c)
    print(f"cleaned {n} combined actor(s)")

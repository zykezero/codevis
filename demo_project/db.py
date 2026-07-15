"""SQLite schema + connection for the watch-list manager."""
import os
import sqlite3

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'library.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY,
    desc_key        TEXT UNIQUE,            -- dedup identity (normalized description)
    link_desc       TEXT,                  -- hyperlink text
    url             TEXT,                  -- current URL (mutable)
    site            TEXT,                  -- source domain (hqporner, xtapes, bingato, ...)
    location        TEXT DEFAULT 'new',    -- new | watch | grave
    status          INTEGER,               -- 1,2,3,4,9,88,94 ; NULL = unwatched
    favorite        INTEGER DEFAULT 0,     -- was keep="T"
    watchlist_flag  INTEGER DEFAULT 0,     -- "watch later"
    rank_override   REAL,                  -- 0-1 replaces the composite when set
    watch_count     INTEGER DEFAULT 0,
    date_added      TEXT,
    date_watched    TEXT,
    tags_raw        TEXT,
    description_raw TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    -- cached scores (filled by scoring.recompute)
    actress_raw     REAL,
    content_tag_raw REAL,
    content_desc_raw REAL,
    content_embed_raw REAL,
    actress         REAL,
    content         REAL,
    date            REAL,
    score_unwatched REAL,
    score_rewatch   REAL
);

CREATE TABLE IF NOT EXISTS embeddings (
    video_id  INTEGER PRIMARY KEY,
    vec       BLOB,
    text_hash TEXT
);

CREATE TABLE IF NOT EXISTS actors (
    id                 INTEGER PRIMARY KEY,
    name               TEXT UNIQUE,
    gender             TEXT,               -- male | female | NULL (manual)
    favorite           INTEGER DEFAULT 0,  -- auto: any favorite video
    exclude_from_score INTEGER DEFAULT 0,  -- disliked performer: dropped from a video's actress avg
    -- cached stats
    n_videos      INTEGER,
    n_watched     INTEGER,
    n_unwatched   INTEGER,
    total_watches INTEGER,
    avg_watches   REAL,
    pct_watched   REAL,
    last_watched  TEXT,
    profile       REAL,
    profile_pct   REAL
);

CREATE TABLE IF NOT EXISTS video_actors (
    video_id INTEGER,
    actor_id INTEGER,
    position INTEGER,
    PRIMARY KEY (video_id, actor_id)
);

CREATE TABLE IF NOT EXISTS tag_scores (
    tag TEXT PRIMARY KEY, total REAL, n_rated INTEGER, n_videos INTEGER
);
CREATE TABLE IF NOT EXISTS term_scores (
    term TEXT PRIMARY KEY, total REAL, n_rated INTEGER, n_videos INTEGER
);

CREATE TABLE IF NOT EXISTS watch_events (
    id INTEGER PRIMARY KEY, video_id INTEGER, watched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS display_state (
    table_name TEXT PRIMARY KEY, video_ids TEXT, generated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_location ON videos(location);
CREATE INDEX IF NOT EXISTS idx_videos_status   ON videos(status);
CREATE INDEX IF NOT EXISTS idx_va_actor        ON video_actors(actor_id);
"""

DEFAULT_SETTINGS = {
    'w_actress': '0.50', 'w_content': '0.35', 'w_date': '0.15',
    'w_actress_unwatched': '0.59', 'w_content_unwatched': '0.41',
    'content_tag_weight': '0.5', 'content_desc_weight': '0.5',
    'select_floor': '60', 'n_unwatched': '20', 'n_rewatch': '5',
    'content_method': 'tag',      # 'tag' (tag+desc totals) | 'embed' (semantic classifier)
}


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    for tbl, col, typ in [('videos', 'content_embed_raw', 'REAL'),
                          ('actors', 'n_unwatched', 'INTEGER'),
                          ('actors', 'last_watched', 'TEXT')]:
        try:                   # add columns to pre-existing DBs
            conn.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} {typ}')
        except sqlite3.OperationalError:
            pass
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute('INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)', (k, v))
    conn.commit()


def get_setting(conn, key, default=None, cast=float):
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    if row is None:
        return default
    try:
        return cast(row['value'])
    except (TypeError, ValueError):
        return row['value']

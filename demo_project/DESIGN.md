# Watch-list manager — design doc

A local, single-user web app that replaces the `web_links.xlsx` + R workflow with one
tool: scrape → dedupe → score → rank → watch → re-rank. Runs on `localhost`, private,
no accounts, no cloud.

> **Safeguard:** everything new lives in `/app`. The migration reads the existing
> `web_links.xlsx` and R files **read-only** and never modifies them. The original
> workflow keeps working until the app has fully taken over.

---

## 1. Principles

- **One source of truth: a SQLite database** (`/app/data/library.db`). Excel is fully retired.
- **Identity is the description, not the URL.** Site URLs mutate (ID numbers change) while
  the description is stable, so every video gets an internal integer `id` and a normalized
  `desc_key`; dedup and the watch-history log key on those, never on the raw URL.
- **Scores recompute whenever the link table changes** (new links, status edits, watch
  events, weight changes). One `recompute()` pass, cached back onto each row.
- **Local & disposable-safe:** automatic backups on every write; the DB file is the only
  thing that matters and is trivial to copy.
- **No build step.** Static front end (vanilla JS + Tabulator) served by the backend, so
  there's nothing to compile and it stays maintainable.

---

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend / API | **Python + FastAPI** | Reuses the scoring engine already written; best scraping libs |
| Database | **SQLite** (+ FTS5) | Single file, transactional, full-text search, easy backup |
| Scraping | **Playwright** (JS/Cloudflare sites) + `requests`/`selectolax` (simple) | Handles eporner/Cloudflare that `rvest` struggled with |
| Tables UI | **Tabulator** (standalone JS) | Sort, filter, inline-edit, multi-row select — no npm/build |
| Charts | The existing **canvas/SVG 3D explorer** | Already built; drops in as the Visualizations tab |
| Scoring | **Python module** (`app/scoring.py`) | Port of `debug/build_score_comparison.py` |

Run model: `python -m app` starts the server and opens `http://localhost:8000` in the browser.

---

## 3. Project layout

```
app/
  DESIGN.md              ← this file
  main.py                ← FastAPI app, routes, static serving
  db.py                  ← SQLite connection, schema, migrations
  models.py              ← table definitions / dataclasses
  scoring.py             ← profile / tag-total / date / composite (ported)
  scraping.py            ← Playwright + requests scrapers, URL canonicalization
  identity.py            ← desc_key normalization + dedup
  selection.py           ← semi-random watch-list selection (floor method)
  backup.py              ← snapshot-on-write
  migrate_excel.py       ← one-time web_links.xlsx → SQLite import (read-only source)
  static/
    index.html           ← shell + tabs
    tables.js            ← Tabulator configs per table
    explorer.html/js     ← the 3D score explorer (moved from debug/)
    app.css
  data/
    library.db           ← the database (gitignored)
    backups/             ← timestamped .db snapshots
    scrape_cache/        ← raw HTML cache for re-parsing without re-fetching
```

---

## 4. Data model

Identity note: `videos.id` is permanent; `url` is mutable; `desc_key` is the dedup key.

### `videos`
| column | type | notes |
|---|---|---|
| id | INTEGER PK | permanent internal id |
| desc_key | TEXT UNIQUE | normalized description → the dedup identity |
| link_desc | TEXT | human description (hyperlink text) |
| url | TEXT | current URL, mutable |
| location | TEXT | `new` \| `watch` \| `grave` (auto-maintained) |
| status | INTEGER NULL | 1,2,3,4,9,88,94; NULL = unwatched |
| favorite | INTEGER | 1 = favorite (was `keep="T"`) |
| watchlist_flag | INTEGER | "watch-later" — pulls to front until watched |
| rank_override | REAL NULL | 0–1; when set, replaces the composite score |
| watch_count | INTEGER | authoritative count (seeded at migration, +1 per event) |
| date_added | DATE | set when the link is added |
| date_watched | DATE NULL | last watch (also derivable from `watch_events`) |
| tags_raw | TEXT | scraped tag string |
| description_raw | TEXT | scraped long description (for content score) |
| created_at / updated_at | TIMESTAMP | |
| — cached scores — | | recomputed by `recompute()` |
| actress_raw, content_raw | REAL | pre-normalization means |
| actress, content, date | REAL | 0–1 components (percentile / linear) |
| score_unwatched, score_rewatch | REAL | table-specific composites |

### `actors`
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT UNIQUE | |
| gender | TEXT NULL | `male` \| `female` — **manual assignment by you** |
| favorite | INTEGER | favorite actor (was godtier; any favorite video, or manual) |
| — cached stats — | | n_videos, pct_watched, total_watches, avg_watches, profile, profile_pct |

### `video_actors`  — (video_id, actor_id, position)  many-to-many
### `tags` — (id, tag) ; `video_tags` — (video_id, tag_id) ; plus cached `tag_total`
### `watch_events` — (id, video_id, watched_at) — forward-only history, keyed to video_id
### `settings` — (key, value) — weights, floors, table sizes, seed
### `display_state` — (table_name, video_ids JSON, generated_at) — the saved selection

**Full-text search:** FTS5 virtual table over `link_desc` + `description_raw` + actor names
for the description/star search box.

---

## 5. Identity & deduplication

1. **URL canonicalization** (port `CONFIG$url_rewrites` from R): `hqporner.com` → `m.hqporner.com`,
   `v./en./hd.xtapes` → `xtapes`, strip tracking params, lowercase host. Stored as `url`.
2. **`desc_key`** = `link_desc` lower-cased, punctuation/whitespace collapsed, trailing site
   noise removed. This is the dedup key (matches today's "dedupe by description" behavior).
3. On paste/scrape, a new link is a **duplicate** if its `desc_key` already exists → surfaced
   instantly in the New-videos table, not added twice. If the URL differs, we can **update**
   the stored `url` on the existing video (URL drifted) without touching its id or history.

---

## 6. Scoring engine (`scoring.py`)

Ported from the finalized model. `recompute()` runs a full pass (fast enough at this scale)
whenever the link table changes.

**Per-actress — profile score** (over all videos, both watch & grave, excluding hard-broken 99):
```
profile = good% × avg_rating × avg_watches × (0.5 + 0.5·watched%)
  good%   : share of her videos in {NA, 3, 4, 9, 88, 94}   ← 9 now counts good (slight positive)
  rating  : 4/88/94→4, 3→3, 2→2, 1→1  (9 unrated)
  watched%: rated videos ÷ total ;  no rated video → 75th-percentile actress
```

**Per-tag — tag total:** `Σ over videos with the tag (1 + watches + content_bonus)`;
`content_bonus` 4/88/94→+2, 3→0, 2→−2, 1→−2, else 0. Unrated tag → 75th percentile.
Content also folds in the **description residual** (long description minus actor names,
stopwords, and the video's own tags) so the desc contributes without double-counting actors.

**Per-video components** (percentile-scaled 0–1 across the ranked set):
- `actress` = percentile(mean profile over the video's actors)
- `content` = percentile(mean tag-total over the video's tags + desc residual)
- `date` = `days_since_last_watch / max`, unwatched = 1

**Two composites (the reason the watch list is two tables):**
```
score_unwatched = wA'·actress + wC'·content            (date dropped, weights renormalized)
score_rewatch   = wA·actress + wC·content + wD·date     (default 0.50 / 0.35 / 0.15)
```
`rank_override`, when set, **replaces** the composite (a direct 0–1 value) so a good video
with an otherwise-disliked-content actor isn't buried.

**Recompute triggers:** add/scrape links, status change, watch event, favorite toggle,
override edit, weight change. (Debounced so a bulk edit recomputes once.)

---

## 7. Status model

Input UI is three toggles — **actor** (dislike/like), **content** (dislike/like),
**rewatch** (yes/no) — mapped to the stored code:

| code | actor | content | rewatch | notes |
|---|---|---|---|---|
| 1 | dislike | dislike | no | |
| 2 | like | dislike | no | actor not penalized; content penalized |
| 3 | like | like | no | |
| 4 | like | like | yes | the rewatch pool |
| 9 | (slight +) | — | no | doesn't load but you added it → **slight actor positive**, no content, not in list |
| 88 | like | like | no | dup from 2nd source; as strong as a 4 for signal, **not** a rewatch |
| 94 | like | like | no | broken former-4; keeps positive signal, out of the list |

`location` auto-follows: unwatched or status 4 → `watch`; anything else → `grave`;
favorites shown separately.

---

## 8. Scraping (`scraping.py`)

- Per-site extractors return `{url, link_desc, description_raw, tags, stars}`.
- Playwright for JS/Cloudflare sites; `requests`+`selectolax` for the rest; raw HTML cached
  to `data/scrape_cache/` so re-parsing needs no re-fetch.
- Retries + a scrape log; failures leave the row in `new` flagged, not lost.
- Runs on the "Scrape and add" button; canonicalizes URL and computes `desc_key` before insert.

---

## 9. Screens

Tabs: **New videos · Watch list · Favorites · Links · Actors · Visualizations.**

### New videos
- Bulk-paste newline-separated URLs; `date_added` stamped on add.
- Instant duplicate flagging (by `desc_key`) before scraping.
- "Scrape & add" → populates fields, moves rows to `watch`.

### Watch list — two tables
- **Rewatch** (5 rows): semi-random weighted pick from status-4 videos, biased to high `score_rewatch`.
- **Unwatched** (20 rows): semi-random ranked pick from unwatched, by `score_unwatched` (no date).
- Columns: **hyperlink** (link_desc as the text, URL as href) · **actors** (nested, each with
  their rank) · **actor score · content score · [date score — rewatch only] · composite** ·
  status toggles · favorite · override.
- **Reselect** button re-draws; the current selection is saved to `display_state` so reopening
  shows the same videos until you reselect or watch them.
- **Filters:** star name, favorite-actor, description search (FTS).
- **Multi-select** rows → **Open all** (backend opens each URL via the OS, dodging the
  pop-up blocker) · **Mark watched** (bulk: +1 watch_count, new watch_event, set date) ·
  bulk status/favorite.
- `watchlist_flag` videos are pinned to the front of the unwatched draw until watched.

### Favorites
- `favorite = 1` videos, shown here and excluded from the watch-list draws.

### Links
- The full videos table, sortable/searchable, all scores, same edit features; stays in sync
  with the watch list. Extra `watchlist_flag` toggle.

### Actors
- Sortable: name, **gender (manual)**, favorite, n_videos, % watched, total/avg watches,
  profile score + percentile.

### Visualizations
- The 3D explorer (actress × content × date), colored by composite, weight sliders wired to
  `settings`, godtier highlights, leaderboards. Reads live DB scores.

---

## 10. Semi-random selection (`selection.py`)

Uses the tuned **floor method**: key = `random_band × (1 + score)` with a floored band so the
score leads and the shuffle is gentle (band max/min ratio near the score's 2× swing). Floor and
table sizes live in `settings`. Rewatch = weighted draw of 5; unwatched = draw of 20;
`watchlist_flag` and `favorite` handled as hard pins. Selection stored to `display_state`.

---

## 11. Backups (`backup.py`)

- Copy `library.db` → `data/backups/library-YYYYMMDD-HHMMSS.db` before each write batch (or on a
  timer + on close). Keep last N + daily rollups. One-line restore (swap the file).

---

## 12. Migration (`migrate_excel.py`, one-time, read-only source)

Read `web_links.xlsx` (hq, grave, actress_scores, dict, new) →
- videos: hq→`watch`, grave→`grave`, new→`new`; map columns; compute `desc_key`, canonical URL,
  favorite from `keep="T"`, seed `watch_count`/`date_watched`.
- actors from exploded `Stars` (+ existing `actress_scores` gender/favorite if present; gender
  still to be filled in manually).
- tags from `tags`. Then `recompute()`. Validate counts against the workbook.

---

## 13. API sketch

```
GET  /api/videos?location=&q=&filters      POST /api/videos/paste  (bulk urls)
POST /api/scrape                           PATCH /api/videos/{id}  (status, favorite, override, url)
POST /api/videos/mark-watched  (ids[])     POST  /api/videos/open  (ids[] → OS open)
GET  /api/watchlist  (rewatch+unwatched)   POST  /api/watchlist/reselect
GET  /api/actors     PATCH /api/actors/{id}  (gender, favorite)
GET  /api/settings   PUT   /api/settings   (weights, floors, sizes) → triggers recompute
POST /api/recompute
```

---

## How to run (built)

```
pip install -r app/requirements.txt      # fastapi uvicorn openpyxl requests selectolax
python app/migrate_excel.py              # one-time: web_links.xlsx -> app/data/library.db
python -m app                            # serve http://127.0.0.1:8000
```

Build status: **Phase 0–3 implemented.** Tabulator is vendored (`app/static/vendor`) so the
UI runs offline; only live scraping needs network. Remaining polish: recompute is a full pass
on every edit (fast at this scale — sub-second; debounce only if it ever lags), and gender
labels are yours to fill in on the Actors tab.

## 14. Everything to do (phased task list)

**Phase 0 — Foundation**
1. Scaffold `/app` (FastAPI, static serving, `python -m app`).
2. `db.py` schema + FTS5; `backup.py`.
3. `identity.py` (URL canonicalization + desc_key + dedup).
4. `scoring.py` — port the finalized model; add 9-as-slight-positive; description residual; two composites; override.
5. `migrate_excel.py` — import + validate against the workbook. **Milestone: DB populated, scores match the explorer.**

**Phase 1 — Core daily loop**
6. New-videos table: bulk paste, instant dedup, date_added.
7. `scraping.py` per-site extractors (start with hqporner + xtapes; then eporner via Playwright).
8. Scrape & add → move `new`→`watch`.
9. Watch list: two tables, columns, hyperlink-as-desc, nested actors-with-rank.
10. `selection.py` semi-random draws + `display_state` persistence + reselect.
11. Status toggle UI (actor/content/rewatch → code) + auto location move (watch↔grave).
12. Multi-select: open-all (OS), bulk mark-watched (+watch_events), bulk status/favorite.
13. Filters (star, favorite-actor, FTS search). **Milestone: run a full day off the app.**

**Phase 2 — Rest of the surface**
14. Favorites table. 15. Links table (synced, watchlist_flag). 16. Actors table + manual gender/favorite.
17. Visualizations tab wired to live DB + weight controls.

**Phase 3 — Polish**
18. rank_override UX. 19. watchlist_flag pinning. 20. Backups/automation + restore. 21. recompute debouncing/perf.

---

## 15. Decisions (resolved)

1. **Weights** — rewatch (watched) = **actor 0.50 / content 0.35 / date 0.15**; unwatched drops
   date and renormalizes to **actor 0.59 / content 0.41**.
2. **Disliked performers** — a per-actor **`exclude_from_score`** flag. A flagged performer
   (e.g. a male you dislike) is **dropped from a video's actress-average**, so they can't
   penalize a co-star you like. Separate from the male/female `gender` label (which is just a
   manual attribute for now). If every performer on a video is excluded, fall back to the 75th pct.
3. **Favorite actor** — **auto** for now (any favorite/keep video makes the actor a favorite);
   manual cleanup later via the Actors table.
4. **Description in content** — measured the same way as tags: tokenize the description into
   **unigrams + bigrams**, strip actor names, stopwords, digits, and the video's own tag words
   (so it's a *residual*, no double-count), score each term with the volume-weighted total
   `Σ(1 + watches + content_bonus)`, average over the video's terms. Content =
   **0.5·percentile(tag signal) + 0.5·percentile(desc signal)** (equal weight). Ship a
   **top-description-terms** table alongside the top-tags table.
5. **Backups** — snapshot `library.db` before each write batch; keep the **last 20** snapshots
   plus **one daily rollup for 30 days**; prune older.

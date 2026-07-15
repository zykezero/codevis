"""Scoring engine: profile actress + tag/description content + linear date -> composites.

recompute(conn) runs a full pass and caches results onto videos / actors / tag_scores /
term_scores. Called whenever the link table changes.
"""
import re
import math
from collections import defaultdict
from datetime import date

from db import get_setting

CONTENT_STOPLIST = {'1080p', '720p', '4k', 'hd'}
ACTOR_EXCLUDE = {'big cock', 'anal'}          # content words that leaked into Stars
DESC_STOP = {
    'the', 'a', 'an', 'and', 'or', 'of', 'for', 'in', 'on', 'to', 'with', 'is', 'are',
    'be', 'by', 'at', 'as', 'this', 'that', 'from', 'her', 'his', 'she', 'he', 'you',
    'your', 'my', 'our', 'we', 'it', 'scene', 'part', 'vol', 'volume', 'ep', 'episode',
    'feat', 'porn', 'video', 'free', 'watch', 'hd', 'com', 'www', 'onlyfans',
    'has', 'have', 'had', 'get', 'gets', 'got', 'getting', 'who', 'can', 'will',
    'when', 'while', 'into', 'out', 'off', 'up', 'down', 'after', 'before',
    'they', 'them', 'all', 'not', 'but', 'was', 'were', 'been', 'new',
    'php', 'viewkey', 'html', 'http', 'https', 'www', 'com', 'net', 'aspx',
    'tube', 'xxx', '1080p', '720p', '4k', 'mp4', 'index',
    'than', 'better', 'only', 'knows', 'know', 'how', 'cant', 'wont', 'dont',
    'just', 'really', 'very', 'too', 'still', 'even', 'gonna', 'wanna', 'both',
    'some', 'any', 'much', 'many', 'well', 'back', 'over', 'about', 'now', 'then',
}

RATING = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 88: 4.0, 94: 4.0}          # 9 = unrated
GOOD_STATUS = {None, 3, 4, 9, 88, 94}                                # 9 now counts good
def content_bonus(st):
    return {4: 2.0, 88: 2.0, 94: 2.0, 3: 0.0, 2: -2.0, 1: -2.0}.get(st, 0.0)  # 9/NA/99 -> 0

_WORD = re.compile(r"[a-z0-9']+")


def percent_rank(values):
    """dplyr percent_rank: (min_rank-1)/(n-1). Returns list aligned to `values`."""
    n = len(values)
    if n <= 1:
        return [0.0] * n
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        p = i / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = p
        i = j + 1
    return out


def p75(sorted_vals):
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(int(0.75 * len(sorted_vals)), len(sorted_vals) - 1)]


def _split_tags(tags_raw, actor_vocab):
    if not tags_raw:
        return []
    out = []
    for t in str(tags_raw).split(','):
        tg = t.strip().lower()
        if tg in ('', 'na', '0') or tg in CONTENT_STOPLIST or tg in actor_vocab:
            continue
        out.append(tg)
    return list(dict.fromkeys(out))


def _desc_terms(desc, tag_words, actor_vocab, actor_words):
    if not desc:
        return []
    out, prev = [], None
    for w in _WORD.findall(desc.lower()):
        drop = (len(w) <= 2 or w.isdigit() or w in DESC_STOP or w in actor_vocab
                or w in actor_words or w in tag_words
                or (len(w) >= 8 and any(ch.isdigit() for ch in w))   # hash / URL id
                or len(w) > 18)
        if drop:
            prev = None
            continue
        out.append(w)
        if prev:
            out.append(prev + ' ' + w)   # bigram of consecutive kept words
        prev = w
    return list(dict.fromkeys(out))


def recompute(conn):
    method = get_setting(conn, 'content_method', 'tag', cast=str)
    if method == 'embed':
        try:
            import embed
            embed.score_embeddings(conn)      # refresh P(keep) from current labels (cheap, no model)
        except Exception:
            method = 'tag'                     # fall back if embeddings unavailable
    vids = [dict(r) for r in conn.execute("""
        SELECT id, status, favorite, location, watch_count, date_watched,
               tags_raw, description_raw, rank_override, content_embed_raw
        FROM videos
    """).fetchall()]
    va = conn.execute("SELECT video_id, actor_id FROM video_actors").fetchall()
    actors = {r['id']: dict(r) for r in conn.execute(
        "SELECT id, name, exclude_from_score FROM actors").fetchall()}

    vid_actor_ids = defaultdict(list)
    for r in va:
        vid_actor_ids[r['video_id']].append(r['actor_id'])
    actor_vids = defaultdict(list)
    for r in va:
        actor_vids[r['actor_id']].append(r['video_id'])

    vmap = {v['id']: v for v in vids}
    actor_vocab = {a['name'].lower() for a in actors.values()}
    actor_words = set()
    for nm in actor_vocab:
        actor_words.update(w for w in nm.split() if len(w) > 2)

    def wc0(v):
        return v['watch_count'] or 0

    # ---- actor PROFILE (over all videos, exclude legacy-broken 99) ----
    prof = {}
    for aid, vidlist in actor_vids.items():
        n_tot = n_good = n_watch = 0
        watch_sum = rating_sum = 0.0
        for vid in vidlist:
            v = vmap[vid]; st = v['status']
            if st == 99:
                continue
            n_tot += 1
            if st in GOOD_STATUS:
                n_good += 1
            if st in RATING:
                n_watch += 1
                watch_sum += wc0(v)
                rating_sum += RATING[st]
        if n_tot == 0 or n_watch == 0:
            prof[aid] = None
            continue
        good_pct = n_good / n_tot
        watched_pct = n_watch / n_tot
        avg_watch = watch_sum / n_watch
        avg_rating = rating_sum / n_watch
        prof[aid] = good_pct * avg_rating * avg_watch * (0.5 + 0.5 * watched_pct)
    prof_p75 = p75(sorted(v for v in prof.values() if v is not None))
    for aid in prof:
        if prof[aid] is None:
            prof[aid] = prof_p75

    # ---- tag totals & description-term totals (over all videos) ----
    tag_total = defaultdict(float); tag_rated = defaultdict(int); tag_nv = defaultdict(int)
    term_total = defaultdict(float); term_rated = defaultdict(int); term_nv = defaultdict(int)
    vid_tags, vid_terms = {}, {}
    for v in vids:
        st = v['status']; contrib = 1.0 + wc0(v) + content_bonus(st); rated = st is not None
        tags = _split_tags(v['tags_raw'], actor_vocab)
        tag_words = set(w for t in tags for w in t.split())
        terms = _desc_terms(v['description_raw'], tag_words, actor_vocab, actor_words)
        vid_tags[v['id']] = tags; vid_terms[v['id']] = terms
        for t in tags:
            tag_total[t] += contrib; tag_nv[t] += 1
            if rated: tag_rated[t] += 1
        for t in terms:
            term_total[t] += contrib; term_nv[t] += 1
            if rated: term_rated[t] += 1
    # unrated tag/term -> 75th percentile
    tp75 = p75(sorted(tag_total[t] for t in tag_total if tag_rated[t] > 0))
    for t in tag_total:
        if tag_rated[t] == 0: tag_total[t] = tp75
    mp75 = p75(sorted(term_total[t] for t in term_total if term_rated[t] > 0))
    for t in term_total:
        if term_rated[t] == 0: term_total[t] = mp75

    # ---- per-video raw signals ----
    for v in vids:
        vid = v['id']
        prof_vals = [prof[aid] for aid in vid_actor_ids.get(vid, [])
                     if not actors.get(aid, {}).get('exclude_from_score')]
        v_actress_raw = sum(prof_vals) / len(prof_vals) if prof_vals else None
        tags = vid_tags[vid]; terms = vid_terms[vid]
        v_tag_raw = sum(tag_total[t] for t in tags) / len(tags) if tags else None
        v_term_raw = sum(term_total[t] for t in terms) / len(terms) if terms else None
        v['_araw'], v['_traw'], v['_draw'] = v_actress_raw, v_tag_raw, v_term_raw

    # 75th-percentile fills for no-actor / no-tag / no-term videos (over watch set)
    watch = [v for v in vids if v['location'] == 'watch']
    a_fill = p75(sorted(v['_araw'] for v in watch if v['_araw'] is not None))
    t_fill = p75(sorted(v['_traw'] for v in watch if v['_traw'] is not None))
    d_fill = p75(sorted(v['_draw'] for v in watch if v['_draw'] is not None))
    for v in vids:
        if v['_araw'] is None: v['_araw'] = a_fill
        if v['_traw'] is None: v['_traw'] = t_fill
        if v['_draw'] is None: v['_draw'] = d_fill

    # ---- percentile-scale over the watch set + compose ----
    wa = get_setting(conn, 'w_actress', 0.50); wc = get_setting(conn, 'w_content', 0.35)
    wd = get_setting(conn, 'w_date', 0.15)
    wau = get_setting(conn, 'w_actress_unwatched', 0.59)
    wcu = get_setting(conn, 'w_content_unwatched', 0.41)
    ctw = get_setting(conn, 'content_tag_weight', 0.5)
    cdw = get_setting(conn, 'content_desc_weight', 0.5)

    a_pct = percent_rank([v['_araw'] for v in watch])
    t_pct = percent_rank([v['_traw'] for v in watch])
    d_pct = percent_rank([v['_draw'] for v in watch])
    if method == 'embed':
        er = [v.get('content_embed_raw') for v in watch]
        pres = sorted(x for x in er if x is not None)
        emed = pres[len(pres) // 2] if pres else 0.5
        e_pct = percent_rank([x if x is not None else emed for x in er])
    today = date.today()
    days = []
    for v in watch:
        if v['status'] is None or not v['date_watched']:
            days.append(None)
        else:
            try:
                y, m, d = map(int, str(v['date_watched'])[:10].split('-'))
                days.append((today - date(y, m, d)).days)
            except Exception:
                days.append(None)
    max_days = max([x for x in days if x is not None], default=1) or 1

    updates = []
    for i, v in enumerate(watch):
        actress = a_pct[i]
        content = e_pct[i] if method == 'embed' else ctw * t_pct[i] + cdw * d_pct[i]
        dt = 1.0 if v['status'] is None else min(1.0, (days[i] if days[i] is not None else max_days) / max_days)
        if v['rank_override'] is not None:
            su = sr = float(v['rank_override'])
        else:
            su = wau * actress + wcu * content
            sr = wa * actress + wc * content + wd * dt
        updates.append((v['_araw'], v['_traw'], v['_draw'], actress, content, dt, su, sr, v['id']))
    conn.executemany("""
        UPDATE videos SET actress_raw=?, content_tag_raw=?, content_desc_raw=?,
               actress=?, content=?, date=?, score_unwatched=?, score_rewatch=?,
               updated_at=datetime('now') WHERE id=?""", updates)
    # grave videos: store raw signals, clear composites
    grave_ids = [v['id'] for v in vids if v['location'] != 'watch']
    conn.executemany("""
        UPDATE videos SET actress_raw=?, content_tag_raw=?, content_desc_raw=?,
               actress=NULL, content=NULL, date=NULL, score_unwatched=NULL, score_rewatch=NULL
        WHERE id=?""",
        [(vmap[i]['_araw'], vmap[i]['_traw'], vmap[i]['_draw'], i) for i in grave_ids])

    # ---- actor stats + profile percentile (favorite is manual, not touched here) ----
    prof_pct_map = {}
    aids = list(prof.keys())
    pr = percent_rank([prof[a] for a in aids])
    for a, p in zip(aids, pr):
        prof_pct_map[a] = p
    astats = []
    for aid, a in actors.items():
        vl = actor_vids.get(aid, [])
        n = len(vl)
        nw = sum(1 for vid in vl if vmap[vid]['status'] in RATING)
        nu = sum(1 for vid in vl if vmap[vid]['status'] is None)
        tw = sum(wc0(vmap[vid]) for vid in vl if vmap[vid]['status'] in RATING)
        lw = max((vmap[vid]['date_watched'] for vid in vl if vmap[vid]['date_watched']), default=None)
        astats.append((
            prof.get(aid), prof_pct_map.get(aid), n, nw, nu, tw,
            (tw / nw if nw else 0.0), (nw / n if n else 0.0), lw, aid))
    conn.executemany("""
        UPDATE actors SET profile=?, profile_pct=?, n_videos=?, n_watched=?, n_unwatched=?,
               total_watches=?, avg_watches=?, pct_watched=?, last_watched=? WHERE id=?""", astats)

    # ---- tag / term leaderboards ----
    conn.execute("DELETE FROM tag_scores")
    conn.executemany("INSERT INTO tag_scores(tag,total,n_rated,n_videos) VALUES (?,?,?,?)",
                     [(t, tag_total[t], tag_rated[t], tag_nv[t]) for t in tag_total])
    conn.execute("DELETE FROM term_scores")
    conn.executemany("INSERT INTO term_scores(term,total,n_rated,n_videos) VALUES (?,?,?,?)",
                     [(t, term_total[t], term_rated[t], term_nv[t]) for t in term_total])
    conn.commit()
    return {'videos': len(vids), 'watch': len(watch), 'actors': len(actors),
            'tags': len(tag_total), 'terms': len(term_total)}

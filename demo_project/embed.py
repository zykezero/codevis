"""Semantic content analysis.

ensure_embeddings(): embed each video's `tags + description` with a small local
sentence model (all-MiniLM), cached in the `embeddings` table (only new/changed text
is re-embedded). score_embeddings(): train a logistic regression on your loved
(Status 4/88/94) vs graveyard (1/2) embeddings and store P(keep) as content_embed_raw.
Everything runs locally — nothing leaves the machine.
"""
import hashlib
import numpy as np

MODEL_NAME = 'all-MiniLM-L6-v2'
_model = None


def model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _text(tags, desc):
    return ((tags or '') + '. ' + (desc or '')).strip().lower() or 'untitled'


def ensure_embeddings(conn, batch=256, log=print):
    """Embed videos whose text is missing or changed. Returns count embedded."""
    rows = conn.execute("""
        SELECT v.id, v.tags_raw, v.description_raw, e.text_hash
        FROM videos v LEFT JOIN embeddings e ON e.video_id = v.id""").fetchall()
    todo = []
    for r in rows:
        txt = _text(r['tags_raw'], r['description_raw'])
        h = hashlib.md5(txt.encode()).hexdigest()
        if r['text_hash'] != h:
            todo.append((r['id'], txt, h))
    if not todo:
        return 0
    log(f'embedding {len(todo)} videos…')
    m = model()
    vecs = m.encode([t for _, t, _ in todo], batch_size=batch, normalize_embeddings=True,
                    show_progress_bar=False)
    conn.executemany("INSERT OR REPLACE INTO embeddings(video_id, vec, text_hash) VALUES (?,?,?)",
                     [(vid, np.asarray(v, dtype='float32').tobytes(), h)
                      for (vid, _, h), v in zip(todo, vecs)])
    conn.commit()
    return len(todo)


def _load(conn):
    rows = conn.execute("""
        SELECT v.id, v.status, e.vec FROM videos v JOIN embeddings e ON e.video_id = v.id
    """).fetchall()
    ids, X, status = [], [], []
    for r in rows:
        ids.append(r['id'])
        X.append(np.frombuffer(r['vec'], dtype='float32'))
        status.append(r['status'])
    return ids, np.vstack(X) if X else np.zeros((0, 384)), status


def score_embeddings(conn):
    """Train loved-vs-graveyard logistic regression on cached embeddings; store P(keep)."""
    from sklearn.linear_model import LogisticRegression
    ids, X, status = _load(conn)
    if len(ids) < 20:
        return 0
    POS = {4, 88, 94}
    NEG = {1, 2}
    y, keep = [], []
    for i, st in enumerate(status):
        if st in POS:
            y.append(1); keep.append(i)
        elif st in NEG:
            y.append(0); keep.append(i)
    if len(set(y)) < 2 or len(keep) < 20:
        return 0
    Xt = X[keep]
    clf = LogisticRegression(max_iter=1000, C=1.0, class_weight='balanced')
    clf.fit(Xt, y)
    proba = clf.predict_proba(X)[:, 1]
    conn.executemany("UPDATE videos SET content_embed_raw=? WHERE id=?",
                     [(float(p), vid) for vid, p in zip(ids, proba)])
    conn.commit()
    return len(ids)


def rebuild(conn, log=print):
    n = ensure_embeddings(conn, log=log)
    scored = score_embeddings(conn)
    log(f'embedded {n} new · scored {scored} videos')
    return {'embedded': n, 'scored': scored}

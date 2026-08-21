import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER NOT NULL,
    attachment_id INTEGER NOT NULL,
    filename      TEXT    NOT NULL,
    title         TEXT    NOT NULL DEFAULT '',
    note          TEXT    NOT NULL DEFAULT '',
    uploader_id   INTEGER NOT NULL,
    uploader_name TEXT    NOT NULL DEFAULT '',
    duration      REAL    NOT NULL DEFAULT 0,
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    recorded_at   TEXT,
    created_at    TEXT    NOT NULL,
    thumb_path    TEXT,
    reactions     INTEGER NOT NULL DEFAULT 0,
    transcript    TEXT    NOT NULL DEFAULT '',
    transcript_model TEXT NOT NULL DEFAULT '',
    segments      TEXT    NOT NULL DEFAULT '',
    thread_id     INTEGER,
    UNIQUE (message_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id    INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL DEFAULT 'transcribe',
    status     TEXT    NOT NULL DEFAULT 'pending',
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT    NOT NULL DEFAULT '',
    tier       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE (clip_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id    INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    user_name  TEXT    NOT NULL,
    at_seconds REAL,
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    thread_message_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_comments_clip ON comments(clip_id, id);

CREATE TABLE IF NOT EXISTS reactions (
    clip_id   INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    user_name TEXT    NOT NULL,
    emoji     TEXT    NOT NULL,
    created_at TEXT   NOT NULL,
    PRIMARY KEY (clip_id, user_id, emoji)
);

CREATE INDEX IF NOT EXISTS idx_reactions_clip ON reactions(clip_id);

CREATE TABLE IF NOT EXISTS favorites (
    clip_id    INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    PRIMARY KEY (clip_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS webhooks (
    channel_id INTEGER PRIMARY KEY,
    webhook_id INTEGER NOT NULL,
    token      TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS web_tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    user_name  TEXT    NOT NULL,
    guild_id   INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clips_message ON clips(message_id);

CREATE TABLE IF NOT EXISTS tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    display   TEXT NOT NULL,
    category  TEXT NOT NULL DEFAULT 'play',
    use_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tag_aliases (
    alias  TEXT PRIMARY KEY,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS clip_tags (
    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (clip_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_clip_tags_tag ON clip_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS clip_fts USING fts5(
    title, tags, note, transcript, tokenize='trigram'
);
"""


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
        _migrate(_conn)
    return _conn


def _needs_message_id_migration(c: sqlite3.Connection) -> bool:
    for idx in c.execute("PRAGMA index_list(clips)").fetchall():
        if not idx["unique"]:
            continue
        cols = [r["name"] for r in c.execute(f"PRAGMA index_info({idx['name']})")]
        if cols == ["message_id"]:
            return True
    return False


def _migrate(c: sqlite3.Connection) -> None:
    cols = [r["name"] for r in c.execute("PRAGMA table_info(clips)")]
    for name, ddl in (("segments", "TEXT NOT NULL DEFAULT ''"),
                      ("transcript_model", "TEXT NOT NULL DEFAULT ''"),
                      ("thread_id", "INTEGER")):
        if name not in cols:
            c.execute(f"ALTER TABLE clips ADD COLUMN {name} {ddl}")
    jcols = [r["name"] for r in c.execute("PRAGMA table_info(jobs)")]
    if jcols and "tier" not in jcols:
        c.execute("ALTER TABLE jobs ADD COLUMN tier TEXT NOT NULL DEFAULT ''")
    c.commit()

    if not _needs_message_id_migration(c):
        return
    cols = [r["name"] for r in c.execute("PRAGMA table_info(clips)")]
    names = ", ".join(cols)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("ALTER TABLE clips RENAME TO clips_old")
    c.executescript(SCHEMA)
    c.execute(f"INSERT INTO clips ({names}) SELECT {names} FROM clips_old")
    c.execute("DROP TABLE clips_old")
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()


def normalize_tag(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw).strip().lower()
    s = s.lstrip("#＃")
    s = re.sub(r"\s+", "", s)
    return s


def resolve_tag(name: str, category: str = "play", create: bool = True) -> Optional[int]:
    c = connect()
    key = normalize_tag(name)
    if not key:
        return None
    row = c.execute("SELECT tag_id FROM tag_aliases WHERE alias = ?", (key,)).fetchone()
    if row:
        return row["tag_id"]
    row = c.execute("SELECT id FROM tags WHERE name = ?", (key,)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    cur = c.execute(
        "INSERT INTO tags (name, display, category) VALUES (?, ?, ?)",
        (key, name.strip().lstrip("#＃"), category),
    )
    c.commit()
    return cur.lastrowid


def add_alias(alias: str, tag_name: str) -> None:
    c = connect()
    tag_id = resolve_tag(tag_name)
    c.execute(
        "INSERT OR REPLACE INTO tag_aliases (alias, tag_id) VALUES (?, ?)",
        (normalize_tag(alias), tag_id),
    )
    c.commit()


def set_tags(clip_id: int, names: list[str], category: str = "play") -> None:
    c = connect()
    old = [r["tag_id"] for r in c.execute(
        "SELECT tag_id FROM clip_tags WHERE clip_id = ?", (clip_id,))]
    for tid in old:
        c.execute("UPDATE tags SET use_count = MAX(use_count - 1, 0) WHERE id = ?", (tid,))
    c.execute("DELETE FROM clip_tags WHERE clip_id = ?", (clip_id,))
    seen = set()
    for n in names:
        tid = resolve_tag(n, category)
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        c.execute("INSERT INTO clip_tags (clip_id, tag_id) VALUES (?, ?)", (clip_id, tid))
        c.execute("UPDATE tags SET use_count = use_count + 1 WHERE id = ?", (tid,))
    c.commit()
    reindex(clip_id)


def add_tags(clip_id: int, names: list[str], category: str = "play") -> None:
    current = [t["display"] for t in get_tags(clip_id)]
    set_tags(clip_id, current + names, category)


def get_tags(clip_id: int) -> list[sqlite3.Row]:
    c = connect()
    return c.execute(
        "SELECT t.* FROM tags t JOIN clip_tags ct ON ct.tag_id = t.id "
        "WHERE ct.clip_id = ? ORDER BY t.category, t.name",
        (clip_id,),
    ).fetchall()


def delete_tag(tag_id: int) -> int:
    c = connect()
    clips = [r["clip_id"] for r in c.execute(
        "SELECT clip_id FROM clip_tags WHERE tag_id = ?", (tag_id,))]
    c.execute("DELETE FROM clip_tags WHERE tag_id = ?", (tag_id,))
    c.execute("DELETE FROM tag_aliases WHERE tag_id = ?", (tag_id,))
    c.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    c.commit()
    for cid in clips:
        reindex(cid)
    return len(clips)


def popular_tags(limit: int = 25) -> list[sqlite3.Row]:
    c = connect()
    return c.execute(
        "SELECT * FROM tags WHERE use_count > 0 ORDER BY use_count DESC, name LIMIT ?",
        (limit,),
    ).fetchall()


def reindex(clip_id: int) -> None:
    c = connect()
    row = c.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not row:
        return
    tags = " ".join(t["display"] for t in get_tags(clip_id))
    c.execute("DELETE FROM clip_fts WHERE rowid = ?", (clip_id,))
    c.execute(
        "INSERT INTO clip_fts (rowid, title, tags, note, transcript) VALUES (?, ?, ?, ?, ?)",
        (clip_id, row["title"] or row["filename"], tags, row["note"], row["transcript"]),
    )
    c.commit()


def insert_clip(**kw) -> Optional[int]:
    c = connect()
    cols = ", ".join(kw)
    marks = ", ".join("?" for _ in kw)
    try:
        cur = c.execute(f"INSERT INTO clips ({cols}) VALUES ({marks})", tuple(kw.values()))
        c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_clip(clip_id: int, **kw) -> None:
    if not kw:
        return
    c = connect()
    sets = ", ".join(f"{k} = ?" for k in kw)
    c.execute(f"UPDATE clips SET {sets} WHERE id = ?", (*kw.values(), clip_id))
    c.commit()
    reindex(clip_id)


def get_clip(clip_id: int) -> Optional[sqlite3.Row]:
    return connect().execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()


def clip_by_message(message_id: int) -> Optional[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM clips WHERE message_id = ?", (message_id,)).fetchone()


def clips_by_message(message_id: int) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM clips WHERE message_id = ?", (message_id,)).fetchall()


def clip_by_attachment(message_id: int, attachment_id: int) -> Optional[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM clips WHERE message_id = ? AND attachment_id = ?",
        (message_id, attachment_id)).fetchone()


def set_reactions(message_id: int, count: int) -> None:
    c = connect()
    c.execute("UPDATE clips SET reactions = ? WHERE message_id = ?", (count, message_id))
    c.commit()


def uploader_id_by_name(name: str) -> Optional[int]:
    row = connect().execute(
        "SELECT uploader_id FROM clips WHERE uploader_name = ? LIMIT 1", (name,)).fetchone()
    return row["uploader_id"] if row else None


def message_refs() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT DISTINCT channel_id, message_id FROM clips ORDER BY message_id").fetchall()


def enqueue(clip_id: int, kind: str = "transcribe", force: bool = False) -> bool:
    c = connect()
    now = datetime.now(timezone.utc).isoformat()
    if force:
        c.execute(
            "INSERT INTO jobs (clip_id, kind, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(clip_id, kind) DO UPDATE SET "
            "status='pending', attempts=0, error='', updated_at=excluded.updated_at",
            (clip_id, kind, now, now))
        c.commit()
        return True
    try:
        c.execute(
            "INSERT INTO jobs (clip_id, kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (clip_id, kind, now, now))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def claim_job(kind: str = "transcribe", min_age_seconds: int = 0,
              tier: str = "") -> Optional[sqlite3.Row]:
    c = connect()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=min_age_seconds)).isoformat()
    row = c.execute(
        "SELECT * FROM jobs WHERE kind = ? AND status = 'pending' AND updated_at <= ? "
        "ORDER BY id LIMIT 1", (kind, cutoff)).fetchone()
    if not row:
        return None
    cur = c.execute(
        "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=?, tier=? "
        "WHERE id = ? AND status='pending'", (now.isoformat(), tier, row["id"]))
    c.commit()
    if cur.rowcount == 0:
        return None
    return c.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def finish_job(job_id: int, status: str, error: str = "") -> None:
    c = connect()
    c.execute("UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
              (status, error[:500], datetime.now(timezone.utc).isoformat(), job_id))
    c.commit()


def requeue_stuck() -> int:
    c = connect()
    cur = c.execute(
        "UPDATE jobs SET status='pending' WHERE status='running' AND attempts < 3")
    c.commit()
    return cur.rowcount


def job_counts(kind: str = "transcribe") -> dict:
    rows = connect().execute(
        "SELECT status, COUNT(*) n FROM jobs WHERE kind = ? GROUP BY status", (kind,))
    return {r["status"]: r["n"] for r in rows}


def failed_jobs(kind: str = "transcribe", limit: int = 5) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT j.*, c.filename FROM jobs j JOIN clips c ON c.id = j.clip_id "
        "WHERE j.kind = ? AND j.status = 'failed' ORDER BY j.updated_at DESC LIMIT ?",
        (kind, limit)).fetchall()


def save_transcript(clip_id: int, text: str, segments: list[dict],
                    model: str = "") -> None:
    c = connect()
    c.execute(
        "UPDATE clips SET transcript = ?, segments = ?, transcript_model = ? WHERE id = ?",
        (text, json.dumps(segments, ensure_ascii=False), model, clip_id))
    c.commit()
    reindex(clip_id)


def get_segments(clip_id: int) -> list[dict]:
    row = get_clip(clip_id)
    if not row or not row["segments"]:
        return []
    try:
        return json.loads(row["segments"])
    except json.JSONDecodeError:
        return []


def game_tag(clip_id: int) -> str:
    for t in get_tags(clip_id):
        if t["category"] == "game":
            return t["display"]
    return ""


def low_quality(preferred: str, limit: int = 500) -> list[int]:
    rows = connect().execute(
        "SELECT id FROM clips WHERE transcript_model != '' AND transcript_model != ? "
        "ORDER BY created_at DESC LIMIT ?", (preferred, limit))
    return [r["id"] for r in rows]


def untranscribed(limit: int = 500) -> list[int]:
    rows = connect().execute(
        "SELECT id FROM clips WHERE transcript = '' ORDER BY created_at DESC LIMIT ?",
        (limit,))
    return [r["id"] for r in rows]


SORTS = {
    "new": ("c.created_at DESC", "新着順"),
    "old": ("c.created_at ASC", "古い順"),
    "hot": ("c.reactions DESC, c.created_at DESC", "人気順"),
    "long": ("c.duration DESC", "長い順"),
    "short": ("c.duration ASC", "短い順"),
}


def _fts_query(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def query_clips(
    query: str = "",
    tag_ids: Optional[list[int]] = None,
    uploader_id: Optional[int] = None,
    favorite_of: Optional[int] = None,
    sort: str = "new",
    offset: int = 0,
    limit: int = 9,
) -> tuple[list[sqlite3.Row], int]:
    c = connect()
    where, params = ["1=1"], []

    if query:
        q = query.strip()
        # コメントは頻繁に増減するので FTS には入れず、LIKE で OR 結合する。
        # インデックス再構築のコストを避けるため。
        comment_hit = "c.id IN (SELECT clip_id FROM comments WHERE body LIKE ?)"
        if len(q) >= 3:
            where.append(
                "(c.id IN (SELECT rowid FROM clip_fts WHERE clip_fts MATCH ?) "
                f"OR {comment_hit})")
            params += [_fts_query(q), f"%{q}%"]
        else:
            where.append(
                f"(c.title LIKE ? OR c.filename LIKE ? OR {comment_hit})")
            params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    if tag_ids:
        marks = ", ".join("?" for _ in tag_ids)
        where.append(
            f"c.id IN (SELECT clip_id FROM clip_tags WHERE tag_id IN ({marks}) "
            f"GROUP BY clip_id HAVING COUNT(DISTINCT tag_id) = ?)"
        )
        params += [*tag_ids, len(tag_ids)]

    if uploader_id:
        where.append("c.uploader_id = ?")
        params.append(uploader_id)

    if favorite_of:
        where.append("c.id IN (SELECT clip_id FROM favorites WHERE user_id = ?)")
        params.append(favorite_of)

    clause = " AND ".join(where)
    total = c.execute(f"SELECT COUNT(*) n FROM clips c WHERE {clause}", params).fetchone()["n"]
    order = SORTS.get(sort, SORTS["new"])[0]
    rows = c.execute(
        f"SELECT c.* FROM clips c WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return rows, total


# ---------- comments ----------

def add_comment(clip_id: int, user_id: int, user_name: str, body: str,
                at_seconds: Optional[float] = None) -> int:
    c = connect()
    cur = c.execute(
        "INSERT INTO comments (clip_id, user_id, user_name, at_seconds, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (clip_id, user_id, user_name, at_seconds, body.strip(),
         datetime.now(timezone.utc).isoformat()))
    c.commit()
    return cur.lastrowid


def list_comments(clip_id: int) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM comments WHERE clip_id = ? ORDER BY "
        "CASE WHEN at_seconds IS NULL THEN 1 ELSE 0 END, at_seconds, id",
        (clip_id,)).fetchall()


def delete_comment(comment_id: int, user_id: int) -> bool:
    c = connect()
    cur = c.execute("DELETE FROM comments WHERE id = ? AND user_id = ?",
                    (comment_id, user_id))
    c.commit()
    return cur.rowcount > 0


def comment_counts() -> dict[int, int]:
    return {r["clip_id"]: r["n"] for r in connect().execute(
        "SELECT clip_id, COUNT(*) n FROM comments GROUP BY clip_id")}


def link_comment(comment_id: int, message_id: int) -> None:
    c = connect()
    c.execute("UPDATE comments SET thread_message_id = ? WHERE id = ?",
              (message_id, comment_id))
    c.commit()


# ---------- reactions ----------

def toggle_reaction(clip_id: int, user_id: int, user_name: str, emoji: str) -> bool:
    """付いていれば外し、無ければ付ける。付けたら True。"""
    c = connect()
    cur = c.execute(
        "DELETE FROM reactions WHERE clip_id = ? AND user_id = ? AND emoji = ?",
        (clip_id, user_id, emoji))
    if cur.rowcount:
        c.commit()
        return False
    c.execute(
        "INSERT INTO reactions (clip_id, user_id, user_name, emoji, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (clip_id, user_id, user_name, emoji, datetime.now(timezone.utc).isoformat()))
    c.commit()
    return True


def clip_reactions(clip_id: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in connect().execute(
            "SELECT emoji, user_id, user_name FROM reactions WHERE clip_id = ? "
            "ORDER BY created_at", (clip_id,)):
        out.setdefault(r["emoji"], []).append(
            {"id": str(r["user_id"]), "name": r["user_name"]})
    return out


def reaction_counts() -> dict[int, int]:
    return {r["clip_id"]: r["n"] for r in connect().execute(
        "SELECT clip_id, COUNT(*) n FROM reactions GROUP BY clip_id")}


# ---------- favorites（個人ごと） ----------

def toggle_favorite(clip_id: int, user_id: int) -> bool:
    """付いていれば外し、無ければ付ける。付けたら True。"""
    c = connect()
    cur = c.execute("DELETE FROM favorites WHERE clip_id = ? AND user_id = ?",
                    (clip_id, user_id))
    if cur.rowcount:
        c.commit()
        return False
    c.execute("INSERT INTO favorites (clip_id, user_id, created_at) VALUES (?, ?, ?)",
              (clip_id, user_id, datetime.now(timezone.utc).isoformat()))
    c.commit()
    return True


def favorite_ids(user_id: int) -> set[int]:
    return {r["clip_id"] for r in connect().execute(
        "SELECT clip_id FROM favorites WHERE user_id = ?", (user_id,))}


def favorite_count(user_id: int) -> int:
    return connect().execute(
        "SELECT COUNT(*) n FROM favorites WHERE user_id = ?", (user_id,)).fetchone()["n"]


def is_favorite(clip_id: int, user_id: int) -> bool:
    return connect().execute(
        "SELECT 1 FROM favorites WHERE clip_id = ? AND user_id = ?",
        (clip_id, user_id)).fetchone() is not None


# ---------- discord 連携 ----------

def set_thread(clip_id: int, thread_id: int) -> None:
    c = connect()
    c.execute("UPDATE clips SET thread_id = ? WHERE id = ?", (thread_id, clip_id))
    c.commit()


def clip_by_thread(thread_id: int) -> Optional[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM clips WHERE thread_id = ?", (thread_id,)).fetchone()


def get_webhook(channel_id: int) -> Optional[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM webhooks WHERE channel_id = ?", (channel_id,)).fetchone()


def save_webhook(channel_id: int, webhook_id: int, token: str) -> None:
    c = connect()
    c.execute(
        "INSERT OR REPLACE INTO webhooks (channel_id, webhook_id, token, created_at) "
        "VALUES (?, ?, ?, ?)",
        (channel_id, webhook_id, token, datetime.now(timezone.utc).isoformat()))
    c.commit()


def drop_webhook(channel_id: int) -> None:
    c = connect()
    c.execute("DELETE FROM webhooks WHERE channel_id = ?", (channel_id,))
    c.commit()


def comment_by_message(message_id: int) -> Optional[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM comments WHERE thread_message_id = ?", (message_id,)).fetchone()


def delete_comment_by_message(message_id: int) -> bool:
    c = connect()
    cur = c.execute("DELETE FROM comments WHERE thread_message_id = ?", (message_id,))
    c.commit()
    return cur.rowcount > 0


# ---------- web tokens ----------

def issue_token(token: str, user_id: int, user_name: str, guild_id: int,
                days: int = 30) -> None:
    now = datetime.now(timezone.utc)
    c = connect()
    c.execute("DELETE FROM web_tokens WHERE user_id = ?", (user_id,))
    c.execute(
        "INSERT INTO web_tokens (token, user_id, user_name, guild_id, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token, user_id, user_name, guild_id, now.isoformat(),
         (now + timedelta(days=days)).isoformat()))
    c.commit()


def resolve_token(token: str) -> Optional[sqlite3.Row]:
    row = connect().execute(
        "SELECT * FROM web_tokens WHERE token = ?", (token,)).fetchone()
    if not row:
        return None
    if row["expires_at"] < datetime.now(timezone.utc).isoformat():
        return None
    return row


def stats() -> dict:
    c = connect()
    row = c.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(duration), 0) d, COALESCE(SUM(size_bytes), 0) s FROM clips"
    ).fetchone()
    return {"count": row["n"], "duration": row["d"], "size": row["s"]}

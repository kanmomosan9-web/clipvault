"""掲示板（スレッドと書き込み）のデータ。

Discord のフォーラムチャンネルとつなげる処理は forum.py。
ここは DB の読み書きだけを扱う。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import db

TITLE_MAX = 90
BODY_MAX = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _c() -> sqlite3.Connection:
    return db.connect()


# ---------------------------------------------------------------- スレッド

def create_thread(title: str, user_id: int, user_name: str,
                  discord_thread_id: Optional[int] = None) -> int:
    now = _now()
    cur = _c().execute(
        "INSERT INTO board_threads (title, creator_id, creator_name, created_at, updated_at, "
        "discord_thread_id) VALUES (?, ?, ?, ?, ?, ?)",
        (title.strip()[:TITLE_MAX] or "無題", user_id, user_name, now, now, discord_thread_id))
    _c().commit()
    return cur.lastrowid


def get_thread(thread_id: int) -> Optional[sqlite3.Row]:
    return _c().execute("SELECT * FROM board_threads WHERE id = ?", (thread_id,)).fetchone()


def thread_by_discord(discord_thread_id: int) -> Optional[sqlite3.Row]:
    return _c().execute("SELECT * FROM board_threads WHERE discord_thread_id = ?",
                        (discord_thread_id,)).fetchone()


def set_thread_discord(thread_id: int, discord_thread_id: int) -> None:
    _c().execute("UPDATE board_threads SET discord_thread_id = ? WHERE id = ?",
                 (discord_thread_id, thread_id))
    _c().commit()


def rename_thread(thread_id: int, title: str) -> None:
    _c().execute("UPDATE board_threads SET title = ? WHERE id = ?",
                 (title.strip()[:TITLE_MAX] or "無題", thread_id))
    _c().commit()


def delete_thread(thread_id: int) -> None:
    c = _c()
    c.execute("DELETE FROM board_posts WHERE thread_id = ?", (thread_id,))
    c.execute("DELETE FROM board_reads WHERE thread_id = ?", (thread_id,))
    c.execute("DELETE FROM board_threads WHERE id = ?", (thread_id,))
    c.commit()


def participants(thread_id: int) -> set[int]:
    c = _c()
    ids = {r["user_id"] for r in c.execute(
        "SELECT DISTINCT user_id FROM board_posts WHERE thread_id = ?", (thread_id,))}
    t = get_thread(thread_id)
    if t:
        ids.add(t["creator_id"])
    return ids


# ---------------------------------------------------------------- 書き込み

def add_post(thread_id: int, user_id: int, user_name: str, body: str = "",
             clip_id: Optional[int] = None, att: Optional[dict] = None,
             discord_message_id: Optional[int] = None) -> int:
    now = _now()
    att = att or {}
    c = _c()
    cur = c.execute(
        "INSERT INTO board_posts (thread_id, user_id, user_name, body, clip_id, "
        "att_channel_id, att_message_id, att_id, att_name, created_at, discord_message_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (thread_id, user_id, user_name, body.strip()[:BODY_MAX], clip_id,
         att.get("channel_id"), att.get("message_id"), att.get("id"), att.get("name"),
         now, discord_message_id))
    c.execute("UPDATE board_threads SET updated_at = ? WHERE id = ?", (now, thread_id))
    c.commit()
    # 自分の書き込みは既読扱い
    mark_read(user_id, thread_id, cur.lastrowid)
    return cur.lastrowid


def get_post(post_id: int) -> Optional[sqlite3.Row]:
    return _c().execute("SELECT * FROM board_posts WHERE id = ?", (post_id,)).fetchone()


def post_by_discord(message_id: int) -> Optional[sqlite3.Row]:
    return _c().execute(
        "SELECT * FROM board_posts WHERE discord_message_id = ? OR discord_forward_id = ?",
        (message_id, message_id)).fetchone()


def set_post_discord(post_id: int, message_id: Optional[int] = None,
                     forward_id: Optional[int] = None) -> None:
    c = _c()
    if message_id is not None:
        c.execute("UPDATE board_posts SET discord_message_id = ? WHERE id = ?",
                  (message_id, post_id))
    if forward_id is not None:
        c.execute("UPDATE board_posts SET discord_forward_id = ? WHERE id = ?",
                  (forward_id, post_id))
    c.commit()


def edit_post(post_id: int, body: str) -> None:
    _c().execute("UPDATE board_posts SET body = ?, edited_at = ? WHERE id = ?",
                 (body.strip()[:BODY_MAX], _now(), post_id))
    _c().commit()


def delete_post(post_id: int) -> None:
    _c().execute("DELETE FROM board_posts WHERE id = ?", (post_id,))
    _c().commit()


def clear_forward(post_id: int) -> None:
    _c().execute("UPDATE board_posts SET discord_forward_id = NULL WHERE id = ?", (post_id,))
    _c().commit()


def first_post(thread_id: int) -> Optional[sqlite3.Row]:
    return _c().execute("SELECT * FROM board_posts WHERE thread_id = ? ORDER BY id LIMIT 1",
                        (thread_id,)).fetchone()


def posts(thread_id: int, after: int = 0, limit: int = 300) -> list[sqlite3.Row]:
    return _c().execute(
        "SELECT * FROM board_posts WHERE thread_id = ? AND id > ? ORDER BY id LIMIT ?",
        (thread_id, after, limit)).fetchall()


# ---------------------------------------------------------------- 既読

def _baseline(user_id: int) -> int:
    """初めて掲示板を開く前の書き込みは未読として数えない（最初から大量の未読にしない）。"""
    c = _c()
    row = c.execute("SELECT last_post_id FROM board_reads WHERE user_id = ? AND thread_id = 0",
                    (user_id,)).fetchone()
    if row:
        return row["last_post_id"]
    top = c.execute("SELECT COALESCE(MAX(id), 0) m FROM board_posts").fetchone()["m"]
    c.execute("INSERT INTO board_reads (user_id, thread_id, last_post_id) VALUES (?, 0, ?)",
              (user_id, top))
    c.commit()
    return top


def mark_read(user_id: int, thread_id: int, upto: int) -> None:
    c = _c()
    c.execute(
        "INSERT INTO board_reads (user_id, thread_id, last_post_id) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, thread_id) DO UPDATE SET "
        "last_post_id = MAX(last_post_id, excluded.last_post_id)",
        (user_id, thread_id, upto))
    c.commit()


def _unread_map(user_id: int) -> dict[int, int]:
    base = _baseline(user_id)
    rows = _c().execute(
        "SELECT p.thread_id, COUNT(*) n FROM board_posts p "
        "LEFT JOIN board_reads r ON r.user_id = ? AND r.thread_id = p.thread_id "
        "WHERE p.user_id != ? AND p.id > MAX(COALESCE(r.last_post_id, 0), ?) "
        "GROUP BY p.thread_id", (user_id, user_id, base))
    return {r["thread_id"]: r["n"] for r in rows}


def unread_total(user_id: int) -> int:
    return sum(_unread_map(user_id).values())


# ---------------------------------------------------------------- 一覧

def _clip_title(clip) -> str:
    return clip["title"] or Path(clip["filename"]).stem


def preview_text(post) -> str:
    if post is None:
        return ""
    body = (post["body"] or "").replace("\n", " ").strip()
    if body:
        return body[:80]
    if post["clip_id"]:
        clip = db.get_clip(post["clip_id"])
        return "🎬 " + (_clip_title(clip) if clip else "クリップ")
    if post["att_id"]:
        return "🎬 動画"
    return ""


def list_threads(user_id: int, mode: str = "all", q: str = "",
                 limit: int = 100) -> list[dict]:
    c = _c()
    where, params = ["1=1"], []
    if mode == "mine":
        where.append("(t.creator_id = ? OR t.id IN "
                     "(SELECT thread_id FROM board_posts WHERE user_id = ?))")
        params += [user_id, user_id]
    elif mode == "clips":
        where.append("t.id IN (SELECT thread_id FROM board_posts "
                     "WHERE clip_id IS NOT NULL OR att_id IS NOT NULL)")
    if q:
        where.append("(t.title LIKE ? OR t.id IN "
                     "(SELECT thread_id FROM board_posts WHERE body LIKE ?))")
        params += [f"%{q}%", f"%{q}%"]
    rows = c.execute(
        f"SELECT t.* FROM board_threads t WHERE {' AND '.join(where)} "
        "ORDER BY t.updated_at DESC LIMIT ?", (*params, limit)).fetchall()
    unread = _unread_map(user_id)
    out = []
    for t in rows:
        stat = c.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN clip_id IS NOT NULL OR att_id IS NOT NULL "
            "THEN 1 ELSE 0 END) v FROM board_posts WHERE thread_id = ?", (t["id"],)).fetchone()
        last = c.execute("SELECT * FROM board_posts WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                         (t["id"],)).fetchone()
        cover = c.execute("SELECT clip_id FROM board_posts WHERE thread_id = ? "
                          "AND clip_id IS NOT NULL ORDER BY id LIMIT 1", (t["id"],)).fetchone()
        out.append({
            "id": t["id"], "title": t["title"],
            "creator": t["creator_name"], "creator_id": str(t["creator_id"]),
            "created_at": t["created_at"], "updated_at": t["updated_at"],
            "posts": stat["n"] or 0, "videos": stat["v"] or 0,
            "last": {"user": last["user_name"], "text": preview_text(last)} if last else None,
            "cover": cover["clip_id"] if cover else None,
            "unread": unread.get(t["id"], 0),
        })
    return out

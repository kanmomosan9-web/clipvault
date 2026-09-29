"""通知（🔔）と、希望者への Discord DM。

種類:
  comment  … 自分のクリップにコメントが付いた
  reply    … 自分が書き込んだ（立てた）スレッドに返信があった
  share    … 自分のクリップが掲示板に共有された
  reaction … 自分のクリップにリアクションが付いた（未読のうちは 1 件にまとめる）
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
import db

log = logging.getLogger("notify")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_title(clip_id: Optional[int]) -> str:
    clip = db.get_clip(clip_id) if clip_id else None
    if clip is None:
        return ""
    return clip["title"] or Path(clip["filename"]).stem


# ---------------------------------------------------------------- 設定

def dm_enabled(user_id: int) -> bool:
    row = db.connect().execute("SELECT dm_notify FROM user_settings WHERE user_id = ?",
                               (user_id,)).fetchone()
    return bool(row and row["dm_notify"])


def set_dm(user_id: int, on: bool) -> None:
    c = db.connect()
    c.execute("INSERT INTO user_settings (user_id, dm_notify) VALUES (?, ?) "
              "ON CONFLICT(user_id) DO UPDATE SET dm_notify = excluded.dm_notify",
              (user_id, 1 if on else 0))
    c.commit()


# ---------------------------------------------------------------- 追加

def add(user_id: Optional[int], kind: str, actor_id: Optional[int], actor_name: str, *,
        clip_id: Optional[int] = None, thread_id: Optional[int] = None,
        post_id: Optional[int] = None, comment_id: Optional[int] = None,
        body: str = "", at: Optional[float] = None) -> Optional[int]:
    """通知を 1 件追加する。自分の操作では通知しない。追加した id（まとめた場合は None）。"""
    if not user_id or user_id == actor_id:
        return None
    c = db.connect()
    if kind == "reaction":
        row = c.execute("SELECT id, actors FROM notifications WHERE user_id = ? AND kind = "
                        "'reaction' AND clip_id = ? AND read = 0", (user_id, clip_id)).fetchone()
        if row:
            names = [n for n in row["actors"].split("\n") if n]
            if actor_name not in names:
                names.append(actor_name)
            c.execute("UPDATE notifications SET actors = ?, actor_name = ?, actor_id = ?, "
                      "created_at = ? WHERE id = ?",
                      ("\n".join(names), actor_name, actor_id, _now(), row["id"]))
            c.commit()
            return None
    cur = c.execute(
        "INSERT INTO notifications (user_id, kind, actor_id, actor_name, actors, clip_id, "
        "thread_id, post_id, comment_id, body, at_seconds, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, kind, actor_id, actor_name, actor_name, clip_id, thread_id, post_id,
         comment_id, (body or "")[:300], at, _now()))
    c.commit()
    return cur.lastrowid


def unread_count(user_id: int) -> int:
    return db.connect().execute(
        "SELECT COUNT(*) n FROM notifications WHERE user_id = ? AND read = 0",
        (user_id,)).fetchone()["n"]


def mark_read(user_id: int, ids: Optional[list[int]] = None) -> None:
    c = db.connect()
    if ids:
        marks = ",".join("?" for _ in ids)
        c.execute(f"UPDATE notifications SET read = 1 WHERE user_id = ? AND id IN ({marks})",
                  (user_id, *ids))
    else:
        c.execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (user_id,))
    c.commit()


def listing(user_id: int, limit: int = 40) -> list[dict]:
    import board
    rows = db.connect().execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, limit)).fetchall()
    out = []
    for r in rows:
        actors = [n for n in r["actors"].split("\n") if n] or [r["actor_name"]]
        t = board.get_thread(r["thread_id"]) if r["thread_id"] else None
        out.append({
            "id": r["id"], "kind": r["kind"], "read": bool(r["read"]),
            "actor": r["actor_name"], "actors": actors,
            "clip_id": r["clip_id"], "clip_title": _clip_title(r["clip_id"]),
            "thread_id": r["thread_id"], "thread_title": t["title"] if t else "",
            "post_id": r["post_id"], "comment_id": r["comment_id"],
            "body": r["body"], "at": r["at_seconds"], "created_at": r["created_at"],
        })
    return out


# ---------------------------------------------------------------- DM

def text_for(n: dict) -> str:
    who = n["actor"]
    if n["kind"] == "comment":
        head = f"💬 {who} があなたのクリップ「{n['clip_title']}」にコメントしました"
    elif n["kind"] == "reply":
        head = f"💬 {who} が掲示板「{n['thread_title']}」に返信しました"
    elif n["kind"] == "share":
        head = f"📤 {who} があなたのクリップ「{n['clip_title']}」を掲示板「{n['thread_title']}」に共有しました"
    else:
        head = f"🔥 {who} があなたのクリップ「{n['clip_title']}」にリアクションしました"
    body = f"\n> {n['body'][:200]}" if n.get("body") else ""
    return head + body + (f"\n{link_for(n)}" if link_for(n) else "")


def link_for(n: dict) -> str:
    base = config.PUBLIC_URL
    if not base:
        return ""
    if n["kind"] in ("reply", "share") and n.get("thread_id"):
        return f"{base}/?board={n['thread_id']}"
    if n.get("clip_id"):
        return f"{base}/?clip={n['clip_id']}" + ("&tab=c" if n["kind"] == "comment" else "")
    return base


async def deliver(client, notif_id: Optional[int]) -> None:
    """DM を希望している人にだけ送る。失敗しても通知（🔔）は残る。"""
    if not notif_id or client is None:
        return
    row = db.connect().execute("SELECT * FROM notifications WHERE id = ?",
                               (notif_id,)).fetchone()
    if row is None or not dm_enabled(row["user_id"]):
        return
    item = next((x for x in listing(row["user_id"], 200) if x["id"] == notif_id), None)
    if item is None:
        return
    try:
        user = client.get_user(row["user_id"]) or await client.fetch_user(row["user_id"])
        await user.send(text_for(item))
    except Exception as e:
        log.info("DM を送れませんでした (%s): %s", row["user_id"], e)


def emit(client, *args, **kwargs) -> Optional[int]:
    """通知を追加し、DM は裏で送る。"""
    nid = add(*args, **kwargs)
    if nid and client is not None:
        try:
            asyncio.get_running_loop().create_task(deliver(client, nid))
        except RuntimeError:
            pass
    return nid


def clip_owner(clip_id: Optional[int]) -> Optional[int]:
    clip = db.get_clip(clip_id) if clip_id else None
    return clip["uploader_id"] if clip else None


def on_board_post(client, thread_id: int, post_id: int, actor_id: int, actor_name: str,
                  body: str, clip_id: Optional[int], before: set[int]) -> None:
    """掲示板に書き込まれたときの通知。before は書き込み前の参加者。"""
    owner = clip_owner(clip_id)
    if owner and owner != actor_id:
        emit(client, owner, "share", actor_id, actor_name, clip_id=clip_id,
             thread_id=thread_id, post_id=post_id, body=body)
    for uid in before:
        if uid in (actor_id, owner):
            continue
        emit(client, uid, "reply", actor_id, actor_name, thread_id=thread_id,
             post_id=post_id, clip_id=clip_id, body=body)

"""掲示板・通知・自動更新の Web API。"""
import asyncio
import logging
from pathlib import Path

from aiohttp import web

import board
import config
import db
import dc
import notify

log = logging.getLogger("board")


def _hook(client, name, *args):
    fn = getattr(client, name, None)
    if fn:
        asyncio.create_task(fn(*args))


def _clip_card(clip_id):
    clip = db.get_clip(clip_id) if clip_id else None
    if clip is None:
        return None
    game = next((t["display"] for t in db.get_tags(clip_id) if t["category"] == "game"), "")
    return {"id": clip["id"], "title": clip["title"] or Path(clip["filename"]).stem,
            "by": clip["uploader_name"], "duration": clip["duration"], "game": game,
            "stars": clip["reactions"], "thumb": f"/thumb/{clip['id']}",
            "media": f"/media/{clip['id']}"}


def _post_json(p) -> dict:
    return {
        "id": p["id"], "thread_id": p["thread_id"],
        "user": p["user_name"], "user_id": str(p["user_id"]),
        "body": p["body"], "created_at": p["created_at"], "edited_at": p["edited_at"],
        "clip": _clip_card(p["clip_id"]),
        "video": {"src": f"/board/media/{p['id']}", "name": p["att_name"]} if p["att_id"] else None,
        "discord": bool(p["discord_message_id"]),
    }


def _thread_json(t, user_id) -> dict:
    return {"id": t["id"], "title": t["title"], "creator": t["creator_name"],
            "creator_id": str(t["creator_id"]), "created_at": t["created_at"],
            "mine": t["creator_id"] == user_id,
            "participants": len(board.participants(t["id"]))}


def _clip_id(value):
    try:
        cid = int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return cid if cid and db.get_clip(cid) else None


def add_routes(app: web.Application, client, require_user, ui_version) -> None:
    r = app.router

    # ------------------------------------------------------------ 自動更新
    async def pulse(request):
        """未読数・画面の版・件数が変わったクリップをまとめて返す（定期的に呼ばれる）。"""
        uid = request["user"]["user_id"]
        try:
            since = int(request.query.get("since", "-1"))
        except ValueError:
            since = -1
        seq, ids = db.changes_since(max(since, 0))
        changes = db.clip_counts(ids) if since >= 0 else {}
        return web.json_response({
            "notifications": notify.unread_count(uid),
            "board": board.unread_total(uid),
            "v": ui_version(), "seq": seq,
            "changes": [{"id": k, **v} for k, v in changes.items()],
        }, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ 通知
    async def notifications(request):
        uid = request["user"]["user_id"]
        return web.json_response({"items": notify.listing(uid),
                                  "unread": notify.unread_count(uid),
                                  "dm": notify.dm_enabled(uid)})

    async def notifications_read(request):
        uid = request["user"]["user_id"]
        body = await request.json()
        ids = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
        notify.mark_read(uid, ids or None)
        return web.json_response({"unread": notify.unread_count(uid)})

    async def settings(request):
        uid = request["user"]["user_id"]
        body = await request.json()
        if "dm" in body:
            notify.set_dm(uid, bool(body["dm"]))
        return web.json_response({"dm": notify.dm_enabled(uid)})

    # ------------------------------------------------------------ 掲示板
    async def threads(request):
        u = request["user"]
        return web.json_response({"items": board.list_threads(
            u["user_id"], request.query.get("mode", "all"),
            request.query.get("q", "").strip()[:80])})

    async def create_thread(request):
        u = request["user"]
        body = await request.json()
        title = str(body.get("title", "")).strip()
        text = str(body.get("body", "")).strip()
        clip_id = _clip_id(body.get("clip_id"))
        if not title:
            return web.json_response({"error": "タイトルを入れてください"}, status=400)
        if not text and not clip_id:
            return web.json_response({"error": "最初の書き込みを入れてください"}, status=400)
        tid = board.create_thread(title, u["user_id"], u["user_name"])
        pid = board.add_post(tid, u["user_id"], u["user_name"], text, clip_id=clip_id)
        notify.on_board_post(client, tid, pid, u["user_id"], u["user_name"], text, clip_id,
                             set())
        _hook(client, "on_board_post", pid)
        return web.json_response({"id": tid, "post_id": pid})

    async def thread_detail(request):
        u = request["user"]
        tid = int(request.match_info["id"])
        t = board.get_thread(tid)
        if t is None:
            return web.json_response({"error": "not found"}, status=404)
        after = int(request.query.get("after", 0) or 0)
        rows = board.posts(tid, after)
        if rows:
            board.mark_read(u["user_id"], tid, rows[-1]["id"])
        return web.json_response({"thread": _thread_json(t, u["user_id"]),
                                  "posts": [_post_json(p) for p in rows]})

    async def edit_thread(request):
        u = request["user"]
        t = board.get_thread(int(request.match_info["id"]))
        if t is None:
            return web.json_response({"error": "not found"}, status=404)
        if t["creator_id"] != u["user_id"]:
            return web.json_response({"error": "スレッドを立てた人だけが変更できます"}, status=403)
        body = await request.json()
        title = str(body.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "タイトルを入れてください"}, status=400)
        board.rename_thread(t["id"], title)
        _hook(client, "on_board_rename", t["id"])
        return web.json_response({"title": board.get_thread(t["id"])["title"]})

    async def delete_thread(request):
        u = request["user"]
        t = board.get_thread(int(request.match_info["id"]))
        if t is None:
            return web.json_response({"error": "not found"}, status=404)
        if t["creator_id"] != u["user_id"]:
            return web.json_response({"error": "スレッドを立てた人だけが削除できます"}, status=403)
        did = t["discord_thread_id"]
        board.delete_thread(t["id"])
        _hook(client, "on_board_thread_delete", did)
        return web.json_response({"ok": True})

    async def add_post(request):
        u = request["user"]
        tid = int(request.match_info["id"])
        if board.get_thread(tid) is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await request.json()
        text = str(body.get("body", "")).strip()
        clip_id = _clip_id(body.get("clip_id"))
        if not text and not clip_id:
            return web.json_response({"error": "empty"}, status=400)
        before = board.participants(tid)
        pid = board.add_post(tid, u["user_id"], u["user_name"], text, clip_id=clip_id)
        notify.on_board_post(client, tid, pid, u["user_id"], u["user_name"], text, clip_id,
                             before)
        _hook(client, "on_board_post", pid)
        return web.json_response({"post": _post_json(board.get_post(pid))})

    async def edit_post(request):
        u = request["user"]
        p = board.get_post(int(request.match_info["id"]))
        if p is None:
            return web.json_response({"error": "not found"}, status=404)
        if p["user_id"] != u["user_id"]:
            return web.json_response({"error": "自分の書き込みだけ編集できます"}, status=403)
        body = await request.json()
        text = str(body.get("body", "")).strip()
        if not text and not p["clip_id"] and not p["att_id"]:
            return web.json_response({"error": "empty"}, status=400)
        board.edit_post(p["id"], text)
        _hook(client, "on_board_edit", p["id"])
        return web.json_response({"post": _post_json(board.get_post(p["id"]))})

    async def delete_post(request):
        u = request["user"]
        p = board.get_post(int(request.match_info["id"]))
        if p is None:
            return web.json_response({"error": "not found"}, status=404)
        if p["user_id"] != u["user_id"]:
            return web.json_response({"error": "自分の書き込みだけ削除できます"}, status=403)
        t = board.get_thread(p["thread_id"])
        board.delete_post(p["id"])
        _hook(client, "on_board_delete", dict(p), dict(t) if t else {})
        return web.json_response({"ok": True})

    async def board_media(request):
        """Discord で直接アップロードされた動画。期限付きURLを取り直して転送する。"""
        p = board.get_post(int(request.match_info["id"]))
        if p is None or not p["att_id"]:
            return web.Response(status=404)
        ref = {"channel_id": p["att_channel_id"], "message_id": p["att_message_id"],
               "attachment_id": p["att_id"]}
        url = await dc.fresh_attachment_url(client, ref)
        if not url:
            return web.Response(status=410)
        return web.HTTPFound(url)

    r.add_get("/api/pulse", require_user(pulse))
    r.add_get("/api/notifications", require_user(notifications))
    r.add_post("/api/notifications/read", require_user(notifications_read))
    r.add_post("/api/settings", require_user(settings))
    r.add_get("/api/board/threads", require_user(threads))
    r.add_post("/api/board/threads", require_user(create_thread))
    r.add_get("/api/board/threads/{id}", require_user(thread_detail))
    r.add_patch("/api/board/threads/{id}", require_user(edit_thread))
    r.add_delete("/api/board/threads/{id}", require_user(delete_thread))
    r.add_post("/api/board/threads/{id}/posts", require_user(add_post))
    r.add_patch("/api/board/posts/{id}", require_user(edit_post))
    r.add_delete("/api/board/posts/{id}", require_user(delete_post))
    r.add_get("/board/media/{id}", require_user(board_media))

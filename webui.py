import asyncio
import logging
import aiohttp
import secrets
from pathlib import Path
from aiohttp import web

import config
import db
import dc

log = logging.getLogger("web")

STATIC = Path(__file__).parent / "web"
COOKIE = "cv_token"


# ---------------------------------------------------------------- auth

def _token_from(request: web.Request) -> str:
    return (request.query.get("t")
            or request.cookies.get(COOKIE)
            or request.headers.get("X-ClipVault-Token", ""))


def _user(request: web.Request):
    tok = _token_from(request)
    return db.resolve_token(tok) if tok else None


def require_user(handler):
    async def wrapped(request: web.Request):
        user = _user(request)
        if user is None:
            log.warning("401 %s %s (token=%s)", request.method, request.path,
                        "あり" if _token_from(request) else "なし")
            return web.json_response(
                {"error": "unauthorized",
                 "detail": "Discord で /library を実行してリンクを取得してください。"},
                status=401)
        request["user"] = user
        return await handler(request)
    return wrapped


def new_token() -> str:
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------- shaping

def _clip_brief(row, tags, reacts, comments, faved=False) -> dict:
    return {
        "id": row["id"],
        "title": row["title"] or Path(row["filename"]).stem,
        "filename": row["filename"],
        "game": next((t["display"] for t in tags if t["category"] == "game"), ""),
        "tags": [t["display"] for t in tags if t["category"] != "game"],
        "by": row["uploader_name"],
        "by_id": str(row["uploader_id"]),
        "duration": row["duration"],
        "width": row["width"],
        "height": row["height"],
        "size": row["size_bytes"],
        "date": (row["recorded_at"] or row["created_at"] or "")[:10],
        "stars": row["reactions"] + reacts,
        "comments": comments,
        "has_transcript": bool(row["transcript"]),
        "favorited": faved,
        "thumb": f"/thumb/{row['id']}",
        "media": f"/media/{row['id']}",
        "jump": (f"https://discord.com/channels/{row['guild_id']}"
                 f"/{row['channel_id']}/{row['message_id']}"),
    }


async def exchange_code(code: str) -> dict:
    """Activity から渡された認可コードをアクセストークンに交換する。

    client_secret を使うのでブラウザ側では絶対にやらない。必ずサーバー経由。
    """
    data = {"client_id": config.CLIENT_ID, "client_secret": config.CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code}
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://discord.com/api/oauth2/token", data=data) as r:
            if r.status != 200:
                raise RuntimeError(f"token exchange failed: {r.status} {await r.text()}")
            tok = await r.json()
        async with sess.get("https://discord.com/api/users/@me",
                            headers={"Authorization": f"Bearer {tok['access_token']}"}) as r:
            if r.status != 200:
                raise RuntimeError(f"user fetch failed: {r.status}")
            user = await r.json()
    return {"access_token": tok["access_token"], "user": user}


def add_routes(app: web.Application, client) -> None:

    # ------------------------------------------------------------ session
    async def me(request):
        u = request["user"]
        base = config.PUBLIC_URL
        tok = _token_from(request)
        return web.json_response({
            "id": str(u["user_id"]), "name": u["user_name"],
            # 再生できない環境から外部ブラウザへ逃がすためのURL
            "browser_url": f"{base}/?t={tok}" if base and tok else "",
        })

    # ------------------------------------------------------------ listing
    async def clips(request):
        q = request.query.get("q", "").strip()
        sort = request.query.get("sort", "new")
        limit = min(int(request.query.get("limit", 60)), 200)
        offset = int(request.query.get("offset", 0))

        tag_ids = []
        for name in request.query.getall("tag", []):
            tid = db.resolve_tag(name, create=False)
            if tid:
                tag_ids.append(tid)
        game = request.query.get("game", "")
        if game:
            tid = db.resolve_tag(game, create=False)
            if tid:
                tag_ids.append(tid)
        me_id = request["user"]["user_id"]
        only_fav = request.query.get("fav") == "1"
        by = request.query.get("by", "")
        uploader_id = db.uploader_id_by_name(by) if by else None
        if by and uploader_id is None:
            return web.json_response({"total": 0, "items": []})

        rows, total = db.query_clips(query=q, tag_ids=tag_ids,
                                     uploader_id=uploader_id,
                                     favorite_of=me_id if only_fav else None,
                                     sort=sort, offset=offset, limit=limit)

        rcount, ccount = db.reaction_counts(), db.comment_counts()
        favs = db.favorite_ids(me_id)
        items = []
        for r in rows:
            tags = db.get_tags(r["id"])
            item = _clip_brief(r, tags, rcount.get(r["id"], 0),
                               ccount.get(r["id"], 0), r["id"] in favs)
            segs = db.get_segments(r["id"])
            item["preview"] = None
            if q and segs:
                hit = next((s for s in segs if q in s["text"]), None)
                if hit:
                    item["preview"] = {"t": hit["start"], "text": hit["text"]}
            if item["preview"] is None and segs:
                item["preview"] = {"t": segs[0]["start"], "text": segs[0]["text"]}
            items.append(item)
        return web.json_response({"total": total, "offset": offset,
                                  "has_more": offset + len(items) < total,
                                  "items": items})

    async def clip_detail(request):
        cid = int(request.match_info["id"])
        row = db.get_clip(cid)
        if row is None:
            return web.json_response({"error": "not found"}, status=404)
        tags = db.get_tags(cid)
        data = _clip_brief(row, tags, len(db.clip_reactions(cid)), 0,
                           db.is_favorite(cid, request["user"]["user_id"]))
        data["note"] = row["note"]
        fresh = await dc.fresh_attachment_url(client, row)
        data["cdn"] = fresh or ""
        data["model"] = row["transcript_model"]
        data["segments"] = db.get_segments(cid)
        data["reactions"] = db.clip_reactions(cid)
        data["discord_stars"] = row["reactions"]
        data["comments"] = [
            {"id": c["id"], "user": c["user_name"], "user_id": str(c["user_id"]),
             "at": c["at_seconds"], "body": c["body"], "created_at": c["created_at"]}
            for c in db.list_comments(cid)]
        data["stars"] = row["reactions"] + sum(
            len(v) for v in data["reactions"].values())
        return web.json_response(data)

    async def facets(request):
        rows = db.connect().execute(
            "SELECT display, category, use_count FROM tags WHERE use_count > 0 "
            "ORDER BY use_count DESC")
        games, tags = [], []
        for r in rows:
            (games if r["category"] == "game" else tags).append(
                {"name": r["display"], "count": r["use_count"]})
        people = [{"name": r["uploader_name"], "count": r["n"]} for r in
                  db.connect().execute(
                      "SELECT uploader_name, COUNT(*) n FROM clips "
                      "GROUP BY uploader_name ORDER BY n DESC")]
        return web.json_response({"games": games, "tags": tags, "members": people,
                                  "favorites": db.favorite_count(
                                      request["user"]["user_id"])})

    # ------------------------------------------------------------ mutations
    async def set_tags(request):
        cid = int(request.match_info["id"])
        if db.get_clip(cid) is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await request.json()
        names = [str(t).strip() for t in body.get("tags", []) if str(t).strip()][:20]
        game = next((t["display"] for t in db.get_tags(cid)
                     if t["category"] == "game"), None)
        db.set_tags(cid, names)
        if game:
            db.add_tags(cid, [game], category="game")
        return web.json_response({"tags": [t["display"] for t in db.get_tags(cid)]})

    async def patch_clip(request):
        cid = int(request.match_info["id"])
        if db.get_clip(cid) is None:
            return web.json_response({"error": "not found"}, status=404)
        body = await request.json()
        fields = {k: str(v)[:400] for k, v in body.items() if k in ("title", "note")}
        if fields:
            db.update_clip(cid, **fields)
        return web.json_response({"ok": True})

    async def post_comment(request):
        cid = int(request.match_info["id"])
        if db.get_clip(cid) is None:
            return web.json_response({"error": "not found"}, status=404)
        u = request["user"]
        body = await request.json()
        text = str(body.get("body", "")).strip()[:1000]
        if not text:
            return web.json_response({"error": "empty"}, status=400)
        at = body.get("at")
        at = float(at) if at is not None else None
        comment_id = db.add_comment(cid, u["user_id"], u["user_name"], text, at)
        hook = getattr(client, "on_web_comment", None)
        if hook:
            asyncio.create_task(
                hook(cid, comment_id, u["user_id"], u["user_name"], text, at))
        return web.json_response({"id": comment_id})

    async def delete_comment(request):
        u = request["user"]
        ok = db.delete_comment(int(request.match_info["id"]), u["user_id"])
        return web.json_response({"ok": ok}, status=200 if ok else 403)

    async def react(request):
        cid = int(request.match_info["id"])
        if db.get_clip(cid) is None:
            return web.json_response({"error": "not found"}, status=404)
        u = request["user"]
        body = await request.json()
        emoji = str(body.get("emoji", ""))[:8]
        if not emoji:
            return web.json_response({"error": "empty"}, status=400)
        added = db.toggle_reaction(cid, u["user_id"], u["user_name"], emoji)
        hook = getattr(client, "on_web_reaction", None)
        if hook:
            asyncio.create_task(hook(cid, emoji, added))
        return web.json_response({"added": added, "reactions": db.clip_reactions(cid)})

    async def client_log(request):
        """画面側の診断結果を受け取ってサーバーのログに出す。

        デスクトップ版 Discord は開発者ツールを開けないため、
        journalctl で読めるようにする。
        """
        u = request["user"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False}, status=400)
        tag = str(body.get("tag", "?"))[:40]
        log.warning("[client] %s / %s / %s", u["user_name"], tag,
                    str(body.get("detail", ""))[:1200])
        return web.json_response({"ok": True})

    # ------------------------------------------------------------ activity
    async def activity_token(request):
        if not (config.CLIENT_ID and config.CLIENT_SECRET):
            return web.json_response(
                {"error": "CLIENT_ID / CLIENT_SECRET が未設定です"}, status=503)
        body = await request.json()
        code = str(body.get("code", ""))
        if not code:
            return web.json_response({"error": "code required"}, status=400)
        try:
            got = await exchange_code(code)
        except Exception as e:
            log.error("activity auth failed: %s", e)
            return web.json_response({"error": "auth failed"}, status=401)

        u = got["user"]
        name = u.get("global_name") or u.get("username") or "unknown"
        tok = new_token()
        db.issue_token(tok, int(u["id"]), name, 0, days=config.WEB_TOKEN_DAYS)
        log.info("activity login: %s (%s)", name, u["id"])
        return web.json_response({"access_token": got["access_token"],
                                  "cv_token": tok, "name": name})

    async def favorite(request):
        cid = int(request.match_info["id"])
        if db.get_clip(cid) is None:
            return web.json_response({"error": "not found"}, status=404)
        me_id = request["user"]["user_id"]
        added = db.toggle_favorite(cid, me_id)
        return web.json_response({"favorited": added,
                                  "count": db.favorite_count(me_id)})

    # ------------------------------------------------------------ media
    async def media(request):
        cid = int(request.match_info["id"])
        clip = db.get_clip(cid)
        if clip is None:
            return web.Response(status=404)
        url = await dc.fresh_attachment_url(client, clip)
        if not url:
            return web.Response(status=410, text="attachment unavailable")

        if request.query.get("stream") != "1":
            # 既定は CDN へ 302。VM の帯域を使わない。
            return web.HTTPFound(url)

        # Activity 内は CSP により外部オリジンを読めないため、
        # VM が中継して同一オリジンとして返す。シークのため Range を通す。
        up_headers = {}
        if "Range" in request.headers:
            up_headers["Range"] = request.headers["Range"]

        session = aiohttp.ClientSession()
        try:
            up = await session.get(url, headers=up_headers)
        except Exception as e:
            await session.close()
            log.warning("media proxy failed: %s", e)
            return web.Response(status=502)

        if up.status >= 400:
            up.release()
            await session.close()
            return web.Response(status=up.status)

        passthrough = ("Content-Type", "Content-Length", "Content-Range",
                       "Accept-Ranges")
        resp = web.StreamResponse(
            status=up.status,
            headers={k: v for k, v in up.headers.items() if k in passthrough})
        resp.headers.setdefault("Accept-Ranges", "bytes")
        resp.headers["Cache-Control"] = "private, max-age=300"
        try:
            await resp.prepare(request)
            async for chunk in up.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass                      # 途中でシークされた/閉じられた
        finally:
            up.release()
            await session.close()
        return resp

    async def thumb(request):
        cid = int(request.match_info["id"])
        clip = db.get_clip(cid)
        if clip is None or not clip["thumb_path"]:
            return web.Response(status=404)
        path = Path(clip["thumb_path"])
        if not path.exists():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    # ------------------------------------------------------------ shell
    async def index(request):
        html = STATIC / "index.html"
        if not html.exists():
            return web.Response(text="web/index.html が見つかりません", status=500)
        # プレースホルダは変数名と衝突しない形にする。
        # 以前 __CLIENT_ID__ を使っていて window.__CLIENT_ID__ まで
        # 置換され、スクリプト全体が構文エラーになる事故があった。
        body = (html.read_text(encoding="utf-8")
                .replace("%%CLIENT_ID%%", config.CLIENT_ID)
                .replace("%%CDN_PREFIX%%", config.CDN_PROXY_PREFIX))
        resp = web.Response(text=body, content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
        tok = request.query.get("t")
        if tok and db.resolve_token(tok):
            resp.set_cookie(COOKIE, tok, max_age=60 * 60 * 24 * 30,
                            httponly=True, samesite="Lax")
        return resp

    @web.middleware
    async def access_log(request, handler):
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            if e.status >= 400:
                log.info("%s %s -> %s", request.method, request.path, e.status)
            raise
        if resp.status >= 400 or request.path.startswith("/.proxy"):
            log.info("%s %s -> %s", request.method, request.path, resp.status)
        return resp

    app.middlewares.append(access_log)

    r = app.router
    r.add_get("/", index)
    r.add_get("/app", index)
    r.add_post("/api/token", activity_token)
    r.add_get("/api/me", require_user(me))
    r.add_get("/api/clips", require_user(clips))
    r.add_get("/api/clips/{id}", require_user(clip_detail))
    r.add_get("/api/facets", require_user(facets))
    r.add_put("/api/clips/{id}/tags", require_user(set_tags))
    r.add_patch("/api/clips/{id}", require_user(patch_clip))
    r.add_post("/api/clips/{id}/comments", require_user(post_comment))
    r.add_delete("/api/comments/{id}", require_user(delete_comment))
    r.add_post("/api/clips/{id}/reactions", require_user(react))
    r.add_post("/api/clips/{id}/favorite", require_user(favorite))
    r.add_post("/api/log", require_user(client_log))
    r.add_get("/media/{id}", require_user(media))
    r.add_get("/thumb/{id}", require_user(thumb))
    if (STATIC / "assets").is_dir():
        r.add_static("/assets", STATIC / "assets")

    # Activity 内からのリクエストは Discord のプロキシが /.proxy を剥がさずに
    # 転送してくる構成もあるため、同じハンドラを両方のパスに生やしておく。
    for route in list(app.router.routes()):
        info = route.resource.canonical
        if info.startswith("/.proxy") or info == "/":
            continue
        try:
            app.router.add_route(route.method, "/.proxy" + info, route.handler)
        except Exception:
            pass
    app.router.add_get("/.proxy/", index)
    app.router.add_get("/.proxy", index)

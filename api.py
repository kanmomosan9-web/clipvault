import hmac
import logging

from aiohttp import web

import config
import db
import dc

log = logging.getLogger("api")


def _authorized(request: web.Request) -> bool:
    if not config.WORKER_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], config.WORKER_TOKEN)


def build_app(client) -> web.Application:
    app = web.Application()

    async def health(request: web.Request):
        counts = db.job_counts()
        return web.json_response({
            "status": "ok",
            "bot": str(client.user) if client.user else None,
            "clips": db.stats()["count"],
            "queue": counts,
        })

    async def claim(request: web.Request):
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json() if request.can_read_body else {}
        tier = str(body.get("tier", "remote"))[:16]
        min_age = int(body.get("min_age", 0))
        allow_upgrade = bool(body.get("allow_upgrade", False))

        job = db.claim_job(min_age_seconds=min_age, tier=tier)

        if job is None and allow_upgrade:
            for cid in db.low_quality(config.PREFERRED_MODEL, limit=1):
                db.enqueue(cid, force=True)
                job = db.claim_job(tier=tier)
                break

        if job is None:
            return web.json_response({"job": None})

        clip = db.get_clip(job["clip_id"])
        if clip is None:
            db.finish_job(job["id"], "failed", "clip not found")
            return web.json_response({"job": None})

        url = await dc.fresh_attachment_url(client, clip)
        if not url:
            db.finish_job(job["id"], "failed", "could not refresh attachment URL")
            return web.json_response({"job": None})

        game = db.game_tag(clip["id"])
        prompt = config.BASE_PROMPT + config.GAME_PROMPTS.get(game, "")
        log.info("job %s handed to %s (clip %s)", job["id"], tier, clip["id"])
        return web.json_response({"job": {
            "job_id": job["id"],
            "clip_id": clip["id"],
            "filename": clip["filename"],
            "duration": clip["duration"],
            "game": game,
            "prompt": prompt,
            "language": config.WHISPER_LANG,
            "url": url,
        }})

    async def result(request: web.Request):
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        job_id = int(body["job_id"])
        status = str(body.get("status", "done"))

        if status == "done":
            db.save_transcript(int(body["clip_id"]), body.get("text", ""),
                               body.get("segments", []), str(body.get("model", "")))
            if not body.get("text"):
                status = "empty"
        db.finish_job(job_id, status, str(body.get("error", "")))
        log.info("job %s reported %s by %s", job_id, status, body.get("model", "?"))
        return web.json_response({"ok": True})

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/api/jobs/claim", claim)
    app.router.add_post("/api/jobs/result", result)
    return app


async def start(client) -> web.AppRunner:
    runner = web.AppRunner(build_app(client))
    await runner.setup()
    site = web.TCPSite(runner, config.API_HOST, config.API_PORT)
    await site.start()
    log.info("job API listening on %s:%s", config.API_HOST, config.API_PORT)
    if not config.WORKER_TOKEN:
        log.warning("WORKER_TOKEN が未設定です - リモートワーカーは接続できません")
    return runner

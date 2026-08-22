"""動画の中継経路を、実際の上流サーバーを立てて検証する。

Activity 内は CSP により外部オリジンを読めないため、CDN プロキシが
使えない場合は VM が中継する。シークのため Range を通す必要がある。
"""
import asyncio
import os

os.environ.setdefault("WORKER_TOKEN", "t")
os.environ.setdefault("WEB_ENABLED", "1")

from aiohttp import web as aw

import config
import db
import selftest

config.WORKER_TOKEN = "t"
BODY = bytes(range(256)) * 400          # 102400 bytes


async def upstream(request):
    rng = request.headers.get("Range")
    if rng:
        a_, b_ = rng.replace("bytes=", "").split("-")
        a = int(a_)
        b = int(b_) if b_ else len(BODY) - 1
        return aw.Response(
            body=BODY[a:b + 1], status=206,
            headers={"Content-Range": f"bytes {a}-{b}/{len(BODY)}",
                     "Accept-Ranges": "bytes", "Content-Type": "video/mp4"})
    return aw.Response(body=BODY,
                       headers={"Content-Type": "video/mp4",
                                "Accept-Ranges": "bytes"})


class FakeAttachment:
    def __init__(self, aid):
        self.id = aid
        self.url = "http://127.0.0.1:18999/v.mp4"


class FakeMessage:
    def __init__(self, aid):
        self.attachments = [FakeAttachment(aid)]


class FakeChannel:
    async def fetch_message(self, mid):
        return FakeMessage(db.clip_by_message(mid)["attachment_id"])


class FakeClient:
    user = "bot"

    def get_channel(self, cid):
        return FakeChannel()


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()

    app = aw.Application()
    app.router.add_get("/v.mp4", upstream)
    runner = aw.AppRunner(app)
    await runner.setup()
    await aw.TCPSite(runner, "127.0.0.1", 18999).start()

    import api
    import webui
    from aiohttp.test_utils import TestClient, TestServer

    tok = webui.new_token()
    db.issue_token(tok, 42, "つるかん", 1)
    H = {"X-ClipVault-Token": tok}

    async with TestClient(TestServer(api.build_app(FakeClient()))) as cli:
        r = await cli.get("/media/1", headers=H, allow_redirects=False)
        print("既定（302 で CDN へ）  ->", r.status)
        assert r.status == 302

        r = await cli.get("/media/1?stream=1", headers=H)
        body = await r.read()
        print("中継（全体）           ->", r.status, len(body), "bytes",
              "| type:", r.headers.get("Content-Type"),
              "| ranges:", r.headers.get("Accept-Ranges"))
        assert r.status == 200 and body == BODY

        r = await cli.get("/media/1?stream=1",
                          headers={**H, "Range": "bytes=1000-1999"})
        body = await r.read()
        print("中継（Range）          ->", r.status, len(body), "bytes",
              "| Content-Range:", r.headers.get("Content-Range"))
        assert r.status == 206 and body == BODY[1000:2000], "シークが壊れている"

        r = await cli.get("/media/1?stream=1")
        print("認証なし               ->", r.status, "(401 が正しい)")
        assert r.status == 401

    await runner.cleanup()
    print("\nMEDIA OK")


if __name__ == "__main__":
    asyncio.run(main())

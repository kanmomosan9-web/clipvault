import asyncio
import json
import os

os.environ.setdefault("WORKER_TOKEN", "test-secret")
os.environ.setdefault("WEB_ENABLED", "1")

import config
import db
import selftest

config.WORKER_TOKEN = "test-secret"


class FakeUser:
    def __str__(self):
        return "clip bot#0000"


class FakeAttachment:
    def __init__(self, aid):
        self.id = aid
        self.url = f"https://cdn.discordapp.com/attachments/1/2/x.mp4?ex=FRESH{aid}"


class FakeMessage:
    def __init__(self, aid):
        self.attachments = [FakeAttachment(aid)]


class FakeChannel:
    async def fetch_message(self, mid):
        return FakeMessage(db.clip_by_message(mid)["attachment_id"])


class FakeClient:
    user = FakeUser()

    def get_channel(self, cid):
        return FakeChannel()


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()

    db.save_transcript(1, "バロン取られた やばい 次いくぞ",
                       [{"start": 3.2, "end": 4.9, "text": "バロン取られた"},
                        {"start": 5.1, "end": 6.0, "text": "やばい"},
                        {"start": 8.4, "end": 9.8, "text": "次いくぞ"}], "large-v3")

    import api
    import webui
    from aiohttp.test_utils import TestClient, TestServer

    tok = webui.new_token()
    db.issue_token(tok, 4242, "つるかん", 1, days=30)

    app = api.build_app(FakeClient())
    async with TestClient(TestServer(app)) as cli:
        async def get(path, **kw):
            return await cli.get(path, headers={"X-ClipVault-Token": tok}, **kw)

        async def send(method, path, body):
            return await cli.request(method, path, json=body,
                                     headers={"X-ClipVault-Token": tok})

        r = await cli.get("/api/clips")
        print("トークン無し           ->", r.status, "(401 が正しい)")
        assert r.status == 401

        r = await get("/api/me")
        print("GET /api/me           ->", await r.json())

        r = await get("/api/facets")
        f = await r.json()
        print("GET /api/facets       -> games:", [g["name"] for g in f["games"]],
              "| tags:", len(f["tags"]), "| members:", len(f["members"]))

        r = await get("/api/clips?sort=hot&limit=3")
        d = await r.json()
        print(f"GET /api/clips        -> total={d['total']} items={len(d['items'])}")
        print("   1件目:", json.dumps(
            {k: d["items"][0][k] for k in ("id", "title", "game", "tags", "stars", "thumb")},
            ensure_ascii=False))

        r = await get("/api/clips?q=" + "バロン")
        d = await r.json()
        hit = d["items"][0]
        print("検索 'バロン'          ->", d["total"], "件 / preview:",
              json.dumps(hit["preview"], ensure_ascii=False))

        r = await get("/api/clips/1")
        c = await r.json()
        print("GET /api/clips/1      -> segments:", len(c["segments"]),
              "| tags:", c["tags"], "| game:", c["game"])

        r = await send("PUT", "/api/clips/1/tags", {"tags": ["ペンタキル", "神プレイ"]})
        print("PUT tags              ->", json.dumps(await r.json(), ensure_ascii=False))
        r = await get("/api/clips/1")
        c = await r.json()
        assert c["game"] == "LoL", "ゲームタグが消えている"
        print("   ゲームタグ保持:", c["game"], "| タグ:", c["tags"])

        r = await send("POST", "/api/clips/1/reactions", {"emoji": "🔥"})
        j = await r.json()
        print("POST reaction (付与)   ->", j["added"], json.dumps(j["reactions"], ensure_ascii=False))
        r = await send("POST", "/api/clips/1/reactions", {"emoji": "🔥"})
        j = await r.json()
        print("POST reaction (解除)   ->", j["added"], j["reactions"])
        await send("POST", "/api/clips/1/reactions", {"emoji": "🔥"})

        r = await send("POST", "/api/clips/1/comments", {"body": "ここのフラッシュが全部", "at": 3.2})
        cid = (await r.json())["id"]
        await send("POST", "/api/clips/1/comments", {"body": "殿堂入りでいい", "at": None})
        r = await get("/api/clips/1")
        c = await r.json()
        print("コメント              ->", [(x["at"], x["body"]) for x in c["comments"]])
        print("   stars(Discord+Web) ->", c["stars"], "= discord", c["discord_stars"],
              "+ web", sum(len(v) for v in c["reactions"].values()))

        r = await get("/api/clips?q=" + "殿堂")
        print("コメント本文で検索      ->", (await r.json())["total"], "件")

        r = await cli.delete("/api/comments/%d" % cid,
                             headers={"X-ClipVault-Token": tok})
        print("DELETE comment        ->", await r.json())

        r = await get("/media/1", allow_redirects=False)
        print("GET /media/1          ->", r.status, r.headers.get("Location", "")[:52])
        assert r.status == 302 and "FRESH" in r.headers["Location"]

        r = await get("/thumb/1")
        print("GET /thumb/1          ->", r.status, r.headers.get("Content-Type"))

        # <img> はヘッダーを送れないので ?t= でも通る必要がある
        r = await cli.get(f"/thumb/1?t={tok}")
        print("GET /thumb/1?t=       ->", r.status, "(クエリ認証)")
        assert r.status == 200, "クエリのトークンで認証できない"
        r = await cli.get("/thumb/1")
        print("GET /thumb/1 (認証なし) ->", r.status, "(401 が正しい)")
        assert r.status == 401

        r = await cli.get("/?t=" + tok)
        body = await r.text()
        print("GET /                 ->", r.status, len(body), "bytes",
              "| cookie:", "cv_token" in r.headers.get("Set-Cookie", ""))
        assert "CLIPVAULT" in body

        r = await cli.post("/api/jobs/claim", json={"tier": "gpu"},
                           headers={"Authorization": "Bearer test-secret"})
        print("ワーカーAPIも生存       ->", r.status)

    print("\nWEB API OK")


if __name__ == "__main__":
    asyncio.run(main())


async def activity_checks():
    """Activity 用の経路（/.proxy 配下と CLIENT_ID 埋め込み）を確認する。"""
    import api
    import webui
    from aiohttp.test_utils import TestClient, TestServer

    config.CLIENT_ID = "123456789012345678"
    config.CDN_PROXY_PREFIX = "/.proxy/cdn"

    tok = webui.new_token()
    db.issue_token(tok, 4242, "つるかん", 1, days=30)
    app = api.build_app(FakeClient())

    async with TestClient(TestServer(app)) as cli:
        H = {"X-ClipVault-Token": tok}

        r = await cli.get("/")
        body = await r.text()
        print("CLIENT_ID 埋め込み     ->", config.CLIENT_ID in body)
        assert f'"{config.CLIENT_ID}"' in body, "CLIENT_ID が値として入っていない"
        assert "window.__CLIENT_ID__" in body, "変数名まで置換されている"
        assert "window.__CDN_PREFIX__" in body, "変数名まで置換されている"
        assert "%%" not in body, "未置換のプレースホルダが残っている"

        # 起動経路とアセット参照の検査。
        # 置換が無言で失敗して boot.js のタグが入っていない事故があった。
        assert 'src="assets/boot.js"' in body, "boot.js の読み込みタグが無い"
        assert "\nboot();" not in body, "boot() を直接呼んでいる（boot.js を経由しない）"
        assert "window.__start = boot;" in body, "起動関数が公開されていない"
        assert "fonts.googleapis.com" not in body, "外部フォント参照が残っている（CSPで弾かれる）"
        assert 'href="assets/fonts.css"' in body, "同梱フォントの参照が無い"
        print("起動経路とアセット      -> OK")

        for path in ("/assets/fonts.css", "/assets/fonts/cinzel-600.woff2"):
            rr = await cli.get(path)
            print(f"{path:<34} -> {rr.status}")
            assert rr.status == 200

        # サーバーが返した実物のスクリプトを構文検査する。
        # テンプレート置換で壊れると画面が真っ白になるため。
        import shutil
        import subprocess
        import tempfile
        if shutil.which("node"):
            script = body.split("<script>")[1].split("</script>")[0]
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(script)
                tmp = f.name
            res = subprocess.run(["node", "--check", tmp], capture_output=True)
            print("描画後スクリプトの構文 ->",
                  "OK" if res.returncode == 0 else res.stderr.decode()[:300])
            assert res.returncode == 0, "置換後のスクリプトが構文エラー"
        else:
            print("描画後スクリプトの構文 -> node が無いのでスキップ")

        r = await cli.get("/.proxy/api/clips", headers=H)
        print("GET /.proxy/api/clips ->", r.status, (await r.json())["total"], "件")
        assert r.status == 200

        r = await cli.get("/.proxy/api/clips/1", headers=H)
        d = await r.json()
        print("   cdn フィールド      ->", d["cdn"][:46])
        assert "cdn.discordapp.com" in d["cdn"]

        r = await cli.get("/.proxy/thumb/1", headers=H)
        print("GET /.proxy/thumb/1   ->", r.status)

        r = await cli.post("/.proxy/api/clips/1/reactions", json={"emoji": "🔥"}, headers=H)
        print("POST /.proxy/...      ->", r.status)

        config.CLIENT_SECRET = "dummy-secret"
        r = await cli.post("/api/token", json={"code": ""})
        print("code 空でトークン交換  ->", r.status, "(400 が正しい)")
        assert r.status == 400

        config.CLIENT_SECRET = ""
        r = await cli.post("/api/token", json={"code": "x"})
        print("SECRET 未設定          ->", r.status, "(503 が正しい)")
        assert r.status == 503

        r = await cli.get("/assets/embedded-app-sdk.js")
        print("SDK 配信              ->", r.status,
              f"{len(await r.text()) // 1024} KB")
        assert r.status == 200

        r = await cli.get("/assets/boot.js")
        print("boot.js 配信          ->", r.status)
        assert r.status == 200

    print("\nACTIVITY OK")


if __name__ == "__main__" and "--activity" in __import__("sys").argv:
    asyncio.run(activity_checks())

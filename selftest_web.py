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

        print("\n--- お気に入り ---")
        r = await get("/api/facets")
        print("初期件数              ->", (await r.json())["favorites"])

        for cid in (1, 3, 5):
            rr = await send("POST", f"/api/clips/{cid}/favorite", {})
            j = await rr.json()
            print(f"  clip {cid} を追加      -> favorited={j['favorited']} count={j['count']}")
        assert j["count"] == 3

        r = await get("/api/clips?fav=1")
        d = await r.json()
        ids = sorted(x["id"] for x in d["items"])
        print("お気に入りのみ         ->", d["total"], "件", ids)
        assert ids == [1, 3, 5]
        assert all(x["favorited"] for x in d["items"])

        r = await get("/api/clips")
        d = await r.json()
        flags = {x["id"]: x["favorited"] for x in d["items"]}
        print("通常一覧の favorited   ->", {k: v for k, v in flags.items() if v})
        assert flags.get(1) and not flags.get(2)

        r = await send("POST", "/api/clips/3/favorite", {})
        print("  clip 3 を解除        ->", await r.json())
        r = await get("/api/clips?fav=1")
        print("解除後                ->", (await r.json())["total"], "件")
        assert (await (await get("/api/clips?fav=1")).json())["total"] == 2

        r = await get("/api/clips?fav=1&q=" + "バロン")
        print("お気に入り × 検索      ->", (await r.json())["total"], "件")

        # 別ユーザーには影響しない
        tok2 = webui.new_token()
        db.issue_token(tok2, 9999, "たいち", 1, days=30)
        r = await cli.get("/api/clips?fav=1", headers={"X-ClipVault-Token": tok2})
        print("別ユーザーのお気に入り  ->", (await r.json())["total"], "件 (0 が正しい)")
        assert (await (await cli.get("/api/clips?fav=1",
                headers={"X-ClipVault-Token": tok2})).json())["total"] == 0

        # ---------------------------------------------------- ショート
        async def feed(seed, offset=0, limit=2, extra=""):
            rr = await get(f"/api/clips?sort=random&seed={seed}&unseen=1"
                           f"&start=2999-01-01&limit={limit}&offset={offset}{extra}")
            return await rr.json()

        whole = await feed(7, 0, 100)
        ids = [c["id"] for c in whole["items"]]
        again = [c["id"] for c in (await feed(7, 0, 100))["items"]]
        other = [c["id"] for c in (await feed(8, 0, 100))["items"]]
        pages = []
        off = 0
        while True:
            d = await feed(7, off, 2)
            pages += [c["id"] for c in d["items"]]
            off += len(d["items"])
            if not d["has_more"]:
                break
        print("ショート: 順番         ->", ids, "| 別シード:", other)
        assert ids == again, "同じシードで順番が変わった"
        assert sorted(ids) == sorted(other) and len(set(ids)) == len(ids) == whole["total"]
        assert pages == ids, "ページ送りで重複・抜けが出た"

        r = await send("POST", f"/api/clips/{ids[0]}/seen", {})
        assert r.status == 200
        after = [c["id"] for c in (await feed(7, 0, 100))["items"]]
        print("既読にした後           ->", after, f"({ids[0]} が最後に回る)")
        assert after[-1] == ids[0] and after[:-1] == [i for i in ids if i != ids[0]]

        d = await (await get("/api/clips?sort=random&seed=7&unseen=1&start=2000-01-01&limit=100")).json()
        assert [c["id"] for c in d["items"]] == ids, "開いた後に見たものは並びを変えない"
        f2 = await (await get("/api/facets")).json()
        assert f2["seen"] == 1
        r = await send("POST", "/api/clips/99999/seen", {})
        assert r.status == 404

        # ---------------------------------------------------- ブラウザ版ログイン
        config.CLIENT_ID, config.CLIENT_SECRET = "123", "sec"
        config.PUBLIC_URL = "https://cv.example.ts.net"
        r = await cli.get("/login?next=/%3Fmode%3Dshorts", allow_redirects=False)
        loc = r.headers.get("Location", "")
        print("GET /login            ->", r.status, loc[:60] + "…")
        assert r.status == 302 and loc.startswith("https://discord.com/oauth2/authorize")
        from urllib.parse import unquote
        assert "redirect_uri=https://cv.example.ts.net/auth/callback" in unquote(loc)
        state = loc.split("state=")[1].split("&")[0]
        login_cookie = r.cookies["cv_login"].value
        assert login_cookie.startswith(state + ".")
        # 本番は https なので Secure クッキーが送られる。テストは http なので手で付ける。
        LC = {"Cookie": f"cv_login={login_cookie}"}

        r = await cli.get(f"/auth/callback?code=x&state=WRONG", allow_redirects=False)
        print("callback（state 不一致）->", r.status, "(400 が正しい)")
        assert r.status == 400

        async def fake_exchange(code, redirect=""):
            assert redirect == "https://cv.example.ts.net/auth/callback"
            return {"access_token": "a", "user": {"id": "5555", "global_name": "やしき"}}
        real_exchange = webui.exchange_code
        webui.exchange_code = fake_exchange
        saved_guild = config.GUILD_ID
        try:
            config.GUILD_ID = 0
            r = await cli.get(f"/auth/callback?code=x&state={state}", allow_redirects=False,
                              headers=LC)
            print("callback（正常）        ->", r.status, r.headers.get("Location"))
            assert r.status == 302 and r.headers["Location"] == "/?mode=shorts"
            web_tok = r.cookies["cv_token"].value
            row = db.resolve_token(web_tok)
            assert row and row["user_name"] == "やしき"
            assert row["expires_at"][:4] >= "2026"
            r = await cli.get("/api/me", headers={"X-ClipVault-Token": web_tok})
            assert (await r.json())["name"] == "やしき"

            # 2 台目でログインしても 1 台目は切れない
            tok_b = webui.new_token()
            db.issue_token(tok_b, 5555, "やしき", 0, days=90)
            assert db.resolve_token(web_tok) and db.resolve_token(tok_b)
            print("別の端末でログイン      -> 両方有効")

            # サーバーのメンバーでなければ入れない
            r = await cli.get("/login", allow_redirects=False)
            st2 = r.headers["Location"].split("state=")[1].split("&")[0]
            lc2 = {"Cookie": f"cv_login={r.cookies['cv_login'].value}"}
            config.GUILD_ID = 424242
            r = await cli.get(f"/auth/callback?code=x&state={st2}", allow_redirects=False,
                              headers=lc2)
            print("メンバー以外            ->", r.status, "(403 が正しい)")
            assert r.status == 403
        finally:
            webui.exchange_code = real_exchange
            config.GUILD_ID = saved_guild

        # 外部サイトへの戻り先は無視する
        assert webui._safe_next("https://evil.example") == "/"
        assert webui._safe_next("//evil.example") == "/"
        assert webui._safe_next("/?mode=shorts") == "/?mode=shorts"

        # 使っている間は期限が延びる
        db.connect().execute("UPDATE web_tokens SET expires_at = '2000-01-01' WHERE token = ?",
                             (tok_b,))
        assert not db.renew_token(tok_b, 90), "期限切れは延ばさない"
        near = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                + __import__("datetime").timedelta(days=3)).isoformat()
        db.connect().execute("UPDATE web_tokens SET expires_at = ? WHERE token = ?",
                             (near, web_tok))
        db.connect().commit()
        r = await cli.get("/api/me", headers={"X-ClipVault-Token": web_tok})
        assert db.resolve_token(web_tok)["expires_at"] > near
        print("期限の自動延長          -> OK")

        r = await cli.get("/logout", headers={"Cookie": f"cv_token={web_tok}"})
        print("GET /logout           ->", r.status)
        assert db.resolve_token(web_tok) is None

        r = await cli.get("/manifest.webmanifest")
        mf = json.loads(await r.text())
        print("manifest              ->", r.status, r.headers["Content-Type"], mf["display"])
        assert r.status == 200 and "manifest" in r.headers["Content-Type"]
        for ic in mf["icons"]:
            rr = await cli.get(ic["src"])
            assert rr.status == 200, ic["src"]
        r = await cli.get("/api/version")
        ver = (await r.json())["v"]
        body = await (await cli.get("/")).text()
        assert f'__UI_VERSION__ = "{ver}"' in body, "画面にバージョンが埋め込まれていない"
        print("GET /api/version      ->", ver)
        r = await cli.get("/sw.js")
        assert r.status == 200 and "javascript" in r.headers["Content-Type"]
        r = await cli.get("/assets/icons/apple-touch-icon.png")
        assert r.status == 200
        config.PUBLIC_URL = ""

        r = await send("POST", "/api/log",
                       {"tag": "media", "detail": {"errorCode": 4}})
        print("POST /api/log         ->", r.status, "(サーバーログに残る)")
        assert r.status == 200
        r = await cli.post("/api/log", json={"tag": "x"})
        print("認証なし               ->", r.status, "(401 が正しい)")
        assert r.status == 401

        r = await get("/media/1", allow_redirects=False)
        print("GET /media/1          ->", r.status, r.headers.get("Location", "")[:52])
        assert r.status == 302 and "FRESH" in r.headers["Location"]

        # Activity 用の中継経路。ここでは「ルートが生きていて認証を通る」
        # ことだけ見る。上流はダミーURLなので本体の検証は selftest_media.py。
        r = await get("/media/1?stream=1")
        print("GET /media/1?stream=1 ->", r.status, "(ルート疎通のみ確認)")
        assert r.status != 401, "中継経路で認証が通らない"

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

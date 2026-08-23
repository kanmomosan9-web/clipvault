"""同時視聴を、実際に2人分のクライアントで検証する。

ホストが配った再生位置とクリップが、もう一方に届いて反映されるか。
ホストの横取り防止と、沈黙後の引き継ぎも見る。
"""
import asyncio
import os

os.environ.setdefault("WORKER_TOKEN", "t")
os.environ.setdefault("WEB_ENABLED", "1")

import config
import db
import selftest
import session

config.WORKER_TOKEN = "t"


class FakeClient:
    user = "bot"

    def get_channel(self, cid):
        return None


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()

    import api
    import webui
    from aiohttp.test_utils import TestClient, TestServer

    ta = webui.new_token()
    tb = webui.new_token()
    db.issue_token(ta, 1, "つるかん", 1)
    db.issue_token(tb, 2, "たいち", 1)
    A = {"X-ClipVault-Token": ta}
    B = {"X-ClipVault-Token": tb}
    INST = "i-1540-pc-99"

    async with TestClient(TestServer(api.build_app(FakeClient()))) as cli:
        async def get(h):
            r = await cli.get(f"/api/session/{INST}", headers=h)
            return await r.json()

        async def post(h, path, body):
            r = await cli.post(f"/api/session/{INST}{path}", headers=h, json=body)
            return await r.json()

        print("=== 参加 ===")
        st = await get(A)
        print("  A が参加        -> host:", st["host_name"] or "(なし)")
        st = await get(B)
        print("  B が参加        -> watchers:", st["watchers"])
        assert len(st["watchers"]) == 2

        print("\n=== A がホストになる ===")
        st = await post(A, "/host", {"take": True})
        print("  A:", st["host_name"], "| is_host:", st["is_host"])
        assert st["is_host"]
        st = await get(B)
        print("  B から見て       -> host:", st["host_name"],
              "| is_host:", st["is_host"])
        assert not st["is_host"]

        print("\n=== A の画面がそのまま B に届く ===")
        view = {"query": "バロン", "sort": "hot", "onlyFav": False,
                "game": "LoL", "tag": None, "by": None, "loaded": 72,
                "openId": 5, "position": 12.5, "playing": True, "scroll": 840}
        await post(A, "/push", {"view": view})
        st = await get(B)
        v = st["view"]
        print("  B が受け取った画面:")
        print("    検索語     :", v["query"])
        print("    並び順     :", v["sort"])
        print("    ゲーム絞込 :", v["game"])
        print("    読込済み   :", v["loaded"], "件")
        print("    開いている :", "clip", v["openId"], f"({v['position']}秒 再生中)")
        print("    スクロール :", v["scroll"], "px")
        for k in view:
            assert v.get(k) == view[k], f"{k} が伝わっていない"

        st = await post(A, "/push", {"view": {**view, "bogus": "捨てられる"}})
        print("  未知のキーは捨てる:", "bogus" not in st["view"])
        assert "bogus" not in st["view"]

        print("\n=== 変化のたびに rev が上がる（追従の判定に使う）===")
        r1 = st["rev"]
        await post(A, "/push", {"view": {**view, "position": 13.0}})
        r2 = (await get(B))["rev"]
        await post(A, "/push", {"view": {**view, "query": "ペンタ"}})
        r3 = (await get(B))["rev"]
        print(f"  位置だけ変化: rev {r1} -> {r2} / 検索語も変化: -> {r3}")
        assert r3 > r2

        print("\n=== B は勝手に上書きできない ===")
        st = await post(B, "/push", {"view": {"query": "のっとり"}})
        print("  B が配布を試行    -> query:", st["view"]["query"], "(ペンタ のまま)")
        assert st["view"]["query"] == "ペンタ"
        st = await post(B, "/host", {"take": True})
        print("  B が横取りを試行  -> host:", st["host_name"], "(つるかん のまま)")
        assert st["host_name"] == "つるかん"

        print("\n=== A が沈黙したら B が引き継げる ===")
        session._sessions[INST]["updated_at"] -= 30
        st = await post(B, "/host", {"take": True})
        print("  B:", st["host_name"], "| is_host:", st["is_host"])
        assert st["is_host"]

        print("\n=== 共有をやめると参加者に伝わる ===")
        st = await post(B, "/host", {"take": False})
        print("  host:", st["host_name"] or "(なし)", "| view:", st["view"])
        assert st["host_id"] is None and st["view"] == {}
        st = await get(A)
        print("  A から見ても host なし ->", st["host_id"] is None)
        assert st["host_id"] is None

        print("\n=== 変化を待って即座に返る（ロングポーリング）===")
        await post(A, "/host", {"take": True})
        st = await post(A, "/push", {"view": {"query": "まえ"}})
        tick = st["tick"]

        async def waiter():
            r = await cli.get(f"/api/session/{INST}?since={tick}", headers=B)
            return await r.json()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.2)
        t0 = asyncio.get_event_loop().time()
        await post(A, "/push", {"view": {"query": "あと"}})
        got = await task
        ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        print(f"  ホスト操作から {ms} ms で受信 -> query:", got["view"]["query"])
        assert got["view"]["query"] == "あと" and ms < 500

        print("\n=== 位置は経過時間つきで返る ===")
        await post(A, "/push", {"view": {"openId": 5, "position": 12.5,
                                         "playing": True}})
        st = await get(B)
        print(f"  直後       -> position={st['view']['position']} "
              f"elapsed={st['elapsed']}")
        await asyncio.sleep(0.5)
        st = await get(B)
        fixed = round(st["view"]["position"] + st["elapsed"], 2)
        print(f"  0.5秒後    -> elapsed={st['elapsed']} / 補正後={fixed}")
        assert st["elapsed"] >= 0.4, "経過時間が返っていない"
        assert abs(fixed - 13.0) < 0.2, "補正後の位置がおかしい"

        print("\n=== お気に入りはホストのものを見る ===")
        rr = await cli.post("/api/clips/1/favorite", headers=A, json={})
        print("  A が clip 1 をお気に入りに:", (await rr.json())["favorited"])
        rr = await cli.get("/api/clips?fav=1", headers=B)
        print("  B が自分のお気に入り     ->", (await rr.json())["total"], "件")
        rr = await cli.get(f"/api/clips?fav=1&as_host=1&instance={INST}", headers=B)
        d = await rr.json()
        print("  B がホスト視点で取得     ->", d["total"], "件",
              [x["id"] for x in d["items"]])
        assert d["total"] == 1 and d["items"][0]["id"] == 1

        rr = await cli.get(f"/api/clips?fav=1&as_host=2&instance={INST}", headers=B)
        print("  ホストでない人を指定     ->", (await rr.json())["total"],
              "件 (0 が正しい・なりすまし防止)")
        assert (await (await cli.get(
            f"/api/clips?fav=1&as_host=2&instance={INST}", headers=B)).json())["total"] == 0

        await post(A, "/host", {"take": False})

        print("\n=== 認証 ===")
        r = await cli.get(f"/api/session/{INST}")
        print("  トークンなし     ->", r.status, "(401 が正しい)")
        assert r.status == 401

    print("\nSESSION OK")


if __name__ == "__main__":
    asyncio.run(main())

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

        print("\n=== 共有をやめる ===")
        st = await post(B, "/host", {"take": False})
        print("  host:", st["host_name"] or "(なし)")
        assert st["host_id"] is None

        print("\n=== 認証 ===")
        r = await cli.get(f"/api/session/{INST}")
        print("  トークンなし     ->", r.status, "(401 が正しい)")
        assert r.status == 401

    print("\nSESSION OK")


if __name__ == "__main__":
    asyncio.run(main())

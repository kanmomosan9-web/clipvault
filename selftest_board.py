"""掲示板・通知・自動更新・フォーラム同期の検査。

  python selftest_board.py
"""
import asyncio
import os
from types import SimpleNamespace as NS

os.environ.setdefault("WEB_ENABLED", "1")

import discord

import board
import config
import db
import forum
import notify
import selftest
import selftest_web as sw

fail = []


def check(cond, label):
    print(f"  {'OK ' if cond else 'NG '} {label}")
    if not cond:
        fail.append(label)


class Sent:
    def __init__(self, mid, channel_id):
        self.id = mid
        self.channel = NS(id=channel_id)


class FakeWebhook:
    def __init__(self):
        self.sent, self.edited, self.deleted, self.next = [], [], [], 9000

    async def send(self, content, username, avatar_url=None, thread=None, thread_name=None,
                   wait=True, allowed_mentions=None):
        self.next += 1
        ch = thread.id if thread is not None else self.next + 50000
        self.sent.append({"content": content, "username": username,
                          "thread": getattr(thread, "id", None), "thread_name": thread_name})
        return Sent(self.next if thread is not None else ch, ch)

    async def edit_message(self, mid, content, thread):
        self.edited.append((mid, content))

    async def delete_message(self, mid, thread):
        self.deleted.append(mid)


class FakeSrc:
    def __init__(self, log):
        self.log = log

    async def forward(self, dest):
        self.log.append(dest.id)
        return NS(id=77777)


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()
    c = db.connect()
    c.execute("UPDATE clips SET uploader_id = 100, uploader_name = 'つるかん' WHERE id = 1")
    c.execute("UPDATE clips SET uploader_id = 200, uploader_name = 'たいち' WHERE id = 2")
    c.commit()

    import api
    import webui
    from aiohttp.test_utils import TestClient, TestServer

    toks = {}
    for uid, name in ((100, "つるかん"), (200, "たいち"), (300, "やしき")):
        toks[uid] = webui.new_token()
        db.issue_token(toks[uid], uid, name, 0, days=30)

    hooks = []

    class Client(sw.FakeClient):
        pass

    cl = Client()
    for name in ("on_board_post", "on_board_edit", "on_board_delete", "on_board_rename",
                 "on_board_thread_delete"):
        async def h(*a, _n=name):
            hooks.append((_n, a))
        setattr(cl, name, h)

    app = api.build_app(cl)
    async with TestClient(TestServer(app)) as cli:
        def H(uid):
            return {"X-ClipVault-Token": toks[uid]}

        async def get(uid, path):
            r = await cli.get(path, headers=H(uid))
            return r.status, await r.json()

        async def send(uid, m, path, body=None):
            r = await cli.request(m, path, json=body or {}, headers=H(uid))
            return r.status, await r.json()

        print("\n=== 自動更新（件数の変化） ===")
        _, p0 = await get(100, "/api/pulse")
        seq = p0["seq"]
        await send(200, "POST", "/api/clips/1/reactions", {"emoji": "🔥"})
        await send(300, "POST", "/api/clips/1/comments", {"body": "ここ好き", "at": 3})
        _, p1 = await get(100, f"/api/pulse?since={seq}")
        ch = {x["id"]: x for x in p1["changes"]}
        check(1 in ch and ch[1]["comments"] == 1 and ch[1]["stars"] >= 1,
              f"変わったクリップの件数が届く {p1['changes']}")
        db.set_reactions(db.get_clip(2)["message_id"], 5)
        _, p2 = await get(100, f"/api/pulse?since={p1['seq']}")
        check([x["id"] for x in p2["changes"]] == [2], "Discord 側のリアクション変化も届く")

        print("\n=== 通知 ===")
        _, n = await get(100, "/api/notifications")
        kinds = [x["kind"] for x in n["items"]]
        check("comment" in kinds and "reaction" in kinds, f"コメントとリアクションの通知 {kinds}")
        check(n["unread"] == 2 and p1["notifications"] == 2, "未読 2 件")
        await send(300, "POST", "/api/clips/1/reactions", {"emoji": "😂"})
        _, n = await get(100, "/api/notifications")
        rx = [x for x in n["items"] if x["kind"] == "reaction"]
        check(len(rx) == 1 and set(rx[0]["actors"]) == {"たいち", "やしき"},
              "未読のリアクションは 1 件にまとめる")
        await send(100, "POST", "/api/clips/1/reactions", {"emoji": "👏"})
        _, n2 = await get(100, "/api/notifications")
        check(n2["unread"] == 2, "自分の操作では通知しない")
        _, st = await send(100, "POST", "/api/settings", {"dm": True})
        check(st["dm"] is True and notify.dm_enabled(100), "DM 通知をオンにできる")
        check(not notify.dm_enabled(200), "DM 通知は最初オフ")
        await send(100, "POST", "/api/notifications/read", {})
        _, n3 = await get(100, "/api/notifications")
        check(n3["unread"] == 0, "すべて既読")
        check("?clip=1&tab=c" in notify.link_for({"kind": "comment", "clip_id": 1})
              if config.PUBLIC_URL else True, "コメント通知のリンク")

        print("\n=== 掲示板 ===")
        board._baseline(200)
        board._baseline(300)
        s, e = await send(100, "POST", "/api/board/threads", {"title": "", "body": "x"})
        check(s == 400, "タイトル無しは作れない")
        s, t = await send(100, "POST", "/api/board/threads",
                          {"title": "神プレイ集", "body": "貼ってこ", "clip_id": 2})
        tid = t["id"]
        check(s == 200 and tid, "スレッドを立てられる（クリップ付き）")
        _, n = await get(200, "/api/notifications")
        check(any(x["kind"] == "share" and x["thread_id"] == tid for x in n["items"]),
              "自分のクリップが共有されたら通知（たいち）")
        check(("on_board_post", (t["post_id"],)) in hooks, "Discord へ送る処理が呼ばれる")

        _, lst = await get(300, "/api/board/threads")
        it = next(x for x in lst["items"] if x["id"] == tid)
        check(it["unread"] == 1 and it["videos"] == 1 and it["cover"] == 2,
              f"一覧：未読・動画数・表紙 {it}")
        _, pl = await get(300, "/api/pulse")
        check(pl["board"] == 1, "掲示板の未読数が届く")

        s, r = await send(300, "POST", f"/api/board/threads/{tid}/posts", {"body": "エグい"})
        check(s == 200 and r["post"]["body"] == "エグい", "書き込める")
        _, n = await get(100, "/api/notifications")
        check(any(x["kind"] == "reply" and x["thread_id"] == tid for x in n["items"]),
              "スレッドを立てた人に返信の通知")
        _, d = await get(300, f"/api/board/threads/{tid}")
        check(len(d["posts"]) == 2 and d["posts"][0]["clip"]["id"] == 2, "スレッドの中身")
        _, lst = await get(300, "/api/board/threads")
        check(next(x for x in lst["items"] if x["id"] == tid)["unread"] == 0, "開いたら既読")
        _, d2 = await get(300, f"/api/board/threads/{tid}?after={d['posts'][0]['id']}")
        check(len(d2["posts"]) == 1, "続きだけ取れる（after）")

        pid = r["post"]["id"]
        s, _ = await send(100, "PATCH", f"/api/board/posts/{pid}", {"body": "改ざん"})
        check(s == 403, "他人の書き込みは編集できない")
        s, e = await send(300, "PATCH", f"/api/board/posts/{pid}", {"body": "エグすぎ"})
        check(s == 200 and e["post"]["body"] == "エグすぎ" and e["post"]["edited_at"],
              "自分の書き込みは編集できる")
        s, _ = await send(300, "PATCH", f"/api/board/threads/{tid}", {"title": "乗っ取り"})
        check(s == 403, "立てた人以外はタイトルを変えられない")
        s, _ = await send(100, "PATCH", f"/api/board/threads/{tid}", {"title": "神プレイ集2"})
        check(s == 200 and board.get_thread(tid)["title"] == "神プレイ集2", "立てた人はタイトル変更")
        s, _ = await send(300, "DELETE", f"/api/board/posts/{pid}")
        check(s == 200 and board.get_post(pid) is None, "自分の書き込みは削除できる")

        _, m = await get(100, "/api/board/threads?mode=mine")
        _, m2 = await get(200, "/api/board/threads?mode=mine")
        check(any(x["id"] == tid for x in m["items"]) and not m2["items"],
              "『自分が参加』で絞れる")
        _, cl_ = await get(300, "/api/board/threads?mode=clips")
        check(any(x["id"] == tid for x in cl_["items"]), "クリップありで絞れる")
        _, q = await get(300, "/api/board/threads?q=" + "貼ってこ")
        check(any(x["id"] == tid for x in q["items"]), "本文で検索できる")

        s, _ = await send(300, "DELETE", f"/api/board/threads/{tid}")
        check(s == 403, "立てた人以外はスレッドを削除できない")
        s, _ = await send(100, "DELETE", f"/api/board/threads/{tid}")
        check(s == 200 and board.get_thread(tid) is None, "立てた人はスレッドを削除できる")

    print("\n=== Discord → 掲示板 ===")
    config.FORUM_CHANNEL_ID = 555
    clip1 = db.get_clip(1)
    fwd = NS(message_id=clip1["message_id"], type=discord.MessageReferenceType.forward)
    msg = NS(reference=fwd, content="", attachments=[])
    check(forum.clip_in(msg)["id"] == 1, "転送からクリップを判別")
    link = f"見て https://discord.com/channels/1/{clip1['channel_id']}/{clip1['message_id']}"
    msg = NS(reference=None, content=link, attachments=[])
    check(forum.clip_in(msg)["id"] == 1 and forum.clean_body(msg, clip1) == "見て",
          "メッセージリンクからクリップを判別し、本文からリンクを除く")
    att = NS(content_type="video/mp4", filename="a.mp4", id=42)
    msg = NS(reference=None, content="", attachments=[att], channel=NS(id=9), id=10)
    check(forum.video_in(msg)["id"] == 42, "直接アップロードの動画を判別")

    fake_thread = NS(id=8000, parent_id=555, name="Discordのスレ", owner_id=300, owner=None)

    real = forum._is_forum_thread
    forum._is_forum_thread = lambda ch: ch is fake_thread
    try:
        user = NS(id=300, display_name="やしき")
        cli2 = NS(user=NS(id=1), get_guild=lambda g: None)
        m1 = NS(channel=fake_thread, webhook_id=None, author=user, id=8000,
                reference=None, content="Discordから立てた", attachments=[])
        await forum.pull(cli2, m1)
        t = board.thread_by_discord(8000)
        check(t is not None and t["title"] == "Discordのスレ", "Discord で立てたスレッドを取り込む")
        m2 = NS(channel=fake_thread, webhook_id=None, author=NS(id=100, display_name="つるかん"),
                id=8001, reference=fwd, content="", attachments=[])
        await forum.pull(cli2, m2)
        ps = board.posts(t["id"])
        check(len(ps) == 2 and ps[1]["clip_id"] == 1, "転送されたクリップはクリップとして載る")
        await forum.pull(cli2, m2)
        check(len(board.posts(t["id"])) == 2, "同じメッセージは二重に取り込まない")
        forum.pull_edit(8000, "編集した")
        check(board.posts(t["id"])[0]["body"] == "編集した", "Discord での編集を反映")
        forum.pull_delete(8001)
        check(len(board.posts(t["id"])) == 1, "Discord での削除を反映")
        forum.pull_thread_rename(8000, "改名")
        check(board.get_thread(t["id"])["title"] == "改名", "スレッド名の変更を反映")
        forum.pull_thread_delete(8000)
        check(board.get_thread(t["id"]) is None, "スレッドの削除を反映")
    finally:
        forum._is_forum_thread = real

    print("\n=== 掲示板 → Discord ===")
    wh = FakeWebhook()
    forwarded = []
    import sync as sy

    async def ensure(client, cid):
        return wh

    async def msg_for(client, clip):
        return FakeSrc(forwarded)

    async def chan(client, cid):
        return NS(id=cid)

    real_e, real_m, real_c = sy.ensure_webhook, sy._message_for, forum._channel
    sy.ensure_webhook, sy._message_for, forum._channel = ensure, msg_for, chan
    try:
        cli3 = NS(get_guild=lambda g: None)
        tid = board.create_thread("Webから", 100, "つるかん")
        p1 = board.add_post(tid, 100, "つるかん", "", clip_id=1)
        await forum.push_post(cli3, p1)
        t = board.get_thread(tid)
        check(wh.sent[0]["thread_name"] == "Webから" and t["discord_thread_id"],
              "最初の書き込みでフォーラムにスレッドを作る")
        check(wh.sent[0]["username"] == "つるかん" and "クリップを共有" in wh.sent[0]["content"],
              "本人の名前で投稿（本文なしはクリップ名）")
        check(forwarded == [t["discord_thread_id"]] and board.get_post(p1)["discord_forward_id"],
              "クリップは元の投稿を転送")
        p2 = board.add_post(tid, 200, "たいち", "いいね")
        await forum.push_post(cli3, p2)
        check(wh.sent[1]["thread"] == t["discord_thread_id"], "2 件目以降は同じスレッドへ")
        board.edit_post(p2, "いいね！")
        await forum.edit_post(cli3, p2)
        check(wh.edited and wh.edited[-1][1] == "いいね！", "編集も Discord に反映")

        # Web で書いたものを消すと、Discord 側は Webhook で消す（bot の権限不要）
        removed = []

        async def chan_del(client, cid):
            return NS(id=cid, get_partial_message=lambda m: NS(delete=lambda: _rm(m)))

        async def _rm(m):
            removed.append(m)
        forum._channel = chan_del
        p1row = dict(board.get_post(p1))
        board.delete_post(p1)
        await forum.delete_post(cli3, p1row, dict(board.get_thread(tid)))
        check(p1row["discord_message_id"] in wh.deleted, "本文は Webhook で消す")
        check(p1row["discord_forward_id"] in removed, "転送は bot が消す")
        forum._channel = chan

        # 権限が無くて送れなかった分は、あとでまとめて送り直す
        tid2 = board.create_thread("権限なしの間", 300, "やしき")
        q1 = board.add_post(tid2, 300, "やしき", "1件目")
        q2 = board.add_post(tid2, 200, "たいち", "2件目")

        async def no_webhook(client, cid):
            return None
        sy.ensure_webhook = no_webhook
        await forum.push_pending(cli3)
        check(board.get_post(q1)["discord_message_id"] is None, "送れない間は未送信のまま残る")
        sy.ensure_webhook = ensure
        n = await forum.push_pending(cli3)
        check(n == 2 and board.get_post(q2)["discord_message_id"]
              and board.get_thread(tid2)["discord_thread_id"],
              "権限が付いたら未送信分をまとめて送る（スレッドも作る）")
        check(await forum.push_pending(cli3) == 0, "送った分は二重に送らない")

        # 転送に失敗したら、元の投稿へのリンクを貼る
        class BadSrc:
            async def forward(self, dest):
                raise discord.HTTPException(NS(status=400, reason="Bad"), "cannot forward")
        posted = []

        async def chan2(client, cid):
            async def send(text, allowed_mentions=None):
                posted.append(text)
                return NS(id=88888)
            return NS(id=cid, send=send)

        async def bad_for(client, clip):
            return BadSrc()
        sy._message_for, forum._channel = bad_for, chan2
        q3 = board.add_post(tid2, 300, "やしき", "", clip_id=2)
        await forum.push_pending(cli3)
        check(posted and "discord.com/channels/" in posted[-1]
              and board.get_post(q3)["discord_forward_id"] == 88888,
              "転送できないときは元の投稿へのリンクを貼る")
        posted.clear()
        await forum.push_pending(cli3)
        check(not posted, "リンクを貼った分は送り直さない")
    finally:
        sy.ensure_webhook, sy._message_for, forum._channel = real_e, real_m, real_c
        config.FORUM_CHANNEL_ID = 0

    print(f"\n{'NG: ' + ' / '.join(fail) if fail else 'BOARD OK'}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

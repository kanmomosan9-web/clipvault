import asyncio
import os

os.environ.setdefault("WORKER_TOKEN", "t")

import config
import db
import selftest
import sync


CLIENT = [None]


class FakeThread:
    def __init__(self, tid):
        self.id = tid


class FakeWebhook:
    def __init__(self):
        self.sent = []
        self.id, self.name, self.token = 900, "ClipVault", "wht"

    async def send(self, content, username, avatar_url, thread, wait,
                   allowed_mentions):
        self.sent.append({"content": content, "username": username,
                          "thread": thread.id})
        return type("M", (), {"id": 5000 + len(self.sent)})()


class FakeReaction:
    def __init__(self, emoji, count, me):
        self.emoji, self.count, self.me = emoji, count, me


class FakeMessage:
    def __init__(self, mid):
        self.id = mid
        self.thread = None
        self.reactions = []
        self.added, self.removed = [], []

    async def create_thread(self, name, auto_archive_duration):
        self.thread = FakeThread(7000 + self.id)
        CLIENT[0].threads[self.thread.id] = self.thread
        return self.thread

    async def add_reaction(self, e):
        self.added.append(e)

    async def remove_reaction(self, e, who):
        self.removed.append(e)


class FakeChannel:
    def __init__(self, cid, wh):
        self.id, self._wh = cid, wh
        self.msgs = {}

    async def fetch_message(self, mid):
        return self.msgs.setdefault(mid, FakeMessage(mid))

    async def webhooks(self):
        return [self._wh]


class FakeMember:
    display_name = "つるかん"
    display_avatar = type("A", (), {"url": "https://cdn/avatar.png"})()


class FakeGuild:
    def get_member(self, uid):
        return FakeMember()


class FakeClient:
    def __init__(self):
        self.wh = FakeWebhook()
        self.channel = FakeChannel(2, self.wh)
        self.user = "bot"
        self.threads = {}

    def get_channel(self, cid):
        if cid == 2:
            return self.channel
        return self.threads.get(cid)

    async def fetch_channel(self, cid):
        if cid == 2:
            return self.channel
        if cid in self.threads:
            return self.threads[cid]
        raise LookupError(cid)

    def get_guild(self, gid):
        return FakeGuild()


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()
    config.SYNC_COMMENTS = config.SYNC_REACTIONS = True

    c = FakeClient()
    CLIENT[0] = c

    import discord
    discord.Thread = FakeThread          # isinstance 判定を差し替え

    print("=== コメント: Web -> Discord ===")
    cid = db.add_comment(1, 4242, "つるかん", "ここのフラッシュが全部", 23.0)
    await sync.push_comment(c, 1, cid, 4242, "つるかん", "ここのフラッシュが全部", 23.0)
    print("  webhook 送信:", c.wh.sent)
    assert c.wh.sent, "Webhook に流れていない"
    assert c.wh.sent[0]["content"].startswith("`0:23`"), "時刻が付いていない"
    assert c.wh.sent[0]["username"] == "つるかん", "本人名義になっていない"

    clip = db.get_clip(1)
    print("  スレッドID を記録:", clip["thread_id"])
    assert clip["thread_id"]
    print("  コメントとメッセージの紐付け:",
          db.list_comments(1)[0]["thread_message_id"])
    assert db.list_comments(1)[0]["thread_message_id"]

    print("\n=== 2件目はスレッドを作り直さない ===")
    before = clip["thread_id"]
    cid2 = db.add_comment(1, 1, "たいち", "殿堂入り", None)
    await sync.push_comment(c, 1, cid2, 1, "たいち", "殿堂入り", None)
    assert db.get_clip(1)["thread_id"] == before
    print("  同じスレッド:", db.get_clip(1)["thread_id"],
          "| 時刻なしの本文:", repr(c.wh.sent[1]["content"]))
    assert not c.wh.sent[1]["content"].startswith("`")

    print("\n=== コメント: Discord -> Web ===")

    class ThreadMsg:
        def __init__(self, body, webhook_id=None):
            self.channel = FakeThread(before)
            self.webhook_id = webhook_id
            self.content = body
            self.id = 8001
            self.author = type("U", (), {"id": 77, "display_name": "やしき"})()

    before_n = len(db.list_comments(1))
    ok = await sync.pull_comment(ThreadMsg("スレッドから書いた"))
    print("  取り込み:", ok, "| 件数", before_n, "->", len(db.list_comments(1)))
    assert ok and len(db.list_comments(1)) == before_n + 1

    ok = await sync.pull_comment(ThreadMsg("webhook 由来", webhook_id=900))
    print("  webhook 由来は無視:", not ok)
    assert not ok, "自分が流したものを取り込んでいる（ループする）"

    print("\n=== リアクション: Web -> Discord ===")
    msg = await c.channel.fetch_message(1000)
    db.toggle_reaction(1, 4242, "つるかん", "🔥")
    await sync.push_reaction(c, 1, "🔥", True)
    print("  付与:", msg.added)
    assert msg.added == ["🔥"]

    db.toggle_reaction(1, 4242, "つるかん", "🔥")   # 解除
    await sync.push_reaction(c, 1, "🔥", False)
    print("  最後の1人が外したら bot も外す:", msg.removed)
    assert msg.removed == ["🔥"]

    db.toggle_reaction(1, 1, "たいち", "👏")
    db.toggle_reaction(1, 2, "やしき", "👏")
    msg.removed.clear()
    db.toggle_reaction(1, 1, "たいち", "👏")        # 1人だけ解除
    await sync.push_reaction(c, 1, "👏", False)
    print("  まだ他に居るなら外さない:", msg.removed)
    assert msg.removed == []

    print("\n=== 二重計上の防止 ===")
    import bot as botmod
    msg.reactions = [FakeReaction("🔥", 3, True), FakeReaction("👏", 2, False),
                     FakeReaction("📼", 1, True)]
    n = botmod.count_reactions(msg)
    print("  🔥3(bot含む) + 👏2 + 📼1 ->", n, "(期待 4)")
    assert n == 4

    print("\nSYNC OK")


if __name__ == "__main__":
    asyncio.run(main())

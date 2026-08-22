"""Activity の動画経路を切り分ける。

VM 上で実行し、CDN の署名付き URL が生きているか、
URL Mappings に登録すべきホストが何かを確かめる。

    .venv/bin/python diagnose_media.py [clip_id]
"""
import asyncio
import sys
from urllib.parse import urlparse

import aiohttp
import discord

import config
import db
import dc


async def main(clip_id: int | None) -> None:
    db.connect()
    if clip_id is None:
        row = db.connect().execute(
            "SELECT id FROM clips ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            print("クリップがありません")
            return
        clip_id = row["id"]

    clip = db.get_clip(clip_id)
    if clip is None:
        print(f"clip {clip_id} が見つかりません")
        return
    print(f"clip {clip_id}: {clip['filename']}\n")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    done = asyncio.Event()

    @client.event
    async def on_ready():
        try:
            url = await dc.fresh_attachment_url(client, clip)
            if not url:
                print("NG  添付URLを取得できません（元メッセージが削除済み？）")
                return
            u = urlparse(url)
            print(f"OK  署名付きURLを取得")
            print(f"    ホスト  : {u.netloc}")
            print(f"    パス    : {u.path}")
            print(f"    クエリ  : {'あり' if u.query else 'なし'}"
                  f"（{len(u.query)} 文字）")
            print()
            print("URL Mappings に登録すべき値:")
            print(f"    Prefix  : /cdn")
            print(f"    Target  : {u.netloc}")
            if u.netloc != "cdn.discordapp.com":
                print()
                print(f"!!  ACTIVITY.md は cdn.discordapp.com を前提にしている。")
                print(f"!!  実際は {u.netloc} なので、こちらを登録すること。")
            print()
            print(f"Activity が実際に叩く URL:")
            print(f"    {config.CDN_PROXY_PREFIX}{u.path}"
                  f"{'?' + u.query if u.query else ''}")
            print()

            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, headers={"Range": "bytes=0-1023"}) as r:
                    print(f"直接取得（Range 0-1023） -> {r.status} "
                          f"{r.headers.get('Content-Type')} "
                          f"{r.headers.get('Content-Range', '')}")
                    if r.status in (200, 206):
                        print("OK  CDN 側は正常。再生できないならマッピングの問題。")
                    else:
                        print("NG  CDN が拒否している。URL の期限切れか権限。")
                # クエリを落とすと弾かれるかを確認（プロキシがクエリを
                # 落としている可能性を切り分けるため）
                bare = f"{u.scheme}://{u.netloc}{u.path}"
                async with sess.get(bare) as r:
                    print(f"クエリ無しで取得        -> {r.status} "
                          f"(401/403 なら署名が必須ということ)")
        finally:
            done.set()
            await client.close()

    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN が .env にありません")
    await client.start(config.TOKEN)
    await done.wait()


if __name__ == "__main__":
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(cid))

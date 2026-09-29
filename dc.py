import asyncio
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import discord

# Discord の添付URLは署名付きで、ex=（16進の期限）まで使える。
# 毎回メッセージを取り直すと 1 本あたり数百ミリ秒かかり、
# スワイプのたびに待たされるので、期限の少し前まで覚えておく。
_cache: dict[tuple[int, int], tuple[str, float]] = {}
_inflight: dict[tuple[int, int], asyncio.Future] = {}
MARGIN = 600
DEFAULT_TTL = 3600


def _expiry(url: str) -> float:
    try:
        ex = parse_qs(urlparse(url).query).get("ex", [""])[0]
        if ex:
            return int(ex, 16) - MARGIN
    except ValueError:
        pass
    return time.time() + DEFAULT_TTL


def forget(clip) -> None:
    _cache.pop((clip["message_id"], clip["attachment_id"]), None)


async def _fetch(client: discord.Client, clip) -> Optional[str]:
    channel = client.get_channel(clip["channel_id"])
    if channel is None:
        try:
            channel = await client.fetch_channel(clip["channel_id"])
        except discord.HTTPException:
            return None
    try:
        msg = await channel.fetch_message(clip["message_id"])
    except discord.HTTPException:
        return None
    for att in msg.attachments:
        if att.id == clip["attachment_id"]:
            return att.url
    return msg.attachments[0].url if msg.attachments else None


async def fresh_attachment_url(client: discord.Client, clip) -> Optional[str]:
    key = (clip["message_id"], clip["attachment_id"])
    hit = _cache.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    # 同じクリップへの同時リクエスト（詳細と動画など）は 1 回の取得にまとめる
    if key in _inflight:
        return await _inflight[key]
    fut = asyncio.get_running_loop().create_future()
    _inflight[key] = fut
    try:
        url = await _fetch(client, clip)
        if url:
            _cache[key] = (url, _expiry(url))
        fut.set_result(url)
        return url
    except asyncio.CancelledError:
        fut.cancel()
        raise
    except Exception as e:
        fut.set_exception(e)
        fut.exception()
        raise
    finally:
        _inflight.pop(key, None)

import logging
from typing import Optional

import discord

import config
import db

log = logging.getLogger("sync")

WEBHOOK_NAME = "ClipVault"


def _clip_title(clip) -> str:
    from pathlib import Path
    return clip["title"] or Path(clip["filename"]).stem


async def _message_for(client: discord.Client, clip) -> Optional[discord.Message]:
    channel = client.get_channel(clip["channel_id"])
    if channel is None:
        try:
            channel = await client.fetch_channel(clip["channel_id"])
        except discord.HTTPException:
            return None
    try:
        return await channel.fetch_message(clip["message_id"])
    except discord.HTTPException:
        return None


async def ensure_thread(client: discord.Client, clip) -> Optional[discord.Thread]:
    """クリップのメッセージにぶら下がるスレッドを用意する。"""
    if clip["thread_id"]:
        th = client.get_channel(clip["thread_id"])
        if isinstance(th, discord.Thread):
            return th
        try:
            th = await client.fetch_channel(clip["thread_id"])
            if isinstance(th, discord.Thread):
                return th
        except discord.HTTPException:
            log.info("thread %s が見つからないので作り直します", clip["thread_id"])

    msg = await _message_for(client, clip)
    if msg is None:
        return None
    if msg.thread is not None:
        db.set_thread(clip["id"], msg.thread.id)
        return msg.thread
    try:
        th = await msg.create_thread(name=_clip_title(clip)[:90] or "clip",
                                     auto_archive_duration=10080)
    except discord.Forbidden:
        log.warning("スレッドを作れません。'公開スレッドの作成' 権限を付けてください")
        return None
    except discord.HTTPException as e:
        log.warning("create_thread failed: %s", e)
        return None
    db.set_thread(clip["id"], th.id)
    return th


async def ensure_webhook(client: discord.Client, channel_id: int):
    """本人名義で投稿するための Webhook。チャンネルごとに1本を使い回す。"""
    row = db.get_webhook(channel_id)
    if row:
        try:
            return discord.Webhook.partial(row["webhook_id"], row["token"],
                                           client=client)
        except Exception:
            db.drop_webhook(channel_id)

    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    try:
        for wh in await channel.webhooks():
            if wh.name == WEBHOOK_NAME and wh.token:
                db.save_webhook(channel_id, wh.id, wh.token)
                return wh
        wh = await channel.create_webhook(name=WEBHOOK_NAME)
    except discord.Forbidden:
        log.warning("Webhook を作れません。'ウェブフックの管理' 権限を付けてください")
        return None
    except discord.HTTPException as e:
        log.warning("webhook error: %s", e)
        return None
    db.save_webhook(channel_id, wh.id, wh.token)
    return wh


def _stamp(at: Optional[float]) -> str:
    if at is None:
        return ""
    s = int(at)
    return f"`{s // 60}:{s % 60:02d}` "


async def push_comment(client: discord.Client, clip_id: int, comment_id: int,
                       user_id: int, user_name: str, body: str,
                       at: Optional[float]) -> None:
    """Web で書かれたコメントを Discord のスレッドへ流す。"""
    if not config.SYNC_COMMENTS:
        return
    clip = db.get_clip(clip_id)
    if clip is None:
        return

    thread = await ensure_thread(client, clip)
    if thread is None:
        return
    wh = await ensure_webhook(client, clip["channel_id"])
    if wh is None:
        return

    avatar = None
    guild = client.get_guild(clip["guild_id"])
    if guild:
        member = guild.get_member(user_id)
        if member:
            avatar = member.display_avatar.url
            user_name = member.display_name

    try:
        sent = await wh.send(content=_stamp(at) + body, username=user_name[:80],
                             avatar_url=avatar, thread=thread, wait=True,
                             allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.warning("webhook send failed: %s", e)
        return
    db.link_comment(comment_id, sent.id)
    log.info("comment %s -> thread %s", comment_id, thread.id)


async def pull_comment(message: discord.Message, client=None) -> bool:
    """Discord のスレッドに直接書かれたコメントを取り込む。"""
    if not config.SYNC_COMMENTS:
        return False
    if not isinstance(message.channel, discord.Thread):
        return False
    if message.webhook_id:          # 自分が流したものは取り込まない
        return False
    clip = db.clip_by_thread(message.channel.id)
    if clip is None:
        return False
    body = message.content.strip()
    if not body:
        return False
    cid = db.add_comment(clip["id"], message.author.id,
                         message.author.display_name, body, None)
    db.link_comment(cid, message.id)
    log.info("thread %s -> comment %s", message.channel.id, cid)
    import notify
    notify.emit(client, clip["uploader_id"], "comment", message.author.id,
                message.author.display_name, clip_id=clip["id"], comment_id=cid, body=body)
    return True


async def push_reaction(client: discord.Client, clip_id: int, emoji: str,
                        added: bool) -> None:
    """Web のリアクションを Discord のメッセージにも映す。

    Discord API には「ユーザーの代理で付ける」手段が無いので bot 名義になる。
    二重計上を避けるため、集計側では bot 自身の分を差し引いている。
    """
    if not config.SYNC_REACTIONS:
        return
    clip = db.get_clip(clip_id)
    if clip is None:
        return
    msg = await _message_for(client, clip)
    if msg is None:
        return
    try:
        if added:
            await msg.add_reaction(emoji)
        elif not db.clip_reactions(clip_id).get(emoji):
            await msg.remove_reaction(emoji, client.user)
    except discord.Forbidden:
        log.warning("リアクションを操作できません。'リアクションの追加' 権限が必要です")
    except discord.HTTPException as e:
        log.debug("reaction sync skipped: %s", e)

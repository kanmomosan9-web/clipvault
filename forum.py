"""掲示板 ⇄ Discord フォーラムチャンネルの同期。

Web → Discord
  書き込みは Webhook で本人の名前とアイコンを使って投稿する。
  クリップは元の投稿を bot が「転送」する（Webhook は転送できないため bot 名義）。
Discord → Web
  フォーラムに直接書かれたものを取り込む。転送・メッセージリンクは
  どのクリップかを判別してクリップとして、動画ファイルはそのまま再生できる形で載せる。
"""
import logging
import re
from pathlib import Path
from typing import Optional

import discord

import board
import config
import db
import notify
import sync

log = logging.getLogger("forum")

LINK = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)")
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def enabled() -> bool:
    return bool(config.FORUM_CHANNEL_ID)


def _title(clip) -> str:
    return clip["title"] or Path(clip["filename"]).stem


async def _channel(client, channel_id: int):
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return ch


def _who(client, user_id: int, fallback: str) -> tuple[str, Optional[str]]:
    guild = client.get_guild(config.GUILD_ID) if config.GUILD_ID else None
    member = guild.get_member(user_id) if guild else None
    if member:
        return member.display_name[:80], member.display_avatar.url
    return fallback[:80], None


# ---------------------------------------------------------------- Web → Discord

async def push_post(client, post_id: int) -> None:
    """Web で書かれた書き込みを Discord に送る。スレッドが無ければ作る。"""
    if not enabled() or client is None:
        return
    post = board.get_post(post_id)
    if post is None:
        return
    thread = board.get_thread(post["thread_id"])
    if thread is None:
        return
    wh = await sync.ensure_webhook(client, config.FORUM_CHANNEL_ID)
    if wh is None:
        return
    name, avatar = _who(client, post["user_id"], post["user_name"])
    clip = db.get_clip(post["clip_id"]) if post["clip_id"] else None
    content = post["body"] or (f"🎬 クリップを共有：{_title(clip)}" if clip else "（動画）")
    none = discord.AllowedMentions.none()
    try:
        if thread["discord_thread_id"]:
            sent = await wh.send(content=content, username=name, avatar_url=avatar,
                                 thread=discord.Object(thread["discord_thread_id"]),
                                 wait=True, allowed_mentions=none)
        else:
            sent = await wh.send(content=content, username=name, avatar_url=avatar,
                                 thread_name=thread["title"][:100], wait=True,
                                 allowed_mentions=none)
            tid = sent.channel.id if sent.channel else sent.id
            board.set_thread_discord(thread["id"], tid)
            thread = board.get_thread(thread["id"])
    except discord.HTTPException as e:
        log.warning("フォーラムへの投稿に失敗: %s", e)
        return
    board.set_post_discord(post_id, message_id=sent.id)

    if clip is not None:
        await _forward_clip(client, post_id, clip, thread["discord_thread_id"])


async def _forward_clip(client, post_id: int, clip, discord_thread_id: int) -> None:
    src = await sync._message_for(client, clip)
    dest = await _channel(client, discord_thread_id)
    if src is None or dest is None:
        return
    try:
        fwd = await src.forward(dest)
    except discord.Forbidden:
        log.warning("転送できません。bot に保存チャンネルの閲覧とフォーラムへの投稿の権限が必要です")
        return
    except discord.HTTPException as e:
        log.warning("転送に失敗: %s", e)
        return
    board.set_post_discord(post_id, forward_id=fwd.id)


async def edit_post(client, post_id: int) -> None:
    post = board.get_post(post_id)
    if not enabled() or post is None or not post["discord_message_id"]:
        return
    thread = board.get_thread(post["thread_id"])
    wh = await sync.ensure_webhook(client, config.FORUM_CHANNEL_ID)
    if wh is None or thread is None or not thread["discord_thread_id"]:
        return
    try:
        await wh.edit_message(post["discord_message_id"], content=post["body"] or "（削除済み）",
                              thread=discord.Object(thread["discord_thread_id"]))
    except discord.HTTPException as e:
        log.info("Discord 側の編集に失敗（本人が Discord で書いたもの等）: %s", e)


async def delete_post(client, post: dict, thread: dict) -> None:
    """DB から消した後に呼ぶ。Discord 側の対応するメッセージも消す。"""
    if not enabled() or not thread.get("discord_thread_id"):
        return
    ch = await _channel(client, thread["discord_thread_id"])
    if ch is None:
        return
    for mid in (post.get("discord_message_id"), post.get("discord_forward_id")):
        if not mid:
            continue
        try:
            await ch.get_partial_message(mid).delete()
        except discord.HTTPException as e:
            log.info("Discord 側の削除に失敗: %s", e)


async def rename_thread(client, thread_id: int) -> None:
    t = board.get_thread(thread_id)
    if not enabled() or t is None or not t["discord_thread_id"]:
        return
    ch = await _channel(client, t["discord_thread_id"])
    if ch is not None:
        try:
            await ch.edit(name=t["title"][:100])
        except discord.HTTPException as e:
            log.warning("スレッド名の変更に失敗（'スレッドの管理' 権限が必要）: %s", e)


async def delete_thread(client, discord_thread_id: Optional[int]) -> None:
    if not enabled() or not discord_thread_id:
        return
    ch = await _channel(client, discord_thread_id)
    if ch is not None:
        try:
            await ch.delete()
        except discord.HTTPException as e:
            log.warning("スレッドの削除に失敗（'スレッドの管理' 権限が必要）: %s", e)


# ---------------------------------------------------------------- Discord → Web

def clip_in(message) -> Optional[object]:
    """転送やメッセージリンクから、どのクリップかを探す。"""
    ref = getattr(message, "reference", None)
    if ref is not None and ref.message_id and \
            getattr(ref, "type", None) == discord.MessageReferenceType.forward:
        clip = db.clip_by_message(ref.message_id)
        if clip:
            return clip
    for m in LINK.finditer(message.content or ""):
        clip = db.clip_by_message(int(m.group(2)))
        if clip:
            return clip
    return None


def video_in(message) -> Optional[dict]:
    for att in getattr(message, "attachments", []) or []:
        ctype = (att.content_type or "").lower()
        if ctype.startswith("video/") or att.filename.lower().endswith(VIDEO_EXT):
            return {"channel_id": message.channel.id, "message_id": message.id,
                    "id": att.id, "name": att.filename}
    return None


def clean_body(message, clip) -> str:
    text = message.content or ""
    if clip is not None:
        text = LINK.sub("", text)
    return text.strip()


def _is_forum_thread(channel) -> bool:
    return isinstance(channel, discord.Thread) and enabled() and \
        channel.parent_id == config.FORUM_CHANNEL_ID


async def pull(client, message) -> bool:
    """フォーラムの書き込みを取り込む。フォーラムのメッセージなら True（他の処理はしない）。"""
    if not _is_forum_thread(message.channel):
        return False
    wh = db.get_webhook(config.FORUM_CHANNEL_ID)
    if message.webhook_id and wh and message.webhook_id == wh["webhook_id"]:
        return True                              # 自分が Web から流したもの
    if client.user and message.author.id == client.user.id:
        return True                              # bot の転送
    if board.post_by_discord(message.id):
        return True

    th = message.channel
    thread = board.thread_by_discord(th.id)
    if thread is None:
        owner = th.owner_id or message.author.id
        name = message.author.display_name if owner == message.author.id else \
            getattr(th.owner, "display_name", None) or message.author.display_name
        tid = board.create_thread(th.name, owner, name, discord_thread_id=th.id)
        thread = board.get_thread(tid)
        log.info("Discord でスレッドが立ちました: %s", th.name)

    clip = clip_in(message)
    att = None if clip else video_in(message)
    body = clean_body(message, clip)
    if not body and clip is None and att is None:
        return True
    before = board.participants(thread["id"])
    pid = board.add_post(thread["id"], message.author.id, message.author.display_name,
                         body, clip_id=clip["id"] if clip else None, att=att,
                         discord_message_id=message.id)
    notify.on_board_post(client, thread["id"], pid, message.author.id,
                         message.author.display_name, body,
                         clip["id"] if clip else None, before)
    return True


def pull_edit(message_id: int, content: Optional[str]) -> bool:
    if content is None:
        return False
    post = board.post_by_discord(message_id)
    if post is None or post["discord_message_id"] != message_id:
        return False
    if (post["body"] or "") == content.strip():
        return True
    board.edit_post(post["id"], content)
    return True


def pull_delete(message_id: int) -> bool:
    post = board.post_by_discord(message_id)
    if post is None:
        return False
    if post["discord_forward_id"] == message_id and post["discord_message_id"] != message_id:
        board.clear_forward(post["id"])          # 転送だけ消された
        return True
    thread = board.get_thread(post["thread_id"])
    if thread and thread["discord_thread_id"] == message_id:
        board.delete_thread(thread["id"])        # 最初の書き込みごと消された
        return True
    board.delete_post(post["id"])
    return True


def pull_thread_rename(discord_thread_id: int, name: str) -> None:
    t = board.thread_by_discord(discord_thread_id)
    if t and t["title"] != name:
        board.rename_thread(t["id"], name)


def pull_thread_delete(discord_thread_id: int) -> None:
    t = board.thread_by_discord(discord_thread_id)
    if t:
        board.delete_thread(t["id"])

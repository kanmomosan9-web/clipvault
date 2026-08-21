import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import api
import sync
import webui
import config
import db
import grid
import media

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clipvault")

BUILD = "2026-08-21t (fix player render)"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix="!cv ", intents=intents)


def is_video(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("video/"):
        return True
    return Path(att.filename).suffix.lower() in config.VIDEO_EXTS


INGEST_MARK = "📼"


def count_reactions(message: discord.Message) -> int:
    """Discord 側のリアクション数。

    Web から付けたぶんは bot 名義でメッセージにも付くため、
    そのまま数えると DB 側と二重計上になる。bot 自身の分は差し引く。
    """
    total = 0
    for r in message.reactions:
        if str(r.emoji) == INGEST_MARK:
            continue
        total += max(r.count - (1 if r.me else 0), 0)
    return total


async def ingest_message(message: discord.Message) -> list[int]:
    created = []
    reactions = count_reactions(message)
    for att in message.attachments:
        if not is_video(att):
            continue
        if db.clip_by_attachment(message.id, att.id):
            continue

        meta = await asyncio.to_thread(media.parse_filename, att.filename)
        probed = await asyncio.to_thread(media.probe, att.url)

        clip_id = db.insert_clip(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            attachment_id=att.id,
            filename=att.filename,
            title=meta["title"],
            uploader_id=message.author.id,
            uploader_name=message.author.display_name,
            duration=probed["duration"],
            width=probed["width"],
            height=probed["height"],
            size_bytes=att.size,
            recorded_at=meta["recorded_at"],
            created_at=message.created_at.astimezone(timezone.utc).isoformat(),
            reactions=reactions,
        )
        if clip_id is None:
            continue

        thumb = await asyncio.to_thread(
            media.extract_thumb, att.url,
            config.THUMB_DIR / f"{clip_id}.jpg", probed["duration"],
        )
        if thumb:
            db.update_clip(clip_id, thumb_path=str(thumb))

        auto = []
        if meta["game"]:
            db.set_tags(clip_id, [meta["game"]], category="game")
            auto.append(meta["game"])
        for m in message.mentions:
            db.add_tags(clip_id, [m.display_name], category="player")
            auto.append(m.display_name)

        db.reindex(clip_id)
        if config.AUTO_TRANSCRIBE:
            db.enqueue(clip_id)
        created.append(clip_id)
        log.info("ingested clip %s (%s)", clip_id, att.filename)
    return created


_api_runner = None


@bot.event
async def on_ready():
    global _api_runner
    db.connect()
    if config.API_ENABLED and _api_runner is None:
        try:
            _api_runner = await api.start(bot)
        except Exception as e:
            log.error("job API failed to start: %s", e)
    if not media.check_ffmpeg():
        log.warning("ffmpeg / ffprobe not found on PATH - thumbnails will be skipped")
    try:
        if config.GUILD_ID:
            g = discord.Object(id=config.GUILD_ID)
            bot.tree.copy_global_to(guild=g)
            synced = await bot.tree.sync(guild=g)
        else:
            synced = await bot.tree.sync()
        log.info("synced %d commands: %s",
                 len(synced), ", ".join("/" + c.name for c in synced))
    except Exception as e:
        log.error("command sync failed: %s", e)
    log.info("BUILD %s", BUILD)
    log.info("logged in as %s | storage channels: %s", bot.user, config.STORAGE_CHANNEL_IDS)


async def on_web_comment(clip_id, comment_id, user_id, user_name, body, at):
    await sync.push_comment(bot, clip_id, comment_id, user_id, user_name, body, at)


async def on_web_reaction(clip_id, emoji, added):
    await sync.push_reaction(bot, clip_id, emoji, added)


bot.on_web_comment = on_web_comment
bot.on_web_reaction = on_web_reaction


@bot.event
async def on_message(message: discord.Message):
    if not message.guild:
        return
    if await sync.pull_comment(message):
        return
    if message.author.bot:
        return
    if message.channel.id in config.STORAGE_CHANNEL_IDS:
        ids = await ingest_message(message)
        if ids:
            try:
                await message.add_reaction(INGEST_MARK)
            except discord.Forbidden:
                log.warning(
                    "cannot add %s in #%s - grant the bot 'Add Reactions' permission",
                    INGEST_MARK, message.channel.name)
            except discord.HTTPException as e:
                log.warning("add_reaction failed: %s", e)
    await bot.process_commands(message)


async def _sync_reactions(message_id: int, channel_id: int) -> bool:
    if not db.clip_by_message(message_id):
        return False
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return False
    try:
        msg = await channel.fetch_message(message_id)
    except discord.Forbidden:
        log.warning("cannot read #%s - grant 'Read Message History'", channel_id)
        return False
    except discord.HTTPException:
        return False
    db.set_reactions(message_id, count_reactions(msg))
    return True


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    # スレッド側でコメントが消されたら DB からも消す
    if db.delete_comment_by_message(payload.message_id):
        log.info("thread message %s deleted -> comment removed", payload.message_id)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await _sync_reactions(payload.message_id, payload.channel_id)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await _sync_reactions(payload.message_id, payload.channel_id)


def transcript_excerpt(clip_id: int, highlight: str = "", lines: int = 6,
                       max_chars: int = 1000) -> str:
    segs = db.get_segments(clip_id)
    if not segs:
        return ""
    hit = [s for s in segs if highlight and highlight in s["text"]]
    picked = (hit or segs)[:lines]
    body = "\n".join(f"-# `{media.fmt_duration(s['start'])}` {s['text']}"
                     for s in picked)[:max_chars]
    rest = len(hit or segs) - len(picked)
    if rest > 0:
        body += f"\n-# … 他 {rest} 行"
    return body


class EditModal(discord.ui.Modal, title="クリップ情報を編集"):
    def __init__(self, clip_id: int, parent: "ClipView"):
        super().__init__()
        self.clip_id = clip_id
        self.parent_view = parent
        clip = db.get_clip(clip_id)
        tags = ", ".join(t["display"] for t in db.get_tags(clip_id))
        self.f_title = discord.ui.TextInput(
            label="タイトル", required=False, max_length=100,
            default=clip["title"] or "")
        self.f_tags = discord.ui.TextInput(
            label="タグ（カンマ区切り）", required=False, max_length=200,
            placeholder="LoL, pentakill, ADC", default=tags)
        self.f_note = discord.ui.TextInput(
            label="メモ", required=False, max_length=400,
            style=discord.TextStyle.paragraph, default=clip["note"] or "")
        for f in (self.f_title, self.f_tags, self.f_note):
            self.add_item(f)

    async def on_submit(self, interaction: discord.Interaction):
        db.update_clip(self.clip_id,
                       title=self.f_title.value.strip(),
                       note=self.f_note.value.strip())
        names = [t for t in self.f_tags.value.replace("、", ",").split(",") if t.strip()]
        db.set_tags(self.clip_id, names)
        await self.parent_view.show(interaction)


class ClipView(discord.ui.View):
    def __init__(self, clip_id: int, back: Optional["GridView"] = None,
                 highlight: str = ""):
        super().__init__(timeout=300)
        self.clip_id = clip_id
        self.back = back
        self.highlight = highlight

    def _transcript_field(self) -> Optional[tuple[str, str]]:
        segs = db.get_segments(self.clip_id)
        if not segs:
            return None
        hit = any(self.highlight and self.highlight in s["text"] for s in segs)
        name = "🔍 該当箇所" if hit else "🎙 文字起こし"
        body = transcript_excerpt(self.clip_id, self.highlight, lines=6)
        return (name, body) if body else None

    async def _embed(self) -> tuple[discord.Embed, Optional[str]]:
        clip = db.get_clip(self.clip_id)
        tags = db.get_tags(self.clip_id)
        title = clip["title"] or Path(clip["filename"]).stem

        e = discord.Embed(title=f"🎬 {title}", colour=0xE50914)
        if tags:
            e.description = " ".join(f"`#{t['display']}`" for t in tags)
        e.add_field(name="長さ", value=media.fmt_duration(clip["duration"]))
        e.add_field(name="解像度", value=f"{clip['width']}x{clip['height']}")
        e.add_field(name="サイズ", value=media.fmt_size(clip["size_bytes"]))
        e.add_field(name="投稿者", value=clip["uploader_name"])
        e.add_field(name="評価", value=f"★ {clip['reactions']}")
        e.add_field(name="録画日時", value=(clip["recorded_at"] or "-")[:19].replace("T", " "))
        if clip["note"]:
            e.add_field(name="メモ", value=clip["note"][:900], inline=False)
        tf = self._transcript_field()
        if tf:
            e.add_field(name=tf[0], value=tf[1], inline=False)
        e.set_footer(text=f"ID {clip['id']} · {clip['filename']}")

        fresh = None
        channel = bot.get_channel(clip["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(clip["message_id"])
                for att in msg.attachments:
                    if att.id == clip["attachment_id"] or len(msg.attachments) == 1:
                        fresh = att.url
                        break
            except discord.HTTPException:
                pass
        if clip["thumb_path"] and Path(clip["thumb_path"]).exists():
            e.set_image(url="attachment://thumb.jpg")
        return e, fresh

    def _build(self, clip, fresh: Optional[str]):
        self.clear_items()
        jump = f"https://discord.com/channels/{clip['guild_id']}/{clip['channel_id']}/{clip['message_id']}"
        self.add_item(discord.ui.Button(label="▶ Discordで再生", url=jump, row=0))
        if fresh:
            self.add_item(discord.ui.Button(label="⬇ ダウンロード", url=fresh, row=0))

        edit = discord.ui.Button(label="🏷 編集", style=discord.ButtonStyle.primary, row=1)

        async def on_edit(interaction: discord.Interaction):
            await interaction.response.send_modal(EditModal(self.clip_id, self))
        edit.callback = on_edit
        self.add_item(edit)

        if self.back is not None:
            back = discord.ui.Button(label="◀ 一覧に戻る", style=discord.ButtonStyle.secondary, row=1)

            async def on_back(interaction: discord.Interaction):
                await self.back.show(interaction)
            back.callback = on_back
            self.add_item(back)

    async def show(self, interaction: discord.Interaction):
        clip = db.get_clip(self.clip_id)
        embed, fresh = await self._embed()
        self._build(clip, fresh)
        files = []
        if clip["thumb_path"] and Path(clip["thumb_path"]).exists():
            files.append(discord.File(clip["thumb_path"], filename="thumb.jpg"))
        kwargs = {"embed": embed, "view": self, "attachments": files}
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)


class GridView(discord.ui.View):
    def __init__(self, *, query: str = "", tag_ids: Optional[list[int]] = None,
                 uploader_id: Optional[int] = None, sort: str = "new"):
        super().__init__(timeout=600)
        self.query = query
        self.tag_ids = tag_ids or []
        self.uploader_id = uploader_id
        self.sort = sort
        self.page = 0
        self.rows: list = []
        self.total = 0

    def heading(self) -> str:
        parts = []
        if self.query:
            parts.append(f'検索 "{self.query}"')
        if self.tag_ids:
            c = db.connect()
            marks = ", ".join("?" for _ in self.tag_ids)
            names = [r["display"] for r in c.execute(
                f"SELECT display FROM tags WHERE id IN ({marks})", self.tag_ids)]
            parts.append(" ".join("#" + n for n in names))
        if self.uploader_id:
            parts.append("投稿者フィルタ")
        base = "クリップライブラリ" if not parts else " / ".join(parts)
        return f"{base}   —   {db.SORTS[self.sort][1]}"

    def fetch(self):
        self.rows, self.total = db.query_clips(
            query=self.query, tag_ids=self.tag_ids, uploader_id=self.uploader_id,
            sort=self.sort, offset=self.page * config.PAGE_SIZE, limit=config.PAGE_SIZE,
        )
        pages = max(1, (self.total + config.PAGE_SIZE - 1) // config.PAGE_SIZE)
        if self.page >= pages:
            self.page = pages - 1
            self.rows, self.total = db.query_clips(
                query=self.query, tag_ids=self.tag_ids, uploader_id=self.uploader_id,
                sort=self.sort, offset=self.page * config.PAGE_SIZE, limit=config.PAGE_SIZE,
            )

    def build(self):
        self.clear_items()

        for i, row in enumerate(self.rows):
            btn = discord.ui.Button(label=str(i + 1), style=discord.ButtonStyle.secondary,
                                    row=0 if i < 5 else 1)

            async def cb(interaction: discord.Interaction, clip_id=row["id"]):
                await ClipView(clip_id, back=self, highlight=self.query).show(interaction)
            btn.callback = cb
            self.add_item(btn)

        pages = max(1, (self.total + config.PAGE_SIZE - 1) // config.PAGE_SIZE)

        prev = discord.ui.Button(label="◀ 前", style=discord.ButtonStyle.primary,
                                 row=2, disabled=self.page == 0)
        nxt = discord.ui.Button(label="次 ▶", style=discord.ButtonStyle.primary,
                                row=2, disabled=self.page >= pages - 1)
        reset = discord.ui.Button(label="🔄 条件クリア", style=discord.ButtonStyle.secondary, row=2)
        listbtn = discord.ui.Button(label="📋 リスト表示", style=discord.ButtonStyle.success, row=2)

        async def on_prev(interaction):
            self.page -= 1
            await self.show(interaction)

        async def on_next(interaction):
            self.page += 1
            await self.show(interaction)

        async def on_reset(interaction):
            self.query, self.tag_ids, self.uploader_id, self.page = "", [], None, 0
            await self.show(interaction)

        async def on_list(interaction):
            await ListView(query=self.query, tag_ids=self.tag_ids,
                           uploader_id=self.uploader_id,
                           sort=self.sort).open_new(interaction)

        prev.callback, nxt.callback = on_prev, on_next
        reset.callback, listbtn.callback = on_reset, on_list
        for b in (prev, nxt, reset, listbtn):
            self.add_item(b)

        tags = db.popular_tags(25)
        if tags:
            opts = [discord.SelectOption(label=f"#{t['display']}", value=str(t["id"]),
                                         description=f"{t['use_count']}件",
                                         default=t["id"] in self.tag_ids) for t in tags]
            sel = discord.ui.Select(placeholder="🏷 タグで絞り込む（複数可）", row=3,
                                    min_values=0, max_values=min(len(opts), 5), options=opts)

            async def on_tag(interaction):
                self.tag_ids = [int(v) for v in sel.values]
                self.page = 0
                await self.show(interaction)
            sel.callback = on_tag
            self.add_item(sel)

        sort_opts = [discord.SelectOption(label=label, value=key, default=key == self.sort)
                     for key, (_, label) in db.SORTS.items()]
        ssel = discord.ui.Select(placeholder="↕ 並び替え", row=4, options=sort_opts)

        async def on_sort(interaction):
            self.sort = ssel.values[0]
            self.page = 0
            await self.show(interaction)
        ssel.callback = on_sort
        self.add_item(ssel)

    async def render_file(self) -> discord.File:
        data = await asyncio.to_thread(
            grid.render, self.rows, self.page, self.total, self.heading())
        return discord.File(fp=io.BytesIO(data), filename="grid.png")

    async def open_new(self, interaction: discord.Interaction):
        self.fetch()
        self.build()
        if self.total == 0:
            await interaction.response.send_message(
                "該当するクリップがありません。", ephemeral=True)
            return
        e = discord.Embed(colour=0xE50914)
        e.set_image(url="attachment://grid.png")
        e.set_footer(text="番号ボタンで開く / セレクトで絞り込み・並び替え")
        await interaction.response.send_message(
            embed=e, file=await self.render_file(), view=self, ephemeral=True)

    async def show(self, interaction: discord.Interaction, first: bool = False):
        if not interaction.response.is_done():
            if first:
                await interaction.response.defer(ephemeral=True)
            else:
                await interaction.response.defer()
        self.fetch()
        self.build()

        if self.total == 0:
            await interaction.edit_original_response(
                content="該当するクリップがありません。", attachments=[], embed=None, view=self)
            return

        e = discord.Embed(colour=0xE50914)
        e.set_image(url="attachment://grid.png")
        e.set_footer(text="番号ボタンで開く / セレクトで絞り込み・並び替え")
        await interaction.edit_original_response(
            content=None, embed=e, attachments=[await self.render_file()], view=self)


class ListView(discord.ui.LayoutView):
    def __init__(self, *, query: str = "", tag_ids: Optional[list[int]] = None,
                 uploader_id: Optional[int] = None, sort: str = "new", page: int = 0):
        super().__init__(timeout=600)
        self.query = query
        self.tag_ids = tag_ids or []
        self.uploader_id = uploader_id
        self.sort = sort
        self.page = page
        self.rows: list = []
        self.total = 0
        self.expanded: set[int] = set()

    def state(self) -> dict:
        return {"query": self.query, "tag_ids": self.tag_ids,
                "uploader_id": self.uploader_id, "sort": self.sort}

    def fetch(self):
        self.rows, self.total = db.query_clips(
            **self.state(), offset=self.page * config.LIST_PAGE_SIZE,
            limit=config.LIST_PAGE_SIZE)
        pages = max(1, (self.total + config.LIST_PAGE_SIZE - 1) // config.LIST_PAGE_SIZE)
        if self.page >= pages:
            self.page = pages - 1
            self.rows, self.total = db.query_clips(
                **self.state(), offset=self.page * config.LIST_PAGE_SIZE,
                limit=config.LIST_PAGE_SIZE)

    def build(self) -> list[discord.File]:
        self.clear_items()
        files: list[discord.File] = []
        box = discord.ui.Container(accent_colour=0xE50914)

        pages = max(1, (self.total + config.LIST_PAGE_SIZE - 1) // config.LIST_PAGE_SIZE)
        head = "クリップライブラリ"
        if self.query:
            head = f'検索 "{self.query}"'
        box.add_item(discord.ui.TextDisplay(
            f"## 🎬 {head}\n{self.total} 件 · {self.page + 1}/{pages} ページ · "
            f"{db.SORTS[self.sort][1]}"))

        for i, row in enumerate(self.rows):
            box.add_item(discord.ui.Separator())

            title = row["title"] or Path(row["filename"]).stem
            tags = db.get_tags(row["id"])
            tagline = " ".join(f"`#{t['display']}`" for t in tags) or "`タグ未設定`"
            body = (f"**{title}**\n{tagline}\n-# {media.fmt_duration(row['duration'])} · "
                    f"★{row['reactions']} · {row['uploader_name']} · "
                    f"{str(row['created_at'])[:10]}")

            segs = db.get_segments(row["id"])
            is_open = row["id"] in self.expanded
            matched = [x for x in segs if self.query and self.query in x["text"]]

            if is_open:
                body += "\n" + transcript_excerpt(
                    row["id"], self.query, lines=config.LIST_TRANSCRIPT_LINES)
            elif matched:
                body += "\n" + transcript_excerpt(row["id"], self.query, lines=1)

            files.append(discord.File(
                io.BytesIO(grid.tile_bytes(row, config.LIST_THUMB_WIDTH)),
                filename=f"t{i}.jpg"))
            box.add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem(media=f"attachment://t{i}.jpg",
                                         description=title)))
            box.add_item(discord.ui.TextDisplay(body))

            bar = discord.ui.ActionRow()
            jump = (f"https://discord.com/channels/{row['guild_id']}"
                    f"/{row['channel_id']}/{row['message_id']}")
            bar.add_item(discord.ui.Button(label="▶ 再生", url=jump))

            edit = discord.ui.Button(label="🏷 編集", style=discord.ButtonStyle.primary)

            async def on_edit(interaction: discord.Interaction, clip_id=row["id"]):
                await interaction.response.send_modal(EditModal(clip_id, self))
            edit.callback = on_edit
            bar.add_item(edit)

            if segs:
                toggle = discord.ui.Button(
                    label="🎙 閉じる" if is_open else f"🎙 文字起こし ({len(segs)})",
                    style=discord.ButtonStyle.secondary)

                async def on_toggle(interaction: discord.Interaction, clip_id=row["id"]):
                    self.expanded.symmetric_difference_update({clip_id})
                    await self.show(interaction)
                toggle.callback = on_toggle
                bar.add_item(toggle)

            box.add_item(bar)

        box.add_item(discord.ui.Separator())
        nav = discord.ui.ActionRow()

        prev = discord.ui.Button(label="◀ 前", style=discord.ButtonStyle.secondary,
                                 disabled=self.page == 0)
        nxt = discord.ui.Button(label="次 ▶", style=discord.ButtonStyle.secondary,
                                disabled=self.page >= pages - 1)
        grid_btn = discord.ui.Button(label="🔲 グリッド表示",
                                     style=discord.ButtonStyle.success)

        async def on_prev(interaction):
            self.page -= 1
            await self.show(interaction)

        async def on_next(interaction):
            self.page += 1
            await self.show(interaction)

        async def on_grid(interaction):
            await GridView(**self.state()).open_new(interaction)

        prev.callback, nxt.callback, grid_btn.callback = on_prev, on_next, on_grid
        for b in (prev, nxt, grid_btn):
            nav.add_item(b)
        box.add_item(nav)

        self.add_item(box)
        return files

    async def open_new(self, interaction: discord.Interaction):
        self.fetch()
        if self.total == 0:
            await interaction.response.send_message(
                "該当するクリップがありません。", ephemeral=True)
            return
        files = self.build()
        await interaction.response.send_message(view=self, files=files, ephemeral=True)

    async def show(self, interaction: discord.Interaction, first: bool = False):
        self.fetch()
        if self.total == 0:
            msg = "該当するクリップがありません。"
            if first:
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.response.edit_message(
                    content=msg, view=None, attachments=[])
            return

        files = self.build()
        if first:
            await interaction.response.send_message(view=self, files=files, ephemeral=True)
        elif interaction.response.is_done():
            await interaction.edit_original_response(view=self, attachments=files)
        else:
            await interaction.response.edit_message(view=self, attachments=files)


@bot.tree.command(name="clips", description="クリップをグリッド表示します")
@app_commands.describe(tag="タグで絞り込み", uploader="投稿者で絞り込み",
                       sort="並び順", view="表示形式")
@app_commands.choices(
    sort=[app_commands.Choice(name=v[1], value=k) for k, v in db.SORTS.items()],
    view=[app_commands.Choice(name="グリッド（一覧性重視）", value="grid"),
          app_commands.Choice(name="リスト（操作性重視）", value="list")])
async def cmd_clips(interaction: discord.Interaction, tag: Optional[str] = None,
                    uploader: Optional[discord.Member] = None,
                    sort: Optional[app_commands.Choice[str]] = None,
                    view: Optional[app_commands.Choice[str]] = None):
    tag_ids = []
    if tag:
        tid = db.resolve_tag(tag, create=False)
        if tid:
            tag_ids.append(tid)
    state = {"tag_ids": tag_ids,
             "uploader_id": uploader.id if uploader else None,
             "sort": sort.value if sort else "new"}
    mode = view.value if view else config.DEFAULT_VIEW
    if mode == "list":
        await ListView(**state).show(interaction, first=True)
    else:
        await GridView(**state).show(interaction, first=True)


@cmd_clips.autocomplete("tag")
async def tag_autocomplete(interaction: discord.Interaction, current: str):
    cur = db.normalize_tag(current)
    rows = db.connect().execute(
        "SELECT display, use_count FROM tags WHERE name LIKE ? "
        "ORDER BY use_count DESC LIMIT 25", (f"%{cur}%",)).fetchall()
    return [app_commands.Choice(name=f"#{r['display']} ({r['use_count']})", value=r["display"])
            for r in rows]


@bot.tree.command(name="search", description="タイトル・タグ・メモを全文検索します")
async def cmd_search(interaction: discord.Interaction, query: str):
    cls = ListView if config.DEFAULT_VIEW == "list" else GridView
    await cls(query=query).show(interaction, first=True)


@bot.tree.command(name="scan", description="保管チャンネルの過去ログを取り込みます")
@app_commands.describe(
    limit="遡るメッセージ数。動画の本数ではなく会話も含む総数（既定 2000）",
    order="どちらから遡るか")
@app_commands.choices(order=[
    app_commands.Choice(name="新しい順（既定）", value="new"),
    app_commands.Choice(name="古い順", value="old")])
async def cmd_scan(interaction: discord.Interaction, limit: int = 2000,
                   order: Optional[app_commands.Choice[str]] = None):
    await interaction.response.defer(ephemeral=True)
    oldest_first = bool(order and order.value == "old")

    total_new, seen, hit_limit = 0, 0, False
    for cid in config.STORAGE_CHANNEL_IDS:
        channel = bot.get_channel(cid)
        if channel is None:
            continue
        count = 0
        async for msg in channel.history(limit=limit, oldest_first=oldest_first):
            count += 1
            seen += 1
            if not msg.author.bot:
                total_new += len(await ingest_message(msg))
            if seen % 250 == 0:
                try:
                    await interaction.edit_original_response(
                        content=f"走査中… {seen} 件を確認 / {total_new} 件を取り込み")
                except discord.HTTPException:
                    pass
        if count >= limit:
            hit_limit = True

    msg = f"取り込み完了：**{total_new} 件**の新規クリップ（{seen} 件のメッセージを確認）"
    if hit_limit:
        msg += (f"\n\n⚠️ 上限 {limit} 件に達したため、それより"
                f"{'新しい' if oldest_first else '古い'}メッセージは未確認です。\n"
                f"`/scan limit:{limit * 5}` で続きを確認できます"
                f"（重複は自動でスキップされます）。")
    if total_new:
        msg += "\n\nリアクションを反映するには `/resync` を実行してください。"
    await interaction.edit_original_response(content=msg)


@bot.tree.command(name="resync", description="全クリップのリアクション数を数え直します")
async def cmd_resync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    refs = db.message_refs()
    ok = 0
    for i, ref in enumerate(refs):
        if await _sync_reactions(ref["message_id"], ref["channel_id"]):
            ok += 1
        if i % 25 == 24:
            await interaction.edit_original_response(
                content=f"再集計中… {i + 1}/{len(refs)}")
            await asyncio.sleep(1)
    await interaction.edit_original_response(
        content=f"再集計完了：{ok}/{len(refs)} 件のメッセージを更新しました。"
                + ("\n取得できなかったメッセージは、削除済みか権限不足です。"
                   if ok < len(refs) else ""))


@bot.tree.command(name="transcribe", description="文字起こしのジョブを積みます")
@app_commands.describe(target="対象", force="完了済みもやり直す")
@app_commands.choices(target=[
    app_commands.Choice(name="未処理のクリップすべて", value="pending"),
    app_commands.Choice(name="低品質モデルの分を再処理", value="low"),
    app_commands.Choice(name="全クリップ", value="all")])
async def cmd_transcribe(interaction: discord.Interaction,
                         target: Optional[app_commands.Choice[str]] = None,
                         force: bool = False):
    await interaction.response.defer(ephemeral=True)
    mode = target.value if target else "pending"
    if mode == "all":
        ids = [r["id"] for r in db.query_clips(limit=100000)[0]]
    elif mode == "low":
        ids = db.low_quality(config.PREFERRED_MODEL, limit=100000)
        force = True
    else:
        ids = db.untranscribed(limit=100000)
    n = sum(1 for cid in ids if db.enqueue(cid, force=force))
    counts = db.job_counts()
    await interaction.edit_original_response(
        content=f"{n} 件をキューに追加しました（対象 {len(ids)} 件）。\n"
                f"キューの状態: {counts or '空'}\n"
                f"`python worker.py` を起動すると処理が始まります。")


@bot.tree.command(name="jobs", description="文字起こしの進捗を表示します")
async def cmd_jobs(interaction: discord.Interaction):
    counts = db.job_counts()
    total_clips = db.stats()["count"]
    done = db.connect().execute(
        "SELECT COUNT(*) n FROM clips WHERE transcript != ''").fetchone()["n"]

    labels = {"pending": "待機中", "running": "処理中", "done": "完了",
              "failed": "失敗", "empty": "音声なし", "skipped": "スキップ"}
    by_model = db.connect().execute(
        "SELECT transcript_model m, COUNT(*) n FROM clips "
        "WHERE transcript_model != '' GROUP BY m ORDER BY n DESC")
    models = {r["m"]: r["n"] for r in by_model}
    low = len(db.low_quality(config.PREFERRED_MODEL, limit=100000))
    e = discord.Embed(title="🎙 文字起こしの状況", colour=0xE50914)
    e.add_field(name="文字起こし済み", value=f"{done} / {total_clips} 件", inline=False)
    if counts:
        e.add_field(name="キュー",
                    value="\n".join(f"{labels.get(k, k)}: {v}" for k, v in counts.items()),
                    inline=False)
    else:
        e.add_field(name="キュー", value="空", inline=False)

    if models:
        e.add_field(name="使用モデル",
                    value="\n".join(f"`{k}`: {v} 件" for k, v in models.items()),
                    inline=False)
    if low:
        e.add_field(name="再処理待ち",
                    value=f"{low} 件が `{config.PREFERRED_MODEL}` 以外で処理済み\n"
                          f"-# GPUワーカーが空いたときに自動でやり直します",
                    inline=False)

    fails = db.failed_jobs()
    if fails:
        e.add_field(name="直近の失敗",
                    value="\n".join(f"`{f['filename'][:28]}` — {f['error'][:60]}"
                                    for f in fails), inline=False)
    e.set_footer(text="worker.py が起動していないとキューは消化されません")
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="library", description="ブラウザ版のクリップライブラリを開きます")
async def cmd_library(interaction: discord.Interaction):
    if not config.WEB_ENABLED:
        await interaction.response.send_message(
            "Web ライブラリは無効になっています（`WEB_ENABLED=0`）。", ephemeral=True)
        return
    if not config.PUBLIC_URL:
        await interaction.response.send_message(
            "`PUBLIC_URL` が未設定です。VM の `.env` に公開URLを設定してください。",
            ephemeral=True)
        return

    token = webui.new_token()
    db.issue_token(token, interaction.user.id, interaction.user.display_name,
                   interaction.guild_id or 0, days=config.WEB_TOKEN_DAYS)
    url = f"{config.PUBLIC_URL}/?t={token}"

    e = discord.Embed(
        title="🎬 クリップライブラリ",
        description="サムネイル一覧、検索、再生、タグ編集、コメントが使えます。",
        colour=0xC8AA6E)
    e.add_field(name="有効期限", value=f"{config.WEB_TOKEN_DAYS} 日", inline=True)
    e.add_field(name="ログイン", value=interaction.user.display_name, inline=True)
    e.set_footer(text="このリンクはあなた専用です。他の人には共有しないでください。")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="ライブラリを開く", url=url, style=discord.ButtonStyle.link))
    await interaction.response.send_message(embed=e, view=view, ephemeral=True)


@bot.tree.command(name="stats", description="ライブラリの統計を表示します")
async def cmd_stats(interaction: discord.Interaction):
    s = db.stats()
    e = discord.Embed(title="📊 ライブラリ統計", colour=0xE50914)
    e.add_field(name="クリップ数", value=f"{s['count']} 件")
    e.add_field(name="総再生時間", value=media.fmt_duration(s["duration"]))
    e.add_field(name="総容量", value=media.fmt_size(s["size"]))
    tags = db.popular_tags(10)
    if tags:
        e.add_field(name="人気タグ",
                    value=" ".join(f"`#{t['display']}({t['use_count']})`" for t in tags),
                    inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="tagdelete", description="タグを削除します（クリップは消えません）")
@app_commands.describe(tag="削除するタグ名")
async def cmd_tagdelete(interaction: discord.Interaction, tag: str):
    tid = db.resolve_tag(tag, create=False)
    if tid is None:
        await interaction.response.send_message(
            f"`{tag}` というタグは見つかりませんでした。", ephemeral=True)
        return
    n = db.delete_tag(tid)
    await interaction.response.send_message(
        f"タグ `{tag}` を削除しました（{n} 件のクリップから外れました）。", ephemeral=True)


@cmd_tagdelete.autocomplete("tag")
async def tagdelete_autocomplete(interaction: discord.Interaction, current: str):
    cur = db.normalize_tag(current)
    rows = db.connect().execute(
        "SELECT display, use_count FROM tags WHERE name LIKE ? "
        "ORDER BY use_count DESC LIMIT 25", (f"%{cur}%",)).fetchall()
    return [app_commands.Choice(name=f"#{r['display']} ({r['use_count']})", value=r["display"])
            for r in rows]


@bot.tree.command(name="alias", description="タグの別名を登録します（表記ゆれ対策）")
async def cmd_alias(interaction: discord.Interaction, alias: str, tag: str):
    db.add_alias(alias, tag)
    await interaction.response.send_message(
        f"`{alias}` → `#{tag}` として登録しました。", ephemeral=True)


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN が .env に設定されていません")
    bot.run(config.TOKEN)

import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config
import db
from media import fmt_duration

BG = (17, 17, 19)
CARD = (32, 32, 36)
TEXT = (240, 240, 243)
MUTED = (150, 150, 158)
ACCENT = (229, 9, 20)
BADGE_BG = (0, 0, 0)

MARGIN = 24
GAP = 16
CELL_W = 336
THUMB_H = 189
TEXT_H = 74
CELL_H = THUMB_H + TEXT_H
HEADER_H = 62

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}
_font_path: Optional[str] = None


def _resolve_font() -> Optional[str]:
    global _font_path
    if _font_path is not None:
        return _font_path or None
    for p in config.FONT_CANDIDATES:
        if Path(p).exists():
            _font_path = p
            return p
    _font_path = ""
    return None


def font(size: int) -> ImageFont.ImageFont:
    if size in _font_cache:
        return _font_cache[size]
    path = _resolve_font()
    try:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    except Exception:
        f = ImageFont.load_default(size)
    _font_cache[size] = f
    return f


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> str:
    if draw.textlength(text, font=f) <= max_w:
        return text
    out = ""
    for ch in text:
        if draw.textlength(out + ch + "…", font=f) > max_w:
            break
        out += ch
    return out + "…"


def _thumb_image(path: Optional[str], size: Optional[tuple[int, int]] = None) -> Image.Image:
    w, h = size or (CELL_W, THUMB_H)
    canvas = Image.new("RGB", (w, h), CARD)
    if not path or not Path(path).exists():
        d = ImageDraw.Draw(canvas)
        f = font(max(12, round(h / 12)))
        msg = "NO PREVIEW"
        tw = d.textlength(msg, font=f)
        d.text(((w - tw) / 2, h / 2 - 9), msg, fill=MUTED, font=f)
        return canvas
    try:
        src = Image.open(path).convert("RGB")
    except Exception:
        return canvas
    ratio = max(w / src.width, h / src.height)
    new = src.resize((max(1, int(src.width * ratio)), max(1, int(src.height * ratio))),
                     Image.LANCZOS)
    left = (new.width - w) // 2
    top = (new.height - h) // 2
    return new.crop((left, top, left + w, top + h))


def _rounded(img: Image.Image, radius: int = 8) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1],
                                           radius=radius, fill=255)
    out = Image.new("RGB", img.size, BG)
    out.paste(img, (0, 0), mask)
    return out


def tile_bytes(row, width: int = 320) -> bytes:
    path = row["thumb_path"] if not isinstance(row, str) else row
    size = (width, round(width * 9 / 16))
    img = _thumb_image(path, size)

    if not isinstance(row, str):
        d = ImageDraw.Draw(img)
        s = max(13, round(width / 34))
        pad, edge = round(s * 0.45), round(width / 40)

        dur = fmt_duration(row["duration"])
        dw = d.textlength(dur, font=font(s))
        d.rounded_rectangle(
            [img.width - dw - pad * 2 - edge, img.height - s - pad * 2 - edge,
             img.width - edge, img.height - edge], radius=round(s / 3), fill=BADGE_BG)
        d.text((img.width - dw - pad - edge, img.height - s - pad - edge), dur,
               fill=(255, 255, 255), font=font(s))

        if row["reactions"]:
            star = f"★{row['reactions']}"
            sw = d.textlength(star, font=font(s))
            d.rounded_rectangle(
                [edge, img.height - s - pad * 2 - edge,
                 edge + sw + pad * 2, img.height - edge],
                radius=round(s / 3), fill=BADGE_BG)
            d.text((edge + pad, img.height - s - pad - edge), star,
                   fill=(255, 205, 60), font=font(s))

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86)
    return buf.getvalue()


def render(rows, page: int, total: int, heading: str,
           cols: int = config.GRID_COLS, page_size: int = config.PAGE_SIZE) -> bytes:
    grid_rows = (page_size + cols - 1) // cols
    width = MARGIN * 2 + CELL_W * cols + GAP * (cols - 1)
    height = HEADER_H + MARGIN + CELL_H * grid_rows + GAP * (grid_rows - 1) + MARGIN

    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)

    draw.rectangle([0, 0, width, HEADER_H], fill=(11, 11, 13))
    draw.rectangle([MARGIN, 22, MARGIN + 5, 44], fill=ACCENT)
    draw.text((MARGIN + 16, 20), _ellipsize(draw, heading, font(22), width - 340),
              fill=TEXT, font=font(22))

    pages = max(1, (total + page_size - 1) // page_size)
    right = f"{total} clips   /   page {page + 1} of {pages}"
    rw = draw.textlength(right, font=font(15))
    draw.text((width - MARGIN - rw, 26), right, fill=MUTED, font=font(15))

    for i in range(page_size):
        cx = MARGIN + (i % cols) * (CELL_W + GAP)
        cy = HEADER_H + MARGIN + (i // cols) * (CELL_H + GAP)
        draw.rounded_rectangle([cx, cy, cx + CELL_W, cy + CELL_H], radius=10, fill=CARD)

        if i >= len(rows):
            continue
        row = rows[i]

        canvas.paste(_rounded(_thumb_image(row["thumb_path"])), (cx, cy))

        badge = f" {i + 1} "
        bw = draw.textlength(badge, font=font(16)) + 8
        draw.rounded_rectangle([cx + 8, cy + 8, cx + 8 + bw, cy + 34], radius=6, fill=ACCENT)
        draw.text((cx + 12, cy + 12), badge.strip(), fill=(255, 255, 255), font=font(16))

        dur = fmt_duration(row["duration"])
        dw = draw.textlength(dur, font=font(14))
        draw.rounded_rectangle(
            [cx + CELL_W - dw - 20, cy + THUMB_H - 30, cx + CELL_W - 8, cy + THUMB_H - 8],
            radius=5, fill=BADGE_BG)
        draw.text((cx + CELL_W - dw - 14, cy + THUMB_H - 27), dur,
                  fill=(255, 255, 255), font=font(14))

        if row["reactions"]:
            star = f"★{row['reactions']}"
            draw.rounded_rectangle([cx + 8, cy + THUMB_H - 30, cx + 16 +
                                    draw.textlength(star, font=font(14)) + 6,
                                    cy + THUMB_H - 8], radius=5, fill=BADGE_BG)
            draw.text((cx + 14, cy + THUMB_H - 27), star, fill=(255, 205, 60), font=font(14))

        ty = cy + THUMB_H + 10
        title = row["title"] or Path(row["filename"]).stem
        draw.text((cx + 12, ty), _ellipsize(draw, title, font(17), CELL_W - 24),
                  fill=TEXT, font=font(17))

        tags = db.get_tags(row["id"])
        line = "  ".join("#" + t["display"] for t in tags) if tags else "タグ未設定"
        draw.text((cx + 12, ty + 25),
                  _ellipsize(draw, line, font(14), CELL_W - 24),
                  fill=(120, 170, 255) if tags else MUTED, font=font(14))

        meta = f"{row['uploader_name']}   {str(row['created_at'])[:10]}"
        draw.text((cx + 12, ty + 46),
                  _ellipsize(draw, meta, font(13), CELL_W - 24), fill=MUTED, font=font(13))

    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()

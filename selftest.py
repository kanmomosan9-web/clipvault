import random
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

import config
import db
import grid
import media

SAMPLES = [
    ("League of Legends 2026.08.12 - 21.34.56.01.mp4", "ペンタキル決めた", ["LoL", "pentakill", "ADC"]),
    ("VALORANT 2026.08.11 - 19.02.11.03.mp4", "1v4クラッチ", ["VALORANT", "clutch"]),
    ("Apex Legends 2026.08.10 - 23.15.02.01.mp4", "", ["Apex"]),
    ("League of Legends 2026.08.09 - 18.44.20.02.mp4", "神ガンク", ["LoL", "JG"]),
    ("Medal_2026_08_08_221130.mp4", "事故", ["LoL", "fail"]),
    ("RimWorld 2026.08.07 - 02.11.09.01.mp4", "コロニー壊滅", ["RimWorld"]),
    ("League of Legends 2026.08.06 - 20.01.44.01.mp4", "バロンスチール", ["LoL", "steal"]),
    ("VALORANT 2026.08.05 - 21.55.31.01.mp4", "エース", ["VALORANT", "ace"]),
    ("CS2_20260804_193012.mp4", "", ["CS2"]),
    ("League of Legends 2026.08.03 - 17.22.10.01.mp4", "ラストヒットミス集", ["LoL", "fail"]),
]


def fake_thumb(path: Path, seed: int):
    random.seed(seed)
    img = Image.new("RGB", (640, 360), (random.randint(20, 70), random.randint(20, 70),
                                        random.randint(40, 110)))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = random.randint(0, 600), random.randint(0, 320)
        d.ellipse([x, y, x + random.randint(20, 120), y + random.randint(20, 120)],
                  fill=(random.randint(40, 200),) * 3)
    img.save(path, quality=80)


def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    db.connect()

    base = datetime(2026, 8, 20)
    for i, (fname, title, tags) in enumerate(SAMPLES):
        meta = media.parse_filename(fname)
        cid = db.insert_clip(
            guild_id=1, channel_id=2, message_id=1000 + i, attachment_id=2000 + i,
            filename=fname, title=title or meta["title"],
            uploader_id=42, uploader_name=random.choice(["auau", "kento", "mira"]),
            duration=random.uniform(12, 95), width=1920, height=1080,
            size_bytes=random.randint(3_000_000, 40_000_000),
            recorded_at=meta["recorded_at"],
            created_at=(base - timedelta(days=i)).isoformat(),
        )
        thumb = config.THUMB_DIR / f"{cid}.jpg"
        fake_thumb(thumb, i)
        db.update_clip(cid, thumb_path=str(thumb), reactions=random.randint(0, 15))
        if meta["game"]:
            db.set_tags(cid, [meta["game"]], category="game")
        db.add_tags(cid, tags)
        print(f"  #{cid:2d} game={meta['game']:<10} rec={meta['recorded_at']} title={title}")

    db.add_alias("ボット", "ADC")
    print("\nalias 'ボット' ->", db.resolve_tag("ボット", create=False),
          "/ 'ADC' ->", db.resolve_tag("ADC", create=False))

    rows, total = db.query_clips(sort="hot", limit=9)
    print(f"sort=hot -> {total} total, page rows: {[r['id'] for r in rows]}")

    rows, total = db.query_clips(query="ペンタ")
    print(f"search 'ペンタ' -> {total} hit(s): {[r['title'] for r in rows]}")

    rows, total = db.query_clips(query="クラッチ")
    print(f"search 'クラッチ' -> {total} hit(s): {[r['title'] for r in rows]}")

    lol = db.resolve_tag("LoL", create=False)
    fail = db.resolve_tag("fail", create=False)
    rows, total = db.query_clips(tag_ids=[lol, fail])
    print(f"tag AND (#LoL & #fail) -> {total} hit(s): {[r['title'] for r in rows]}")

    rows, total = db.query_clips(sort="new", limit=9)
    png = grid.render(rows, 0, total, "クリップライブラリ   —   新着順")
    out = Path("preview_grid.png")
    out.write_bytes(png)
    print(f"\nrendered {out} ({len(png) // 1024} KB), font = {grid._resolve_font()}")


if __name__ == "__main__":
    main()

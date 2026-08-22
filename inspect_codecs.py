"""クリップの映像/音声パラメータを調べる。

デスクトップ版 Discord（Electron）で
kUnsupportedConfig が出る場合、プロファイルやレベル、
クロマサブサンプリングが原因のことが多い。

    .venv/bin/python inspect_codecs.py [clip_id ...]
    .venv/bin/python inspect_codecs.py --all
"""
import asyncio
import json
import subprocess
import sys

import discord

import config
import db
import dc

# Electron / Chromium が安全に扱える範囲の目安
SAFE_PROFILES = {"Constrained Baseline", "Baseline", "Main", "High"}
SAFE_PIX_FMT = {"yuv420p", "yuvj420p"}
MAX_LEVEL = 52          # Level 5.2


def probe(url: str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", url]
    out = subprocess.run(cmd, capture_output=True, timeout=120)
    if out.returncode != 0:
        return {"error": out.stderr.decode("utf-8", "ignore")[:200]}
    return json.loads(out.stdout)


def verdict(v: dict, a: dict | None) -> list[str]:
    problems = []
    if v.get("codec_name") != "h264":
        problems.append(f"映像コーデックが h264 でない: {v.get('codec_name')}")
    prof = v.get("profile", "")
    if prof and prof not in SAFE_PROFILES:
        problems.append(f"プロファイルが特殊: {prof}")
    lvl = v.get("level")
    if isinstance(lvl, int) and lvl > MAX_LEVEL:
        problems.append(f"レベルが高すぎる: {lvl / 10:.1f}")
    pix = v.get("pix_fmt", "")
    if pix and pix not in SAFE_PIX_FMT:
        problems.append(f"クロマ形式が特殊: {pix}（4:2:0 でない）")
    if v.get("bits_per_raw_sample") not in (None, "8"):
        problems.append(f"ビット深度: {v.get('bits_per_raw_sample')}bit")
    if a is not None and a.get("codec_name") not in ("aac", "mp3", "opus"):
        problems.append(f"音声コーデックが特殊: {a.get('codec_name')}")
    return problems


async def main(ids: list[int], everything: bool) -> None:
    db.connect()
    if everything:
        ids = [r["id"] for r in db.connect().execute(
            "SELECT id FROM clips ORDER BY created_at DESC LIMIT 40")]
    if not ids:
        row = db.connect().execute(
            "SELECT id FROM clips ORDER BY created_at DESC LIMIT 5").fetchone()
        ids = [row["id"]] if row else []
    if not ids:
        print("クリップがありません")
        return

    client = discord.Client(intents=discord.Intents.default())
    done = asyncio.Event()

    @client.event
    async def on_ready():
        bad = 0
        try:
            for cid in ids:
                clip = db.get_clip(cid)
                if clip is None:
                    print(f"clip {cid}: 見つかりません")
                    continue
                url = await dc.fresh_attachment_url(client, clip)
                if not url:
                    print(f"clip {cid}: 添付を取得できません")
                    continue
                data = await asyncio.to_thread(probe, url)
                if "error" in data:
                    print(f"clip {cid}: ffprobe 失敗 {data['error']}")
                    continue

                streams = data.get("streams", [])
                v = next((s for s in streams if s["codec_type"] == "video"), {})
                a = next((s for s in streams if s["codec_type"] == "audio"), None)

                name = clip["filename"][:38]
                print(f"\nclip {cid}: {name}")
                print(f"  映像 : {v.get('codec_name')} / {v.get('profile')} / "
                      f"Level {(v.get('level') or 0) / 10:.1f} / "
                      f"{v.get('pix_fmt')} / {v.get('width')}x{v.get('height')}")
                if a:
                    print(f"  音声 : {a.get('codec_name')} / {a.get('profile') or '-'} / "
                          f"{a.get('sample_rate')}Hz / {a.get('channels')}ch")
                else:
                    print("  音声 : なし")

                problems = verdict(v, a)
                if problems:
                    bad += 1
                    for p in problems:
                        print(f"  !!   {p}")
                else:
                    print("  OK   一般的な範囲。デコードできるはず")

            print(f"\n{len(ids)} 件中 {bad} 件に注意点あり")
            if bad:
                print("\nデスクトップ版 Discord で再生できない場合、")
                print("再エンコードで解決する見込みが高い:")
                print("  ffmpeg -i in.mp4 -c:v libx264 -profile:v high -level 4.1 \\")
                print("         -pix_fmt yuv420p -c:a aac -b:a 128k out.mp4")
        finally:
            done.set()
            await client.close()

    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN が .env にありません")
    await client.start(config.TOKEN)
    await done.wait()


if __name__ == "__main__":
    args = sys.argv[1:]
    every = "--all" in args
    nums = [int(a) for a in args if a.isdigit()]
    asyncio.run(main(nums, every))

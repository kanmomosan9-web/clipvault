"""Discord のメッセージからタグを取り込む部分を検証する。

  クリップ投稿時の本文     : 「ペンタキル取った #LoL #神プレイ」
  あとから返信            : 「#やらかし」
  投稿を編集              : タグとタイトルを付け直す
"""
import asyncio
import os

os.environ.setdefault("WORKER_TOKEN", "t")

import config
import db
import media
import selftest


def show(clip_id: int) -> tuple[str, list[str]]:
    clip = db.get_clip(clip_id)
    tags = [t["display"] for t in db.get_tags(clip_id)]
    return clip["title"], tags


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()

    print("\n=== 本文の解析 ===")
    cases = [
        ("ペンタキル取った #LoL #神プレイ", "ペンタキル取った", ["LoL", "神プレイ"]),
        ("#バロン スチール決まった #しげん", "スチール決まった", ["バロン", "しげん"]),
        ("<#1234567890> の件 #雑談", "の件", ["雑談"]),
        ("<@708279743232999477> のプレイ #ADC", "のプレイ", ["ADC"]),
        ("<:kusa:123456> 込み #草", "込み", ["草"]),
        ("見て！ #やらかし、#ミス集。", "見て！", ["やらかし", "ミス集"]),
        ("https://example.com #参考", "", ["参考"]),
        ("#LoL #lol #LOL", "", ["LoL"]),
        ("タイトルだけ", "タイトルだけ", []),
        ("", "", []),
    ]
    ng = 0
    for text, want_title, want_tags in cases:
        got = media.parse_message(text)
        ok = got["title"] == want_title and got["tags"] == want_tags
        ng += not ok
        print(f"  {'OK ' if ok else 'NG '} {text[:34]!r:<38} "
              f"-> {got['title']!r} {got['tags']}")
    assert ng == 0, f"{ng} 件が期待と違う"

    print("\n=== クリップへの反映 ===")
    cid = 1
    db.set_tags(cid, ["LoL"], category="game")
    said = media.parse_message("ペンタキル取った #ADC #神プレイ")
    db.add_tags(cid, said["tags"])
    db.update_clip(cid, title=said["title"])
    title, tags = show(cid)
    print(f"  投稿時   -> title={title!r} tags={tags}")
    assert title == "ペンタキル取った"
    assert "LoL" in tags and "ADC" in tags and "神プレイ" in tags

    said = media.parse_message("#やらかし")
    db.add_tags(cid, said["tags"])
    title, tags = show(cid)
    print(f"  返信で追加 -> title={title!r} tags={tags}")
    assert "やらかし" in tags and "ADC" in tags, "既存タグが消えている"

    said = media.parse_message("やっぱり神プレイではない #ミス")
    db.add_tags(cid, said["tags"])
    db.update_clip(cid, title=said["title"])
    title, tags = show(cid)
    print(f"  編集後   -> title={title!r} tags={tags}")
    assert title == "やっぱり神プレイではない" and "ミス" in tags

    print("\n=== 表記ゆれは既存タグに寄る ===")
    db.add_alias("ぼっと", "ADC")
    said = media.parse_message("#ぼっと")
    db.add_tags(cid, said["tags"])
    _, tags = show(cid)
    print(f"  #ぼっと を追加 -> {tags}")
    assert tags.count("ADC") == 1 and "ぼっと" not in tags

    print("\n=== 検索に乗る ===")
    rows, total = db.query_clips(query="神プレイ")
    print(f"  タグで検索 -> {total} 件")
    assert total >= 1

    print("\nTAGS OK")


if __name__ == "__main__":
    asyncio.run(main())

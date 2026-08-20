import json
import os

os.environ.setdefault("DISCORD_TOKEN", "x")

import config
import db
import selftest


def main():
    if not db.connect().execute("SELECT COUNT(*) n FROM clips").fetchone()["n"]:
        selftest.main()

    import bot

    v = bot.ListView(sort="hot")
    v.fetch()
    files = v.build()

    payload = [c.to_component_dict() for c in v.children]
    print(json.dumps(payload, ensure_ascii=False, indent=1)[:900], "...\n")

    def walk(node, depth=0):
        t = node.get("type")
        names = {1: "ActionRow", 2: "Button", 9: "Section", 10: "TextDisplay",
                 11: "Thumbnail", 12: "MediaGallery", 14: "Separator", 17: "Container"}
        label = node.get("label") or (node.get("content", "")[:28].replace("\n", " / "))
        print("  " * depth + f"{names.get(t, t)}  {label}")
        for k in ("components", "items"):
            for ch in node.get(k, []) or []:
                walk(ch, depth + 1)
        if node.get("accessory"):
            walk(node["accessory"], depth + 1)

    total = 0
    for c in payload:
        walk(c)

    def count(node):
        n = 1
        for k in ("components", "items"):
            for ch in node.get(k, []) or []:
                n += count(ch)
        if node.get("accessory"):
            n += count(node["accessory"])
        return n

    total = sum(count(c) for c in payload)
    print(f"\ncomponents = {total} (limit 40)   attachments = {len(files)} (limit 10)")
    print("attachment names:", [f.filename for f in files])
    assert total <= 40, "component limit exceeded"
    assert len(files) <= 10, "attachment limit exceeded"
    print("\nOK")


if __name__ == "__main__":
    main()

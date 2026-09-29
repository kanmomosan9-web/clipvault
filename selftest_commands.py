"""bot.py のスラッシュコマンドが揃っているかを検査する。

編集の巻き込みでコマンドが消える事故があったため、期待一覧との差分を見る。
"""
import ast
import pathlib
import sys

EXPECTED = {
    "clips", "search", "scan", "resync", "transcribe", "jobs",
    "library", "shorts", "stats", "tagdelete", "alias",
}


def found() -> set[str]:
    tree = ast.parse(pathlib.Path("bot.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if isinstance(f, ast.Attribute) and f.attr == "command":
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        names.add(kw.value.value)
    return names


def main() -> int:
    got = found()
    print(f"定義されているコマンド ({len(got)}):")
    for n in sorted(got):
        print(f"  /{n}")
    missing, extra = EXPECTED - got, got - EXPECTED
    if missing:
        print(f"\n欠落: {sorted(missing)}")
    if extra:
        print(f"\n期待一覧に無い（EXPECTED の更新が必要かも）: {sorted(extra)}")
    if missing:
        print("\nNG")
        return 1
    print("\nCOMMANDS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import os
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent

MARKERS = {
    "bot.py": [
        ("リスト表示モード", "class ListView"),
        ("/resync コマンド", "cmd_resync"),
        ("リアクション集計の修正", "def count_reactions"),
        ("モード切り替え", "def open_new"),
        ("ビルド表示", "BUILD ="),
    ],
    "db.py": [
        ("スキーマ自動移行", "def _migrate"),
        ("複数添付の対応", "UNIQUE (message_id, attachment_id)"),
        ("リアクション専用更新", "def set_reactions"),
        ("ジョブキュー", "def claim_job"),
    ],
    "grid.py": [("リスト用タイル生成", "def tile_bytes")],
    "worker.py": [("文字起こしワーカー", "def transcribe"),
                  ("リモートモード", "class RemoteRunner")],
    "api.py": [("ジョブAPI", "def build_app")],
    "config.py": [("リスト表示の件数", "LIST_PAGE_SIZE")],
    "vocab.py": [("LoL 語彙", "PROMPT_TERMS"), ("認識結果の補正", "def correct")],
    "webui.py": [("Web API", "def add_routes"), ("Activity 認証", "def exchange_code")],
    "web/assets/boot.js": [("Activity 起動処理", "startActivity")],
    "web/assets/fonts.css": [("同梱フォント", "font-face")],
    "selftest_commands.py": [("コマンド欠落検査", "EXPECTED")],
    "web/index.html": [("ブラウザ版UI", "CLIPVAULT")],
}

OK, NG, WARN = "  OK  ", "  NG  ", " WARN "


def line(status, text, detail=""):
    print(f"[{status}] {text}" + (f"   {detail}" if detail else ""))


def check_interpreter() -> bool:
    """venv 以外で起動されていたら警告する。

    setup-vm.sh は .venv に依存を入れる。素の python3 で実行すると
    「未インストール」が並んで誤診断になるため、先に気づけるようにする。
    """
    venv = HERE / ".venv" / "bin" / "python"
    if not venv.exists():
        return True
    if Path(sys.prefix).resolve() == (HERE / ".venv").resolve():
        return True
    print("!" * 66)
    print(" venv の外で実行されています。次のように実行し直してください:")
    print(f"   {venv} doctor.py")
    print(" このまま続けると『未インストール』が誤って表示されます。")
    print("!" * 66)
    print()
    return False


def main():
    print(f"\n=== ClipVault doctor ===\nフォルダ: {HERE}\n")
    in_venv = check_interpreter()
    stale = False

    print("--- ファイルの版 ---")
    for fname, marks in MARKERS.items():
        path = HERE / fname
        if not path.exists():
            line(NG, f"{fname} が存在しない")
            stale = True
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, needle in marks:
            if needle in text:
                line(OK, f"{fname}: {label}")
            else:
                line(NG, f"{fname}: {label} が見つからない → 古いファイルです")
                stale = True

    print("\n--- 実行環境 ---")
    line(OK if sys.version_info >= (3, 10) else NG,
         f"Python {sys.version_info.major}.{sys.version_info.minor}", "3.10 以上が必要")

    try:
        import discord
        ok = tuple(int(x) for x in discord.__version__.split(".")[:2]) >= (2, 6)
        line(OK if ok else NG, f"discord.py {discord.__version__}",
             "リスト表示には 2.6 以上が必要")
        if not ok:
            print("       → pip install -U discord.py")
    except ImportError:
        line(NG, "discord.py 未インストール", "pip install -r requirements.txt")

    try:
        import PIL
        line(OK, f"Pillow {PIL.__version__}")
    except ImportError:
        line(NG, "Pillow 未インストール")

    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        line(OK if p else NG, exe, p or "PATH に無い → サムネが作られません")

    print("\n--- 設定 ---")
    env = HERE / ".env"
    if not env.exists():
        line(NG, ".env が無い", "copy .env.example .env")
    else:
        vals = {}
        for ln in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.partition("=")
                vals[k.strip()] = v.strip()
        tok = vals.get("DISCORD_TOKEN", "")
        line(OK if len(tok) > 50 else NG, "DISCORD_TOKEN",
             f"{tok[:8]}…（{len(tok)}文字）" if tok else "未設定")
        for key in ("GUILD_ID", "STORAGE_CHANNEL_IDS"):
            v = vals.get(key, "")
            digits = v.replace(",", "").replace(" ", "").isdigit() and v != ""
            line(OK if digits else NG, key, v or "未設定")
            if digits and key == "GUILD_ID" and len(v) < 17:
                line(WARN, "  GUILD_ID が短い", "サーバーIDは17〜19桁です")

    print("\n--- フォント ---")
    sys.path.insert(0, str(HERE))
    try:
        import grid
        f = grid._resolve_font()
        line(OK if f else NG, "日本語フォント", f or "見つからない → 文字が豆腐になります")
    except Exception as e:
        line(WARN, "フォント確認をスキップ", str(e))

    print("\n--- ワーカー構成 ---")
    sys.path.insert(0, str(HERE))
    try:
        import config as cfg
        if cfg.WORKER_API_URL:
            line(OK, "モード", f"リモート（{cfg.WORKER_API_URL} / tier={cfg.WORKER_TIER}）")
            line(OK if cfg.WORKER_TOKEN else NG, "WORKER_TOKEN",
                 "設定済み" if cfg.WORKER_TOKEN else "未設定")
        else:
            line(OK, "モード", f"ローカル（tier={cfg.WORKER_TIER}, "
                              f"grace={cfg.WORKER_GRACE_SECONDS}s）")
        line(OK, "モデル", f"{cfg.WHISPER_MODEL} on {cfg.WHISPER_DEVICE} "
                          f"({cfg.WHISPER_COMPUTE})")
        line(OK, "優先モデル", cfg.PREFERRED_MODEL)
    except Exception as e:
        line(WARN, "config 読み込み失敗", str(e))

    print("\n--- Web ライブラリ ---")
    try:
        import config as cfg2
        if not cfg2.WEB_ENABLED:
            line(WARN, "無効", "WEB_ENABLED=0")
        elif not cfg2.PUBLIC_URL:
            line(NG, "PUBLIC_URL 未設定", "/library がリンクを発行できません")
        else:
            line(OK, "公開URL", cfg2.PUBLIC_URL)
            line(OK, "トークン有効期限", f"{cfg2.WEB_TOKEN_DAYS} 日")
        if cfg2.ACTIVITY_ENABLED:
            ok_id = bool(cfg2.CLIENT_ID) and cfg2.CLIENT_ID.isdigit()
            line(OK if ok_id else NG, "CLIENT_ID",
                 cfg2.CLIENT_ID or "未設定（Activity が動きません）")
            line(OK if cfg2.CLIENT_SECRET else NG, "CLIENT_SECRET",
                 "設定済み" if cfg2.CLIENT_SECRET else "未設定")
            sdk = HERE / "web" / "assets" / "embedded-app-sdk.js"
            line(OK if sdk.exists() else NG, "Embedded App SDK",
                 f"{sdk.stat().st_size // 1024} KB" if sdk.exists() else "見つからない")
        idx = HERE / "web" / "index.html"
        line(OK if idx.exists() else NG, "web/index.html",
             f"{idx.stat().st_size // 1024} KB" if idx.exists() else "見つからない")
    except Exception as e:
        line(WARN, "Web 設定の確認をスキップ", str(e))

    print("\n--- 語彙 ---")
    try:
        import vocab
        line(OK, "チャンピオン/アイテム/用語", f"{len(vocab.ALL_TERMS)} 語")
        line(OK, "プロンプト候補", f"{len(vocab.PROMPT_TERMS)} 語")
        line(OK, "呼び名", "、".join(vocab.MEMBERS[:6]) + " ...")
        line(OK, "生成されるプロンプト", f"{len(vocab.prompt_for('LoL'))} 字")
    except Exception as e:
        line(WARN, "vocab 読み込み失敗", str(e))

    print("\n--- 文字起こし ---")
    try:
        import faster_whisper
        line(OK, f"faster-whisper {faster_whisper.__version__}")
        try:
            import torch
            if torch.cuda.is_available():
                line(OK, "CUDA", torch.cuda.get_device_name(0))
            else:
                line(WARN, "CUDA 利用不可",
                     "CPUで動きますが非常に遅くなります（config の WHISPER_DEVICE=cpu 推奨）")
        except ImportError:
            line(WARN, "torch 未インストール",
                 "GPU判定不可。faster-whisper 単体でも動きますが確認できません")
    except ImportError:
        line(WARN, "faster-whisper 未インストール",
             "pip install -r requirements-worker.txt")

    print("\n--- データベース ---")
    dbp = HERE / "data" / "clipvault.db"
    if not dbp.exists():
        line(WARN, "DB 未作成", "bot を一度起動すると作られます")
    else:
        c = sqlite3.connect(dbp)
        c.row_factory = sqlite3.Row
        n = c.execute("SELECT COUNT(*) n FROM clips").fetchone()["n"]
        z = c.execute("SELECT COUNT(*) n FROM clips WHERE reactions = 0").fetchone()["n"]
        line(OK, "クリップ数", str(n))
        if n and z == n:
            line(WARN, "全クリップの★が0",
                 "bot 再起動後に /resync を実行してください")
        elif n:
            line(OK, "★が付いているクリップ", f"{n - z}/{n}")
        legacy = False
        for idx in c.execute("PRAGMA index_list(clips)").fetchall():
            if idx["unique"]:
                cols = [r["name"] for r in c.execute(f"PRAGMA index_info({idx['name']})")]
                if cols == ["message_id"]:
                    legacy = True
        line(WARN if legacy else OK, "スキーマ",
             "旧形式（次回起動時に自動移行されます）" if legacy else "最新")

        try:
            tr = c.execute(
                "SELECT COUNT(*) n FROM clips WHERE transcript != ''").fetchone()["n"]
            line(OK, "文字起こし済み", f"{tr}/{n}")
            q = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
            if q.get("pending"):
                line(WARN, "未処理ジョブ", f"{q['pending']} 件 → python worker.py を起動")
            if q.get("failed"):
                line(WARN, "失敗ジョブ", f"{q['failed']} 件 → /jobs で理由を確認")
            if q and not q.get("pending") and not q.get("failed"):
                line(OK, "ジョブキュー", str(q))
        except sqlite3.OperationalError:
            line(WARN, "jobs テーブルなし", "bot を再起動すると作られます")
        c.close()

    print("\n=== 結論 ===")
    if not in_venv:
        print("※ venv の外で実行したため、パッケージ関連の判定は信用できません。")
        print(f"   {HERE}/.venv/bin/python doctor.py で再実行してください。")
        print()
    if stale:
        print("古いファイルが残っています。ダウンロードした clipvault の中身を")
        print("このフォルダに『すべて上書き』してから、bot を再起動してください。")
    else:
        print("ファイルは最新です。あとは bot のプロセスを再起動してください。")
        print("起動ログに次の行が出れば、新しいコードで動いています:")
        print("  INFO BUILD 2026-08-21p (signed thumbs + scroll fix)")
    print()


if __name__ == "__main__":
    main()

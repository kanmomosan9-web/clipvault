import os
import re
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STORAGE_CHANNEL_IDS = {
    int(x) for x in os.getenv("STORAGE_CHANNEL_IDS", "").replace(" ", "").split(",") if x
}

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
THUMB_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "clipvault.db"

for d in (DATA_DIR, THUMB_DIR):
    d.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 9
GRID_COLS = 3

DEFAULT_VIEW = "list"
LIST_PAGE_SIZE = 3
LIST_THUMB_WIDTH = 768
# リスト表示で「🎙 文字起こし」を展開したときに載せる行数
LIST_TRANSCRIPT_LINES = int(os.getenv("LIST_TRANSCRIPT_LINES", "8"))

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

GAME_ALIASES = {
    "league of legends": "LoL",
    "valorant": "VALORANT",
    "apex legends": "Apex",
    "counter-strike 2": "CS2",
    "overwatch 2": "OW2",
    "rimworld": "RimWorld",
    "minecraft": "Minecraft",
    "monster hunter wilds": "MHWilds",
    "teamfight tactics": "TFT",
}

FONT_CANDIDATES = [
    str(BASE_DIR / "fonts" / "NotoSansJP-Bold.ttf"),
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

AUTO_TRANSCRIBE = os.getenv("AUTO_TRANSCRIBE", "1") == "1"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")
WHISPER_LANG = os.getenv("WHISPER_LANG", "ja")
WORKER_POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "10"))
MAX_TRANSCRIBE_SECONDS = int(os.getenv("MAX_TRANSCRIBE_SECONDS", "900"))

# VAD（無音除去）。既定値は faster-whisper の推奨に合わせる。
# min_silence_duration_ms を小さくしすぎると息継ぎまで削り、発話が欠ける。
VAD_FILTER = os.getenv("VAD_FILTER", "1") == "1"
VAD_MIN_SILENCE_MS = int(os.getenv("VAD_MIN_SILENCE_MS", "2000"))
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
VAD_SPEECH_PAD_MS = int(os.getenv("VAD_SPEECH_PAD_MS", "400"))


def vad_parameters() -> dict:
    return {
        "min_silence_duration_ms": VAD_MIN_SILENCE_MS,
        "threshold": VAD_THRESHOLD,
        "speech_pad_ms": VAD_SPEECH_PAD_MS,
    }


PREFERRED_MODEL = os.getenv("PREFERRED_MODEL", "large-v3")
WORKER_TIER = os.getenv("WORKER_TIER", "gpu")
WORKER_GRACE_SECONDS = int(os.getenv("WORKER_GRACE_SECONDS", "600"))
WORKER_API_URL = os.getenv("WORKER_API_URL", "").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
AUTO_UPGRADE = os.getenv("AUTO_UPGRADE", "1") == "1"

API_ENABLED = os.getenv("API_ENABLED", "1") == "1"
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("PORT") or os.getenv("API_PORT") or "8787")

# Web ライブラリ（ブラウザ / Discord Activity 用）
WEB_ENABLED = os.getenv("WEB_ENABLED", "1") == "1"
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEB_TOKEN_DAYS = int(os.getenv("WEB_TOKEN_DAYS", "30"))
# 「Discord でログイン」で入ったときの期限。使っている間は自動で延びる。
WEB_LOGIN_DAYS = int(os.getenv("WEB_LOGIN_DAYS", "90"))
# 掲示板とつなげる Discord のフォーラムチャンネル。0 なら Web だけの掲示板になる。
def _discord_id(name: str) -> int:
    """.env の ID を読む。全角数字・かっこ・空白・コメントが混ざっていても数字だけ拾う。

    書き方を少し間違えただけで bot 全体が起動しなくなるのを防ぐ。
    """
    raw = unicodedata.normalize("NFKC", os.getenv(name, "") or "")
    m = re.search(r"\d{5,}", raw)
    if raw.strip() and not m:
        print(f"[config] {name} の値が読めません（数字の ID を書いてください）: {raw!r}")
    return int(m.group()) if m else 0


FORUM_CHANNEL_ID = _discord_id("FORUM_CHANNEL_ID")
# bot 起動時に、止まっていた間のリアクションを数え直す件数（新しい順）
REACTION_CATCHUP = int(os.getenv("REACTION_CATCHUP", "300"))

# Web と Discord の相互同期
# コメント: クリップのスレッドへ Webhook で本人名義で投稿し、
#           スレッドに直接書かれたものも取り込む。
# リアクション: Discord API に代理付与の手段が無いため bot 名義になる。
SYNC_COMMENTS = os.getenv("SYNC_COMMENTS", "1") == "1"
SYNC_REACTIONS = os.getenv("SYNC_REACTIONS", "1") == "1"

# Discord Activity（Discord 内の iframe で動かす）
# CLIENT_ID は Developer Portal の General Information、
# CLIENT_SECRET は OAuth2 タブで取得する。
ACTIVITY_ENABLED = os.getenv("ACTIVITY_ENABLED", "1") == "1"
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
# Activity 内では全通信が Discord のプロキシを通る。
# URL Mappings には "/cdn -> cdn.discordapp.com" と登録し（"/.proxy" は
# ポータルでは入力できない）、アプリからは "/.proxy/cdn/..." を叩く。
# "/.proxy" の部分は Discord 側が解決する。
CDN_PROXY_PREFIX = os.getenv("CDN_PROXY_PREFIX", "/.proxy/cdn")

# 自然な文章をプロンプトに入れるとそのまま出力されやすい（幻覚）。
# 語彙リストのみに留めること。既定は空。
BASE_PROMPT = os.getenv("BASE_PROMPT", "")

# vocab.py の語彙を使うゲーム（プロンプト生成と認識結果の補正）
VOCAB_GAMES = {g for g in os.getenv("VOCAB_GAMES", "LoL,TFT,").split(",")}
VOCAB_CORRECT = os.getenv("VOCAB_CORRECT", "1") == "1"
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "170"))

GAME_PROMPTS = {
    "LoL": "バロン、ドラゴン、ガンク、フラッシュ、ウルト、リコール、タワー、ミニオン、"
           "レーン、ジャングル、ミッド、トップ、ボット、サポート、集団戦、オブジェクト、"
           "ペンタキル、エース、スチール、ワード、ビジョン",
    "VALORANT": "スパイク、設置、解除、エコ、フルバイ、クラッチ、エース、ピーク、"
                "スモーク、フラッシュ、ウルト、リテイク、ローテ、サイト、アタッカー、"
                "ディフェンダー、ヘッドショット",
    "Apex": "リング、ダウン、バナー、リスポーン、レイス、パスファインダー、アーマー、"
            "シールド、ノックダウン、チャンピオン、キルレ、漁夫、詰め、離脱",
    "CS2": "設置、解除、エコ、フルバイ、クラッチ、ヘッドショット、フラッシュ、スモーク、"
           "サイト、ラッシュ、ローテ",
    "OW2": "ウルト、タンク、ヒーラー、DPS、ペイロード、拠点、リスポーン、集団戦",
    "TFT": "リロール、レベル、コンプ、アイテム、キャリー、シナジー、利子",
    "RimWorld": "コロニー、入植者、襲撃、メカノイド、収穫、建築、医療",
}

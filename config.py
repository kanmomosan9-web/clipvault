import os
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

PREFERRED_MODEL = os.getenv("PREFERRED_MODEL", "large-v3")
WORKER_TIER = os.getenv("WORKER_TIER", "gpu")
WORKER_GRACE_SECONDS = int(os.getenv("WORKER_GRACE_SECONDS", "600"))
WORKER_API_URL = os.getenv("WORKER_API_URL", "").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
AUTO_UPGRADE = os.getenv("AUTO_UPGRADE", "1") == "1"

API_ENABLED = os.getenv("API_ENABLED", "1") == "1"
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("PORT") or os.getenv("API_PORT") or "8787")

# 自然な文章をプロンプトに入れるとそのまま出力されやすい（幻覚）。
# 語彙リストのみに留めること。既定は空。
BASE_PROMPT = os.getenv("BASE_PROMPT", "")

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

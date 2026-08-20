import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

SEP = r"[.\-_ ]*"
FILENAME_PATTERNS = [
    re.compile(
        rf"^(?P<game>.+?)[ _]+(?P<y>\d{{4}}){SEP}(?P<mo>\d{{2}}){SEP}(?P<d>\d{{2}})"
        rf"{SEP}-?{SEP}(?P<h>\d{{2}}){SEP}(?P<mi>\d{{2}}){SEP}(?P<s>\d{{2}})"
    ),
    re.compile(
        rf"^(?P<game>.+?)[ _]+(?P<y>\d{{4}}){SEP}(?P<mo>\d{{2}}){SEP}(?P<d>\d{{2}})"
    ),
]

GENERIC_PREFIXES = {"medal", "outplayed", "clip", "replay", "recording", "desktop"}


def parse_filename(name: str) -> dict:
    stem = Path(name).stem
    lead = re.match(r"^([A-Za-z]+)[ _\-]+(?=\D)", stem)
    if lead and lead.group(1).lower() in GENERIC_PREFIXES:
        stem = stem[lead.end():]
    for pat in FILENAME_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        raw_game = m.group("game").strip(" _-")
        if raw_game.lower() in GENERIC_PREFIXES:
            raw_game = ""
        game = config.GAME_ALIASES.get(raw_game.lower(), raw_game)
        parts = m.groupdict()
        try:
            rec = datetime(
                int(parts["y"]), int(parts["mo"]), int(parts["d"]),
                int(parts.get("h") or 0), int(parts.get("mi") or 0),
                int(parts.get("s") or 0),
            ).isoformat()
        except (ValueError, TypeError):
            rec = None
        return {"game": game, "recorded_at": rec, "title": ""}
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return {"game": "", "recorded_at": None, "title": cleaned[:80]}


def probe(src: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", src,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=90, check=True).stdout
        data = json.loads(out)
    except Exception:
        return {"duration": 0.0, "width": 0, "height": 0}
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    width = height = 0
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            width = int(st.get("width", 0) or 0)
            height = int(st.get("height", 0) or 0)
            if not duration:
                duration = float(st.get("duration", 0) or 0)
            break
    return {"duration": duration, "width": width, "height": height}


def extract_thumb(src: str, dest: Path, duration: float = 0.0) -> Optional[Path]:
    ts = max(0.5, duration * 0.35) if duration else 3.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{ts:.2f}", "-i", src,
        "-frames:v", "1", "-vf", "scale=1280:-2",
        "-q:v", "4", str(dest),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180, check=True)
    except Exception:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", src, "-frames:v", "1",
                 "-vf", "scale=640:-2", "-q:v", "4", str(dest)],
                capture_output=True, timeout=180, check=True,
            )
        except Exception:
            return None
    return dest if dest.exists() and dest.stat().st_size > 0 else None


def check_ffmpeg() -> bool:
    for exe in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([exe, "-version"], capture_output=True, check=True, timeout=15)
        except Exception:
            return False
    return True


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds or 0))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fmt_size(n: int) -> str:
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.1f}{unit}" if unit != "B" else f"{int(v)}B"
        v /= 1024
    return f"{v:.1f}GB"

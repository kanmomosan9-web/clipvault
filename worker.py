import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import config
import media

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

_model = None
_model_name = ""
_forced_cpu = False

CUDA_ERROR_HINTS = ("cublas", "cudnn", "cuda", "libcu", "no kernel image",
                    "out of memory", "device")

HALLUCINATIONS = (
    "ご視聴ありがとうございました", "ご清聴ありがとうございました",
    "最後までご視聴", "チャンネル登録", "高評価", "字幕視聴者",
    "thanks for watching", "subscribe", "www.", "http",
)


def _norm(text: str) -> str:
    return re.sub(r"[\s、。，．,.!?！？・…\-ー]", "", text).lower()


def _prompt_terms(prompt: str) -> list[str]:
    return [_norm(t) for t in re.split(r"[、,]\s*", prompt) if _norm(t)]


def _is_prompt_echo(text: str, terms: list[str]) -> bool:
    """語彙リストを2語以上つなげて復唱した場合のみ True。

    単語1つだけの一致は本物の発話でも起こりうるので除外しない
    （例: 実際に「フラッシュ」とだけ叫んだ場合）。
    """
    if len(terms) < 2:
        return False
    hits = [t for t in terms if t and t in text]
    if len(hits) < 2:
        return False
    # 語彙以外の文字がほとんど無ければ復唱とみなす
    remainder = text
    for t in sorted(hits, key=len, reverse=True):
        remainder = remainder.replace(t, "")
    return len(remainder) <= max(2, len(text) * 0.2)


def _is_noise(text: str, terms: list[str]) -> bool:
    t = _norm(text)
    if len(t) < 2:
        return True
    if any(h in t for h in (_norm(h) for h in HALLUCINATIONS)):
        return True
    if _is_prompt_echo(t, terms):
        return True
    return False


def _register_cuda_dlls() -> None:
    if os.name != "nt":
        return
    import site
    roots = []
    for base in set(site.getsitepackages() + [site.getusersitepackages()]):
        nv = Path(base) / "nvidia"
        if nv.is_dir():
            roots.extend(p for p in nv.glob("*/bin") if p.is_dir())
    if not roots:
        return
    for d in roots:
        try:
            os.add_dll_directory(str(d))
        except (OSError, AttributeError):
            pass
    os.environ["PATH"] = os.pathsep.join(
        [str(d) for d in roots] + [os.environ.get("PATH", "")])
    log.info("registered %d CUDA DLL directories", len(roots))


def _looks_like_cuda_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(h in msg for h in CUDA_ERROR_HINTS)


def load_model(force_cpu: bool = False):
    global _model, _model_name
    if _model is not None:
        return _model
    _register_cuda_dlls()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "faster-whisper が入っていません。\n"
            "  pip install -r requirements-worker.txt")

    device = "cpu" if (force_cpu or _forced_cpu) else config.WHISPER_DEVICE
    compute = "int8" if device == "cpu" else config.WHISPER_COMPUTE
    log.info("loading %s on %s (%s) ...", config.WHISPER_MODEL, device, compute)
    try:
        _model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
    except Exception as e:
        if device == "cpu":
            raise
        log.warning("GPU init failed (%s) - falling back to CPU/int8", e)
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        device = "cpu"
    _model_name = f"{config.WHISPER_MODEL}({device})" if device == "cpu" \
        else config.WHISPER_MODEL
    log.info("model ready (%s)", device)
    return _model


def _fallback_to_cpu(reason: BaseException) -> None:
    global _model, _forced_cpu
    log.error("CUDA が使えません: %s", reason)
    log.error("Windows の場合は次を実行してください:")
    log.error("  pip install nvidia-cublas-cu12 \"nvidia-cudnn-cu12>=9,<10\"")
    log.warning("以降は CPU で処理を継続します（大幅に遅くなります）")
    _forced_cpu = True
    _model = None
    load_model(force_cpu=True)


def extract_audio(src: str, dest: Path) -> bool:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vn",
           "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=900)
    except subprocess.CalledProcessError as e:
        log.error("ffmpeg failed: %s", e.stderr.decode("utf-8", "ignore")[:300])
        return False
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timed out")
        return False
    return dest.exists() and dest.stat().st_size > 1000


def _run(model, path: Path, prompt: str, language: str):
    segments, info = model.transcribe(
        str(path), language=language, vad_filter=config.VAD_FILTER,
        vad_parameters=config.vad_parameters(),
        initial_prompt=prompt, beam_size=5, condition_on_previous_text=False,
    )
    terms = _prompt_terms(prompt)
    out, dropped = [], 0
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        if _is_noise(text, terms):
            dropped += 1
            continue
        out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
    log.info("  language=%s prob=%.2f segments=%d (filtered %d)",
             info.language, info.language_probability, len(out), dropped)
    return " ".join(s["text"] for s in out), out


def transcribe(path: Path, prompt: str, language: str) -> tuple[str, list[dict]]:
    try:
        return _run(load_model(), path, prompt, language)
    except Exception as e:
        if _forced_cpu or not _looks_like_cuda_error(e):
            raise
        _fallback_to_cpu(e)
        return _run(load_model(), path, prompt, language)


async def run_job(job: dict) -> dict:
    log.info("job %s: %s (%.0fs, %s)", job["job_id"], job["filename"],
             job.get("duration") or 0, job.get("game") or "-")

    if job.get("duration") and job["duration"] > config.MAX_TRANSCRIBE_SECONDS:
        return {"job_id": job["job_id"], "status": "skipped", "error": "too long"}

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        if not await asyncio.to_thread(extract_audio, job["url"], wav):
            return {"job_id": job["job_id"], "status": "failed",
                    "error": "audio extraction failed"}
        try:
            text, segments = await asyncio.to_thread(
                transcribe, wav, job.get("prompt", ""), job.get("language", "ja"))
        except Exception as e:
            log.exception("transcription error")
            return {"job_id": job["job_id"], "status": "failed", "error": str(e)}

    log.info("  %d chars / %d segments: %s", len(text), len(segments), text[:60])
    return {"job_id": job["job_id"], "clip_id": job["clip_id"], "status": "done",
            "text": text, "segments": segments, "model": _model_name}


class RemoteRunner:
    def __init__(self):
        import aiohttp
        self.aiohttp = aiohttp
        self.base = config.WORKER_API_URL
        self.headers = {"Authorization": f"Bearer {config.WORKER_TOKEN}"}

    async def loop(self):
        log.info("remote mode -> %s (tier=%s)", self.base, config.WORKER_TIER)
        async with self.aiohttp.ClientSession(headers=self.headers) as sess:
            while True:
                try:
                    job = await self.claim(sess)
                except SystemExit:
                    raise
                except Exception as e:
                    log.warning("claim failed (%s) - retrying in 30s", e)
                    await asyncio.sleep(30)
                    continue
                if job is None:
                    await asyncio.sleep(config.WORKER_POLL_SECONDS)
                    continue
                result = await run_job(job)
                try:
                    await self.report(sess, result)
                except Exception as e:
                    log.error("could not report result: %s", e)

    async def claim(self, sess) -> Optional[dict]:
        payload = {"tier": config.WORKER_TIER, "min_age": 0,
                   "allow_upgrade": config.AUTO_UPGRADE}
        async with sess.post(f"{self.base}/api/jobs/claim", json=payload,
                             timeout=self.aiohttp.ClientTimeout(total=60)) as r:
            if r.status == 401:
                raise SystemExit("WORKER_TOKEN が一致しません")
            r.raise_for_status()
            return (await r.json()).get("job")

    async def report(self, sess, result: dict):
        async with sess.post(f"{self.base}/api/jobs/result", json=result,
                             timeout=self.aiohttp.ClientTimeout(total=120)) as r:
            r.raise_for_status()


class LocalRunner:
    def __init__(self):
        import discord

        import db
        import dc
        self.db, self.dc = db, dc
        self.client = discord.Client(intents=discord.Intents.default())
        self.client.event(self.on_ready)

    async def on_ready(self):
        log.info("local mode as %s (tier=%s, grace=%ss)",
                 self.client.user, config.WORKER_TIER, config.WORKER_GRACE_SECONDS)
        n = self.db.requeue_stuck()
        if n:
            log.info("requeued %d stuck job(s)", n)
        self.client.loop.create_task(self.loop())

    async def loop(self):
        await asyncio.sleep(2)
        while not self.client.is_closed():
            job_row = self.db.claim_job(
                min_age_seconds=config.WORKER_GRACE_SECONDS, tier=config.WORKER_TIER)
            if job_row is None:
                await asyncio.sleep(config.WORKER_POLL_SECONDS)
                continue

            clip = self.db.get_clip(job_row["clip_id"])
            if clip is None:
                self.db.finish_job(job_row["id"], "failed", "clip not found")
                continue
            url = await self.dc.fresh_attachment_url(self.client, clip)
            if not url:
                self.db.finish_job(job_row["id"], "failed",
                                   "could not refresh attachment URL")
                continue

            game = self.db.game_tag(clip["id"])
            job = {"job_id": job_row["id"], "clip_id": clip["id"],
                   "filename": clip["filename"], "duration": clip["duration"],
                   "game": game, "language": config.WHISPER_LANG, "url": url,
                   "prompt": config.BASE_PROMPT + config.GAME_PROMPTS.get(game, "")}

            result = await run_job(job)
            if result["status"] == "done":
                self.db.save_transcript(clip["id"], result["text"],
                                        result["segments"], result["model"])
                if not result["text"]:
                    result["status"] = "empty"
            self.db.finish_job(job_row["id"], result["status"], result.get("error", ""))

    def run(self):
        self.client.run(config.TOKEN, log_handler=None)


def main():
    if not media.check_ffmpeg():
        raise SystemExit("ffmpeg / ffprobe が PATH にありません")
    if "--preload" in sys.argv:
        log.info("preloading model (also keeps memory resident on idle VMs)")
        load_model()

    if config.WORKER_API_URL:
        if not config.WORKER_TOKEN:
            raise SystemExit("WORKER_TOKEN が .env に設定されていません")
        asyncio.run(RemoteRunner().loop())
    else:
        if not config.TOKEN:
            raise SystemExit(
                "DISCORD_TOKEN も WORKER_API_URL も未設定です。\n"
                "  VM上で動かす場合: DISCORD_TOKEN を設定\n"
                "  自宅PCで動かす場合: WORKER_API_URL と WORKER_TOKEN を設定")
        import db
        db.connect()
        LocalRunner().run()


if __name__ == "__main__":
    main()

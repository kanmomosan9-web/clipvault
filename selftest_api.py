import asyncio
import json
import os

os.environ["WORKER_TOKEN"] = "test-secret"

import config
import db
import selftest

config.WORKER_TOKEN = "test-secret"
config.PREFERRED_MODEL = "large-v3"


class FakeUser:
    def __str__(self):
        return "ClipVault#0000"


class FakeAttachment:
    def __init__(self, aid):
        self.id = aid
        self.url = f"https://cdn.discordapp.com/attachments/x/y/z.mp4?ex=FRESH{aid}"


class FakeMessage:
    def __init__(self, aid):
        self.attachments = [FakeAttachment(aid)]


class FakeChannel:
    async def fetch_message(self, mid):
        clip = db.clip_by_message(mid)
        return FakeMessage(clip["attachment_id"])


class FakeClient:
    user = FakeUser()

    def get_channel(self, cid):
        return FakeChannel()


async def main():
    config.DB_PATH.unlink(missing_ok=True)
    db._conn = None
    selftest.main()

    import api
    from aiohttp.test_utils import TestClient, TestServer

    app = api.build_app(FakeClient())
    async with TestClient(TestServer(app)) as cli:
        auth = {"Authorization": "Bearer test-secret"}

        r = await cli.get("/health")
        print("GET /health ->", r.status, (await r.json())["clips"], "clips")

        r = await cli.post("/api/jobs/claim", json={"tier": "gpu"})
        print("claim without token ->", r.status)
        assert r.status == 401

        for cid in (1, 2, 3):
            db.enqueue(cid)
        print("queued:", db.job_counts())

        r = await cli.post("/api/jobs/claim", headers=auth, json={"tier": "gpu"})
        job = (await r.json())["job"]
        print("gpu claimed job", job["job_id"], "clip", job["clip_id"],
              "| game:", job["game"], "| prompt:", job["prompt"][:24] + "...")
        assert "FRESH" in job["url"]

        cpu = db.claim_job(min_age_seconds=600, tier="cpu")
        print("cpu claim with 600s grace ->", cpu)
        assert cpu is None, "CPU should not steal fresh jobs"

        cpu = db.claim_job(min_age_seconds=0, tier="cpu")
        print("cpu claim with 0s grace -> job", cpu["id"], "tier", cpu["tier"])

        r = await cli.post("/api/jobs/result", headers=auth, json={
            "job_id": job["job_id"], "clip_id": job["clip_id"], "status": "done",
            "text": "バロン取られたやばい",
            "segments": [{"start": 0.0, "end": 1.5, "text": "バロン取られた"},
                         {"start": 1.5, "end": 2.4, "text": "やばい"}],
            "model": "large-v3"})
        print("POST result ->", r.status, await r.json())

        clip = db.get_clip(job["clip_id"])
        print("stored model:", clip["transcript_model"], "| text:", clip["transcript"])

        rows, total = db.query_clips(query="バロン")
        print("FTS 'バロン' ->", total, "hit(s)")
        assert total >= 1

        db.save_transcript(9, "てすと", [{"start": 0, "end": 1, "text": "てすと"}], "small")
        low = db.low_quality("large-v3")
        print("low quality clips:", low)
        assert 9 in low and job["clip_id"] not in low

        while True:
            leftover = db.claim_job()
            if leftover is None:
                break
            db.finish_job(leftover["id"], "done")
        for j in db.connect().execute("SELECT id FROM jobs WHERE status='running'"):
            db.finish_job(j["id"], "done")
        print("queue drained:", db.job_counts())

        r = await cli.post("/api/jobs/claim", headers=auth, json={"tier": "gpu"})
        print("claim with empty queue ->", (await r.json())["job"])

        r = await cli.post("/api/jobs/claim", headers=auth,
                           json={"tier": "gpu", "allow_upgrade": True})
        up = (await r.json())["job"]
        print("auto-upgrade claim -> clip", up["clip_id"] if up else None)
        assert up and up["clip_id"] == 9, "should pick up the small-model clip"

        print("\nfinal queue:", db.job_counts())
        print("API OK")


if __name__ == "__main__":
    asyncio.run(main())

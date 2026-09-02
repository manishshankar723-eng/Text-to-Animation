"""The SPEND GUARDS on animating an animatic frame with Veo.

This is the only path in the animatic editor that costs money, so the rules
around it are worth more than the feature: a mis-click here is a real charge on
someone's card. Every rule the 2026-08-07 Work Log entry established for
`server/videos.py` is asserted again for this router.

⚠ VEO IS NEVER CALLED. The worker submit is stubbed, so this suite spends
nothing — which is also the only way it can be run as often as it should be.
What is checked is everything AROUND the render: what gets priced, what gets
refused, and — the one that actually protects a paid clip — that the editor's
autosave cannot reach the render state.

    python tests/animate_guard_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("animate_guard_check_")

from fastapi.testclient import TestClient

from server import config, worker
from server.jobs import get_store
from server.main import app
from server.schemas import JobStatus

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


# --- Stub the render pool ----------------------------------------------------
# Without this the suite would submit real Veo work and bill for it. Record what
# WOULD have been rendered instead; that list is itself worth asserting on.
submitted: list[tuple[str, list[str], dict]] = []
worker.submit_animatic_animate = lambda job_id, clip_ids, render: submitted.append(
    (job_id, list(clip_ids), dict(render))
)

client = TestClient(app)
store = get_store()

email = f"_veo_{uuid.uuid4().hex[:10]}@example.com"
r = client.post("/auth/register", json={"email": email, "password": "veo-pass-12345"})
assert r.status_code == 201, r.text
auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

print(f"\nstore: {type(store).__name__}   batch cap: {config.MAX_VIDEO_BATCH}\n")


def make_animatic(n: int) -> tuple[str, list[str]]:
    """An animatic with `n` upload-backed frames. No files: nothing decodes here."""
    frames = [
        {
            "id": f"fr{i}",
            "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
            "duration_ms": 2000,
            "label": f"Shot {i + 1}",
        }
        for i in range(n)
    ]
    res = client.post("/animatics", headers=auth, json={"title": "Veo guards", "frames": frames})
    assert res.status_code == 201, res.text
    return res.json()["job_id"], [f["id"] for f in frames]


job_id, frame_ids = make_animatic(3)

# ---------------------------------------------------------------------------
print("[1] the estimate is FREE, and prices only what would actually render")
body = {
    "frame_ids": frame_ids,
    # Frame 3 is left promptless on purpose.
    "prompts": {frame_ids[0]: "he turns to camera", frame_ids[1]: "the leaves fall"},
    "render": {"tier": "fast", "resolution": "720p", "duration_seconds": 8,
               "generate_audio": True, "negative_prompt": ""},
}
r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth, json=body)
check("estimate -> 200", r.status_code, 200)
est = r.json()
check("a PROMPTLESS frame is not priced (2 of 3)", est["shots"], 2)
check("seconds is the sum of what renders", est["seconds"], 16)
check("it quotes a price above zero", est["usd"] > 0)
check("nothing was submitted by an estimate", submitted, [])
check("the animatic is untouched by an estimate", store.get(job_id).status, JobStatus.QUEUED)

# ---------------------------------------------------------------------------
print("\n[2] a request with nothing renderable is REFUSED, not billed")
r = client.post(f"/animatics/{job_id}/animate", headers=auth,
                json={"frame_ids": frame_ids, "prompts": {}, "render": body["render"]})
check("no prompts anywhere -> 409", r.status_code, 409)
check("and says why", "motion prompt" in r.json()["detail"])
check("still nothing submitted", submitted, [])

# ---------------------------------------------------------------------------
print("\n[3] the batch is CAPPED")
big_id, big_frames = make_animatic(config.MAX_VIDEO_BATCH + 1)
r = client.post(f"/animatics/{big_id}/animate", headers=auth, json={
    "frame_ids": big_frames,
    "prompts": {fid: "move" for fid in big_frames},
    "render": body["render"],
})
check(f"{config.MAX_VIDEO_BATCH + 1} frames -> 413", r.status_code, 413)
check("it names the limit", str(config.MAX_VIDEO_BATCH) in r.json()["detail"])
check("a refused batch submits nothing", submitted, [])
client.delete(f"/animatics/{big_id}", headers=auth)

# ---------------------------------------------------------------------------
print("\n[4] a real request queues, and the job goes RUNNING")
r = client.post(f"/animatics/{job_id}/animate", headers=auth, json=body)
check("animate -> 202", r.status_code, 202)
check("the price is in the message", "$" in r.json()["message"])
check("exactly the two prompted frames were submitted", len(submitted[0][1]), 2)
check("the render settings went with them", submitted[0][2]["resolution"], "720p")

job = store.get(job_id)
check("job is RUNNING", job.status, JobStatus.RUNNING)
clips = (job.result or {}).get("veo_clips") or []
check("two render records were written", len(clips), 2)
check("they are queued", {c["status"] for c in clips}, {"queued"})
check("each names the frame it came from", {c["frame_id"] for c in clips},
      {frame_ids[0], frame_ids[1]})
check("records live in RESULT, not params", "veo_clips" in (job.result or {}))
check("and NOT in params", "veo_clips" not in (job.params or {}))

# ---------------------------------------------------------------------------
print("\n[5] ⚠ THE AUTOSAVE CANNOT RACE A RENDER")
# This is the rule the whole design rests on. While a batch is in flight the
# server is the only writer to this job; a save that started before the render
# finished must not be able to roll the record back.
r = client.put(f"/animatics/{job_id}", headers=auth, json={"frames": []})
check("a save during a render -> 409", r.status_code, 409)
check("the frames were NOT wiped", len((store.get(job_id).params or {}).get("frames") or []), 3)
check("the render records survive", len((store.get(job_id).result or {}).get("veo_clips") or []), 2)

# ---------------------------------------------------------------------------
print("\n[6] a finished clip is server-owned: a later save cannot erase it")
from server.animatics import _write_veo_clip

ready_id = clips[0]["id"]
_write_veo_clip(job_id, ready_id, status="ready", upload_id="deadbeefcafe",
                duration_ms=8000, cost_usd=0.24)
# Let the batch "finish" so saving is allowed again.
store.update(job_id, status=JobStatus.QUEUED)

r = client.put(f"/animatics/{job_id}", headers=auth, json={"frames": [], "texts": []})
check("the save now succeeds", r.status_code, 200)
after = (store.get(job_id).result or {}).get("veo_clips") or []
check("a save that wiped every frame did NOT touch the records", len(after), 2)
ready = next(c for c in after if c["id"] == ready_id)
check("the paid clip is still ready", ready["status"], "ready")
check("its upload id survives", ready["upload_id"], "deadbeefcafe")
check("its cost survives", ready["cost_usd"], 0.24)
check("and the project reports it back",
      len(client.get(f"/animatics/{job_id}", headers=auth).json()["veo_clips"]), 2)

# ---------------------------------------------------------------------------
print("\n[7] a frame that already rendered is not silently re-rendered")
# Frame 0 now has a ready clip. Put the frames back first — the save above
# deliberately emptied them.
client.put(f"/animatics/{job_id}", headers=auth, json={"frames": [
    {"id": fid, "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
     "duration_ms": 2000, "label": ""} for fid in frame_ids
]})
again = {"frame_ids": frame_ids, "prompts": {fid: "move" for fid in frame_ids},
         "render": body["render"]}
r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth, json=again)
check("the rendered frame drops out of the estimate (2 of 3)", r.json()["shots"], 2)
r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth,
                json={**again, "force": True})
check("...unless force is asked for (3 of 3)", r.json()["shots"], 3)

# ---------------------------------------------------------------------------
print("\n[8] another account cannot see or spend on this animatic")
other = f"_veo2_{uuid.uuid4().hex[:10]}@example.com"
r = client.post("/auth/register", json={"email": other, "password": "veo-pass-12345"})
auth2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
check("GET -> 404", client.get(f"/animatics/{job_id}", headers=auth2).status_code, 404)
check("estimate -> 404",
      client.post(f"/animatics/{job_id}/animate/estimate", headers=auth2, json=again).status_code, 404)
check("animate -> 404",
      client.post(f"/animatics/{job_id}/animate", headers=auth2, json=again).status_code, 404)

# --- Clean up ---------------------------------------------------------------
store.update(job_id, status=JobStatus.QUEUED)
client.delete(f"/animatics/{job_id}", headers=auth)
client.delete("/auth/me", headers=auth)
client.delete("/auth/me", headers=auth2)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    print("\nA spend guard is broken. Do not ship this.")
    sys.exit(1)
print("Every spend guard holds, and a paid clip is out of the autosave's reach.")

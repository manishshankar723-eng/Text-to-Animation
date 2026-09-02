"""ONE VIDEO FROM ONE SENTENCE — the Media pane's ✨, Video tab.

    "i want same genearte any type of video from text prompt and user + upload
     amd drop image in prompt box and user generate both type video text and
     photo + text prompt fill and user click generate video buttun and gemini
     generate video an come back generated video in media under video and Video
     layer under in Timline"

---------------------------------------------------------------------------
WHAT THIS FILE IS ACTUALLY GUARDING
---------------------------------------------------------------------------
⚠ THIS IS THE SPENDING PATH, so most of these checks are about money. Veo is
billed per SECOND OF OUTPUT — a text-to-video clip costs exactly what animating
a panel costs — so the rule the rest of this editor follows applies here without
discount: nothing renders until a FREE estimate has been shown, both routes take
the same body so the quote can only be the price of what the button does, and a
promptless render is refused HERE rather than sent and charged for.

⚠ AND IT IS THE SAME RECORD AS ✨ ANIMATE, WITH NO `frame_id`. That one field is
the whole difference: `render_frame_clip` branches on it once, and everything
after — the Veo call, the retry policy, the upload, the paid record, the
self-heal that recovers a clip finished while the editor was closed — is shared.
A second render path would be a second place for a paid clip to go missing.

⚠ AND `text_only` IS ASKED FOR, NEVER INFERRED. `render_shot` still refuses a
missing image by default, because for every other caller the picture IS the shot
— a still that failed to load must never become a paid text-to-video render of
the motion notes.

⚠ VEO IS NEVER CALLED. Every route here is either free or is stopped before the
worker runs.

    python tests/video_generate_check.py
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

_TMP = pin("video_generate_check_")

from fastapi.testclient import TestClient
from PIL import Image

import server.worker as worker
from server.jobs import get_store
from server.main import app
from server.schemas import JobStatus

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
store = get_store()


def register():
    email = f"_vidgen_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "vidgen-pass-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


auth, email = register()
other_auth, _ = register()

print(f"\nstore: {type(store).__name__}\n")

# ⚠ THE WORKER IS STUBBED BEFORE THE FIRST RENDER CALL. Everything up to the
# submit is real — the guards, the record, the RUNNING flip — and Veo is not.
queued: list[dict] = []
worker.submit_animatic_animate = lambda job_id, clip_ids, render: queued.append(
    {"job_id": job_id, "clip_ids": list(clip_ids), "render": dict(render)}
)

r = client.post(
    "/animatics", headers=auth, json={"title": "Vid gen", "settings": {"aspect_ratio": "9:16"}}
)
assert r.status_code == 201, r.text
job_id = r.json()["job_id"]

RENDER = {"tier": "fast", "resolution": "720p", "duration_seconds": 8, "generate_audio": False}


def estimate(body):
    return client.post(f"/animatics/{job_id}/videos/generate/estimate", headers=auth, json=body)


def generate(body):
    return client.post(f"/animatics/{job_id}/videos/generate", headers=auth, json=body)


# ---------------------------------------------------------------------------
print("[1] the price is free, and it is the price of what the button does")
r = estimate({"prompt": "a slow dolly down a rain-soaked street", "render": RENDER})
check("-> 200", r.status_code, 200)
q = r.json()
check("one clip", q["shots"], 1)
check("…of the length that was asked for", q["seconds"], 8)
check("…at the tier that was asked for", q["tier"], "fast")
check("…and it costs something", q["usd"] > 0, True)
check("the estimate spends nothing", store.get(job_id).status, JobStatus.QUEUED)
check("…and queues nothing", queued, [])

dearer = estimate({
    "prompt": "x",
    "render": {**RENDER, "tier": "standard", "resolution": "1080p", "generate_audio": True},
}).json()
check("a dearer tier quotes more", dearer["usd"] > q["usd"], True)

# ---------------------------------------------------------------------------
print("\n[2] a render is queued as a FRAME-LESS Veo record")
r = generate({"prompt": "  a slow dolly down a rain-soaked street  ", "render": RENDER})
check("-> 202", r.status_code, 202)
check("the project goes RUNNING, so an autosave cannot land on it",
      store.get(job_id).status, JobStatus.RUNNING)
check("one clip was queued on the video pool", len(queued), 1)
check("…with the settings that were priced", queued[0]["render"]["duration_seconds"], 8)

clips = (store.get(job_id).result or {}).get("veo_clips") or []
check("a paid record exists before anything renders", len(clips), 1)
rec = clips[0]
check("⚠ IT HAS NO FRAME — that is what makes it a standalone render",
      rec["frame_id"], "")
check("no starting picture, so it is text-to-video", rec["source_upload_id"], "")
check("the prompt is stored trimmed", rec["prompt"], "a slow dolly down a rain-soaked street")
check("it is queued, not ready", rec["status"], "queued")
check("and it is NAMED, so the Media card has something to say",
      rec["label"].startswith("a slow dolly"))

# ---------------------------------------------------------------------------
print("\n[3] a second render is refused while one is in flight")
r = generate({"prompt": "another one", "render": RENDER})
check("-> 409 while RUNNING", r.status_code, 409)
check("…and nothing more was queued", len(queued), 1)

# Put the project back so the rest can run.
store.update(job_id, status=JobStatus.QUEUED, progress=None)

# ---------------------------------------------------------------------------
print("\n[4] a starting picture makes it image-to-video")
img = Image.new("RGB", (64, 64), (10, 20, 30))
import io as _io  # noqa: E402

buf = _io.BytesIO()
img.save(buf, "PNG")
r = client.post(
    f"/animatics/{job_id}/images",
    headers=auth,
    files={"files": ("start.png", buf.getvalue(), "image/png")},
)
check("the still uploads through the ORDINARY image route", r.status_code, 200)
upload_id = r.json()["items"][0]["upload_id"]

queued.clear()
r = generate({"prompt": "push in slowly", "source_upload_id": upload_id, "render": RENDER})
check("-> 202", r.status_code, 202)
rec = [c for c in (store.get(job_id).result or {}).get("veo_clips") or []
       if c["prompt"] == "push in slowly"][0]
check("the record names the starting picture", rec["source_upload_id"], upload_id)
check("…and still has no frame", rec["frame_id"], "")
store.update(job_id, status=JobStatus.QUEUED, progress=None)

# ---------------------------------------------------------------------------
print("\n[5] ⚠ THE REFUSALS — every one of these is money not spent")
queued.clear()
check("a blank prompt -> 400", generate({"prompt": "   ", "render": RENDER}).status_code, 400)
check("…and it never reached the queue", queued, [])

# ⚠ A NAMED PICTURE THAT IS NOT THERE IS A 400, NOT A QUIET TEXT-TO-VIDEO
# RENDER. The user chose a starting frame; rendering without it would bill them
# for something they did not ask for.
r = generate({"prompt": "x", "source_upload_id": "deadbeefcafe", "render": RENDER})
check("a starting picture that has gone -> 400", r.status_code, 400)
check("…and it says so plainly", "no longer in this project" in r.json()["detail"])
r = estimate({"prompt": "x", "source_upload_id": "deadbeefcafe", "render": RENDER})
check("…and the ESTIMATE refuses it too, so the price is never of a fiction",
      r.status_code, 400)
r = generate({"prompt": "x", "source_upload_id": "../../etc/passwd", "render": RENDER})
check("an upload id that is a path -> 400", r.status_code, 400)
check("nothing was queued by any refusal", queued, [])

r = client.post(
    f"/animatics/{job_id}/videos/generate", headers=other_auth, json={"prompt": "x"}
)
check("someone else's project -> 404", r.status_code, 404)

# ---------------------------------------------------------------------------
print("\n[6] ⚠ `text_only` IS ASKED FOR, NEVER INFERRED")
# The guard that stops a still which failed to load becoming a paid
# text-to-video render of the motion notes.
from video_client import VideoGenerationError, render_shot  # noqa: E402

try:
    render_shot(None, "a prompt")
    check("no image and no flag raises", False)
except VideoGenerationError as e:
    check("no image and no flag raises", "No source image" in str(e))
except Exception as e:  # noqa: BLE001
    check("no image and no flag raises VideoGenerationError", repr(e), "VideoGenerationError")

try:
    render_shot(None, "   ", text_only=True)
    check("text-only with no prompt still raises", False)
except VideoGenerationError as e:
    check("text-only with no prompt still raises", "no motion prompt" in str(e))

# ---------------------------------------------------------------------------
print("\n[7] the renderer branches on the frame, and shares everything else")
import inspect  # noqa: E402

import server.animatics as animatics  # noqa: E402

body = inspect.getsource(animatics.render_frame_clip)
check("it renders text-only ONLY when nothing was named",
      "text_only=not record.frame_id and not record.source_upload_id" in body)
check("a frame-less record with a source still loads that picture",
      "elif record.source_upload_id:" in body)
check("…and a missing one is an error, not a silent text-to-video render",
      "The starting picture for this render is missing" in body)
check("there is ONE call to Veo, not two", body.count("render_shot("), 1)

client.delete(f"/animatics/{job_id}", headers=auth)

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A sentence becomes a video, priced before it is paid for.")

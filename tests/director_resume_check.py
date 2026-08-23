"""KILL THE RENDER MID-BATCH, REOPEN, AND DO NOT PAY TWICE.

    python tests/director_resume_check.py

⚠ THIS IS THE TEST THAT EXISTS BECAUSE THE FAILURE COSTS MONEY. Phase C renders
every shot in the film in submissions of `MAX_VIDEO_BATCH`, and a 48-shot board
is four passes over several minutes. In that window a laptop sleeps, a tab is
closed, a browser is refreshed — and when the user comes back, twelve or
twenty-four clips have been rendered and CHARGED FOR, sitting on the server with
nothing on the timeline pointing at them.

There are two ways to get that wrong and both are expensive:

  · ABANDON THEM. The user pays again for footage they already own, and the only
    hint is a bigger invoice next month.
  · RE-SUBMIT THEM. Same charge, arrived at more actively.

So the property under test is one sentence: **resuming an interrupted pass
renders exactly the shots that were never submitted, and not one more.**

Six things are checked, in order of how much they matter:

  1. THE ARITHMETIC. `outstanding` splits the run's intention against the clip
     records: ready → done, never re-submitted; nothing → to do; failed →
     REPORTED, not retried, because Veo bills a failure exactly as it bills a
     success and an automatic retry on every reopen is a loop that spends.
  2. THE RECORD IS WRITTEN BEFORE THE MONEY MOVES. A run recorded after the
     first submission would be missing exactly the runs that need it.
  3. IT SURVIVES THE CRASH. The record is in the job's `result`, so the editor's
     autosave — which rewrites `params` wholesale — cannot erase it, and it comes
     back on the next `GET /animatics/{id}`.
  4. THE RESUME DOES NOT RE-PAY, and it is refused twice over: the browser sends
     only the outstanding shots, and `_animate_targets` would drop a paid one
     anyway. Both halves are asserted, because either alone is one bug away.
  5. THE RUN CLOSES. A record left saying "running" offers to resume a pass that
     finished, every time the project is opened.
  6. NOTHING IS OFFERED WHEN THERE IS NOTHING TO FINISH.

⚠ VEO IS NEVER CALLED. The render pool is stubbed and the "finished" clips are
written by hand, exactly as `animate_guard_check.py` does — which is the only way
a suite about paid renders can be run as often as it should be.

Needs node for the arithmetic half; the rest runs against the real routes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ===========================================================================
# 1. THE ARITHMETIC — what an interrupted pass still owes
# ===========================================================================
HARNESS = """
import { outstanding } from "__VEO__";

/** The run's intention: 24 shots, as `/director/{id}/veo/start` wrote it down. */
const shots = Array.from({ length: 24 }, (_, i) => ({
  shot: i + 1,
  frame_id: `f${i + 1}`,
  label: `Shot ${i + 1}`,
  prompt: "he turns to camera",
  seconds: 4,
  hold_ms: 2400,
}));

/** A clip record, as the server keeps it. */
const clip = (n, status, extra = {}) => ({
  id: `c${n}`,
  frame_id: `f${n}`,
  status,
  upload_id: status === "ready" ? `up${n}` : "",
  cost_usd: status === "ready" ? 0.48 : 0,
  ...extra,
});

const out = {};

// ⚠ THE CRASH, EXACTLY: pass one (12) finished and was paid for, pass two never
// went. Nothing on the server is mid-flight, because the process that would have
// been doing it is gone.
out.crashed = outstanding(shots, Array.from({ length: 12 }, (_, i) => clip(i + 1, "ready")));

// The same run, killed WHILE pass two was in flight — the browser died but the
// server did not, so twelve are ready and twelve are still rendering.
out.midflight = outstanding(shots, [
  ...Array.from({ length: 12 }, (_, i) => clip(i + 1, "ready")),
  ...Array.from({ length: 12 }, (_, i) => clip(i + 13, "rendering")),
]);

// One shot FAILED. It has already been billed and will very likely fail again
// for the same reason, so it is reported rather than retried.
out.failed = outstanding(shots, [
  ...Array.from({ length: 11 }, (_, i) => clip(i + 1, "ready")),
  clip(12, "failed", { error: "Veo refused the prompt." }),
]);

// ⚠ READY WINS OVER EVERYTHING. A shot rendered twice — a failure and then a
// success — has two records, and the one that matters is the one with a file.
out.retried = outstanding(shots.slice(0, 2), [
  clip(1, "failed", { error: "transient" }),
  clip(1, "ready"),
  clip(2, "queued"),
]);

// Nothing was ever submitted: everything is still to do, and nothing is owed.
out.untouched = outstanding(shots, []);

// The pass finished: nothing left, and the money is all accounted for.
out.finished = outstanding(shots, shots.map((s, i) => clip(i + 1, "ready")));

// ⚠ A `ready` RECORD WITH NO FILE BEHIND IT IS NOT DONE. `_animate_targets`
// asks for `status == "ready" AND upload_id`, and so does this — the two must
// agree or the browser skips a shot the server would happily render.
out.halfReady = outstanding(shots.slice(0, 1), [
  { id: "cx", frame_id: "f1", status: "ready", upload_id: "", cost_usd: 0 },
]);

const shape = (o) => ({
  done: o.done.map((s) => s.shot),
  todo: o.todo.map((s) => s.shot),
  failed: o.failed.map((s) => ({ shot: s.shot, why: s.why })),
  inFlight: o.inFlight.map((s) => s.shot),
  paidUsd: o.paidUsd,
});
process.stdout.write(
  JSON.stringify(Object.fromEntries(Object.entries(out).map(([k, v]) => [k, shape(v)])))
);
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-resume-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS.replace("__VEO__", (AGENT / "veo_pass.js").as_uri()))
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def arithmetic_half():
    data = run_node()
    if data is None:
        print("  node is not on PATH, or veo_pass.js would not load — nothing checked.")
        return False

    print("\n⚠ WHAT AN INTERRUPTED PASS STILL OWES — the record says what was\n"
          "  INTENDED, the clips say what was PAID FOR, and this is the difference\n")
    c = data["crashed"]
    check("⚠ THE TWELVE ALREADY RENDERED ARE DONE, AND NOT IN THE WORK LIST",
          c["done"] == list(range(1, 13)) and not set(c["done"]) & set(c["todo"]),
          json.dumps(c["done"]))
    check("⚠ ...AND THE TWELVE NEVER SUBMITTED ARE THE WHOLE OF IT",
          c["todo"] == list(range(13, 25)), json.dumps(c["todo"]))
    check("nothing is lost between the two",
          len(c["done"]) + len(c["todo"]) + len(c["failed"]) + len(c["inFlight"]) == 24,
          json.dumps([len(c["done"]), len(c["todo"])]))
    check("...and it says what has already been spent, off the records themselves",
          c["paidUsd"] == 5.76, str(c["paidUsd"]))

    m = data["midflight"]
    check("⚠ A CLIP STILL RENDERING IS NEITHER DONE NOR TO DO — the server may yet\n"
          "       finish it, and re-submitting it is how you pay for it twice",
          m["inFlight"] == list(range(13, 25)) and m["todo"] == [],
          json.dumps([m["inFlight"], m["todo"]]))

    f = data["failed"]
    check("⚠ A FAILED RENDER IS REPORTED, NOT RETRIED — Veo bills a failure like a\n"
          "       success, so an automatic retry on every reopen is a loop that spends",
          [row["shot"] for row in f["failed"]] == [12] and 12 not in f["todo"],
          json.dumps(f["failed"]))
    check("...carrying the server's own reason, so the panel can print it",
          f["failed"] and "refused the prompt" in f["failed"][0]["why"],
          json.dumps(f["failed"]))

    r = data["retried"]
    check("⚠ READY WINS OVER A FAILURE ON THE SAME SHOT — two records, one file,\n"
          "       and the one with the file is the one that counts",
          r["done"] == [1] and 1 not in r["todo"] and not r["failed"],
          json.dumps(r))
    check("...and a queued one beside it is still in flight, not to do",
          r["inFlight"] == [2] and r["todo"] == [], json.dumps(r))

    check("a run that never submitted anything owes all of it and has spent nothing",
          data["untouched"]["todo"] == list(range(1, 25))
          and data["untouched"]["paidUsd"] == 0,
          json.dumps(data["untouched"]["paidUsd"]))
    check("a run that finished owes nothing",
          data["finished"]["todo"] == [] and len(data["finished"]["done"]) == 24,
          json.dumps(len(data["finished"]["done"])))
    check("⚠ ...AND A `ready` RECORD WITH NO FILE BEHIND IT IS NOT DONE — the same\n"
          "       test `_animate_targets` makes, or the two disagree about a shot",
          data["halfReady"]["todo"] == [1] and data["halfReady"]["done"] == [],
          json.dumps(data["halfReady"]))
    return True


# ===========================================================================
# 2..6 — THE RUN RECORD, AND THE RESUME, AGAINST THE REAL ROUTES
# ===========================================================================
def server_half():
    from fastapi.testclient import TestClient

    from server import worker
    from server.animatics import _write_veo_clip
    from server.jobs import get_store
    from server.main import app
    from server.schemas import JobStatus, RenderSettings

    # ⚠ WITHOUT THIS THE SUITE WOULD SUBMIT REAL VEO WORK AND BILL FOR IT.
    submitted: list[tuple[str, list[str], dict]] = []
    worker.submit_animatic_animate = lambda job_id, clip_ids, render: submitted.append(
        (job_id, list(clip_ids), dict(render))
    )

    client = TestClient(app)
    store = get_store()
    email = f"_resume_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "resume-pass-12345"})
    assert r.status_code == 201, r.text
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    render = RenderSettings().model_dump()
    N = 6           # a small film, so the batch cap is not what is being tested
    FIRST = 3       # how much of it "finished" before the crash

    frames = [
        {
            "id": f"fr{i}",
            "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
            "duration_ms": 2400,
            "label": f"Shot {i + 1}",
        }
        for i in range(N)
    ]
    res = client.post("/animatics", headers=auth, json={"title": "Resume", "frames": frames})
    assert res.status_code == 201, res.text
    job_id = res.json()["job_id"]
    ids = [f["id"] for f in frames]
    shots = [
        {
            "shot": i + 1, "frame_id": ids[i], "label": f"Shot {i + 1}",
            "prompt": "he turns to camera", "seconds": 4, "hold_ms": 2400,
        }
        for i in range(N)
    ]

    print("\n⚠ THE RECORD IS WRITTEN BEFORE THE MONEY MOVES — a run recorded after\n"
          "  the first submission would be missing exactly the runs that need it\n")
    r = client.post(f"/director/{job_id}/veo/start", headers=auth,
                    json={"shots": shots, "render": render})
    check("`/veo/start` answers", r.status_code == 200, r.text[:200])
    run = r.json()
    check("it opens the run as running", run["status"] == "running", run["status"])
    check("...with the whole intention written down",
          len(run["shots"]) == N, str(len(run["shots"])))
    check("...each shot carrying the length the Director chose for it",
          {s["seconds"] for s in run["shots"]} == {4},
          json.dumps([s["seconds"] for s in run["shots"]]))
    check("...and what the pass was quoted at, before a penny of it moved",
          run["quoted_usd"] > 0, str(run["quoted_usd"]))
    check("⚠ OPENING A RUN SPENDS NOTHING AND SUBMITS NOTHING",
          submitted == [], json.dumps(submitted))
    check("...and does not touch the project's status",
          store.get(job_id).status == JobStatus.QUEUED, str(store.get(job_id).status))
    check("⚠ THE RECORD LIVES IN `result`, WHERE THE AUTOSAVE CANNOT REACH IT",
          "director_run" in (store.get(job_id).result or {}))
    check("...and NOT in `params`, which a save rewrites wholesale",
          "director_run" not in (store.get(job_id).params or {}))

    print("\n⚠ PASS ONE GOES, AND IS PAID FOR\n")
    first = shots[:FIRST]
    r = client.post(f"/animatics/{job_id}/animate", headers=auth, json={
        "frame_ids": [s["frame_id"] for s in first],
        "prompts": {s["frame_id"]: s["prompt"] for s in first},
        "durations": {s["frame_id"]: s["seconds"] for s in first},
        "render": render,
    })
    check("the pass is accepted", r.status_code == 202, r.text[:200])
    check(f"exactly {FIRST} renders were submitted", len(submitted[0][1]) == FIRST,
          str(len(submitted[0][1])))

    # The worker "finishes" them — this is what `render_frame_clip` writes.
    clips = (store.get(job_id).result or {}).get("veo_clips") or []
    for c in clips:
        _write_veo_clip(job_id, c["id"], status="ready",
                        upload_id=uuid.uuid4().hex[:12], duration_ms=4000, cost_usd=0.48)

    print("\n⚠ NOW KILL IT — the browser is gone, and everything the run knows is on\n"
          "  the server. The autosave that follows the reload must not erase it.\n")
    # A crashed browser leaves the job in whatever state the worker left it;
    # a finished batch puts it back to QUEUED, which is when a save is allowed.
    store.update(job_id, status=JobStatus.QUEUED)
    r = client.put(f"/animatics/{job_id}", headers=auth,
                   json={"frames": frames, "texts": [], "shapes": []})
    check("the editor's autosave succeeds after the crash", r.status_code == 200,
          r.text[:200])

    r = client.get(f"/animatics/{job_id}", headers=auth)
    check("the project reads back", r.status_code == 200, r.text[:200])
    project = r.json()
    check("⚠ THE RUN CAME BACK, AND IT STILL SAYS `running` — which is the whole\n"
          "       signal that a pass was interrupted",
          (project.get("director_run") or {}).get("status") == "running",
          json.dumps(project.get("director_run") and project["director_run"]["status"]))
    check("...with every shot it meant to render",
          len(project["director_run"]["shots"]) == N,
          str(len(project["director_run"]["shots"])))
    check("⚠ ...AND A SAVE THAT REWROTE THE WHOLE DOCUMENT DID NOT TOUCH IT",
          project["director_run"]["id"] == run["id"])
    ready = [c for c in project["veo_clips"] if c["status"] == "ready" and c["upload_id"]]
    check(f"the {FIRST} paid clips came back too", len(ready) == FIRST, str(len(ready)))

    print("\n⚠ RESUME — and the paid clips are not rendered a second time\n")
    # What the browser works out (`outstanding`): the shots with no ready clip.
    done_ids = {c["frame_id"] for c in ready}
    todo = [s for s in project["director_run"]["shots"] if s["frame_id"] not in done_ids]
    check("⚠ THE BROWSER'S WORK LIST IS THE UNPAID SHOTS ONLY",
          len(todo) == N - FIRST and not {s["frame_id"] for s in todo} & done_ids,
          json.dumps([s["shot"] for s in todo]))

    submitted.clear()
    r = client.post(f"/animatics/{job_id}/animate", headers=auth, json={
        "frame_ids": [s["frame_id"] for s in todo],
        "prompts": {s["frame_id"]: s["prompt"] for s in todo},
        "durations": {s["frame_id"]: s["seconds"] for s in todo},
        "render": render,
    })
    check("the resumed pass is accepted", r.status_code == 202, r.text[:200])
    check(f"⚠ IT RENDERS THE {N - FIRST} OUTSTANDING SHOTS AND NOTHING ELSE",
          len(submitted[0][1]) == N - FIRST, str(len(submitted[0][1])))

    print("\n⚠ AND THE SERVER REFUSES TO RE-PAY EVEN IF ASKED — the second half of\n"
          "  the rule, because either half alone is one bug away from an invoice\n")
    store.update(job_id, status=JobStatus.QUEUED)
    submitted.clear()
    # A browser that had forgotten what it already bought, asking for ALL of them.
    r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth, json={
        "frame_ids": ids,
        "prompts": {fid: "he turns to camera" for fid in ids},
        "durations": {fid: 4 for fid in ids},
        "render": render,
    })
    check(f"⚠ THE PAID SHOTS ARE NOT EVEN PRICED — {N} asked for, {N - FIRST} quoted",
          r.json()["shots"] == N - FIRST, str(r.json()["shots"]))
    r = client.post(f"/animatics/{job_id}/animate", headers=auth, json={
        "frame_ids": ids,
        "prompts": {fid: "he turns to camera" for fid in ids},
        "durations": {fid: 4 for fid in ids},
        "render": render,
    })
    check("⚠ ...AND NOT RENDERED. `_animate_targets` drops a frame that already\n"
          "       has a ready clip, so a browser bug cannot spend twice",
          len(submitted[0][1]) == N - FIRST, str(len(submitted[0][1])))

    # ⚠ AND `force` IS THE DELIBERATE WAY PAST IT — a separate, differently
    # worded action in the UI ("Render again"), never a silent retry. Asserted so
    # the guard above is proved to be a GUARD rather than a broken code path that
    # happens to render fewer shots.
    store.update(job_id, status=JobStatus.QUEUED)
    submitted.clear()
    r = client.post(f"/animatics/{job_id}/animate", headers=auth, json={
        "frame_ids": ids,
        "prompts": {fid: "he turns to camera" for fid in ids},
        "durations": {fid: 4 for fid in ids},
        "render": render,
        "force": True,
    })
    check("...and `force` renders all of them on purpose, which is what makes the "
          "refusal above a guard rather than a dead branch",
          len(submitted[0][1]) == N, str(len(submitted[0][1])))

    print("\n⚠ THE RUN CLOSES — a record left saying `running` offers to resume a\n"
          "  pass that finished, every single time the project is opened\n")
    store.update(job_id, status=JobStatus.QUEUED)
    r = client.post(f"/director/{job_id}/veo/state", headers=auth,
                    json={"run_id": run["id"], "status": "done"})
    check("`/veo/state` answers", r.status_code == 200, r.text[:200])
    check("the run is done", r.json()["status"] == "done", r.json()["status"])
    check("⚠ ...AND THE SHOT LIST WAS NOT REWRITTEN — what a run intended is settled\n"
          "       when it opens; how far it got is a question for `veo_clips`",
          len(r.json()["shots"]) == N, str(len(r.json()["shots"])))
    r = client.get(f"/animatics/{job_id}", headers=auth)
    check("...so the next load offers nothing to resume",
          r.json()["director_run"]["status"] == "done",
          r.json()["director_run"]["status"])

    r = client.post(f"/director/{job_id}/veo/state", headers=auth,
                    json={"run_id": "someoldrun", "status": "stopped"})
    check("⚠ A TAB LEFT OPEN ON AN ABANDONED RUN CANNOT CLOSE THE CURRENT ONE",
          r.status_code == 200 and r.json()["status"] == "done", r.text[:120])
    r = client.post(f"/director/{job_id}/veo/state", headers=auth,
                    json={"run_id": run["id"], "status": "nonsense"})
    check("...and an unknown status is refused rather than written", r.status_code == 400,
          str(r.status_code))

    print("\n⚠ NOTHING TO FINISH IS NOTHING OFFERED\n")
    res = client.post("/animatics", headers=auth, json={"title": "Fresh", "frames": frames})
    fresh_id = res.json()["job_id"]
    r = client.get(f"/animatics/{fresh_id}", headers=auth)
    check("a project that never ran 🎬 carries no run at all",
          r.json().get("director_run") is None, json.dumps(r.json().get("director_run")))
    r = client.post(f"/director/{fresh_id}/veo/state", headers=auth, json={"status": "done"})
    check("...and closing a run that does not exist is a 404, not a silent write",
          r.status_code == 404, str(r.status_code))
    r = client.post(f"/director/{fresh_id}/veo/start", headers=auth,
                    json={"shots": [], "render": render})
    check("...and a run with nothing in it is refused",
          r.status_code == 400, str(r.status_code))

    client.delete(f"/animatics/{job_id}", headers=auth)
    client.delete(f"/animatics/{fresh_id}", headers=auth)


def main():
    ran = arithmetic_half()
    server_half()
    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    if not ran:
        print("The server half passed; the arithmetic half needs node.")
        return 2
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

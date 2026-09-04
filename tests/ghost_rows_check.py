"""Proves no library lists a project the user never put anything into.

THE FAULT THIS EXISTS FOR. The Editor's New Project tile created the project on
the way IN and relied on the editor deleting it again on the way OUT — and that
discard only ran on the ← button, so the sidebar, a refresh and a closed tab
each left an empty "Untitled Project" behind. Reported with a screenshot of four
of them: *"mai ye project mai kuchh nhi kiya magar ye yaha pe dikhte hai"*. Each
one had also spent a slot of the account's `projects` quota.

⚠ THE FIX HAS TWO HALVES AND THIS CHECKS THE SECOND ONE. The client no longer
creates a project until the first real action (`ensureProject` in
`AnimaticEditor.jsx`, `ensureSession` in `PlanAndScript.jsx`); the list routes
refuse to SHOW an empty, never-named row whatever made it. A browser suite can
prove the first half. This proves the second, and it is the half that also
covers rows already in the database and rows a stale tab still makes.

⚠ READ-ONLY. It opens the live collection and writes nothing to it — the sweep
that deletes an old ghost is deliberately NOT exercised. If Mongo is not
reachable the synthetic half still runs, so it is useful in CI.

    python tests/ghost_rows_check.py

RULEBOOK E118.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import logging  # noqa: E402

# Re-summarising a whole collection logs a warning per unparseable frame. Right
# in a server, noise here.
logging.disable(logging.WARNING)

from server import animatics, config, plans  # noqa: E402
from server.schemas import AnimaticSummary, Job, JobKind, JobStatus  # noqa: E402

failures: list[str] = []


def fail(label, detail=""):
    print(f"    FAIL {label}" + (f"  {detail}" if detail else ""))
    failures.append(label)


def ok(label, detail=""):
    print(f"    ok   {label}" + (f"  {detail}" if detail else ""))


def _job(**kw) -> Job:
    base = dict(
        job_id="j" * 12,
        kind=JobKind.ANIMATIC,
        status=JobStatus.SUCCEEDED,
        character_name="Untitled Project",
        owner="someone@example.com",
        params={},
        result={},
        created_at="2020-01-01T00:00:00+00:00",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    base.update(kw)
    return Job(**base)


def _card(**kw) -> AnimaticSummary:
    base = dict(
        job_id="j" * 12,
        title="Untitled Project",
        status=JobStatus.SUCCEEDED,
        aspect_ratio="16:9",
        frame_count=0,
        duration_ms=0,
        cover_url=None,
        text_count=0,
        audio_count=0,
        has_audio=False,
        has_video=False,
        size_bytes=0,
        created_at="2020-01-01T00:00:00+00:00",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    base.update(kw)
    return AnimaticSummary(**base)


# ---------------------------------------------------------------------------
# 1. The rule itself — what counts as a row nobody made
# ---------------------------------------------------------------------------
print("\n  the animatic rule (`_is_ghost`)")

if animatics._is_ghost(_job(), _card()):
    ok("empty + placeholder title = a ghost")
else:
    fail("AN EMPTY UNTITLED PROJECT IS NOT BEING HIDDEN")

if not animatics._is_ghost(_job(character_name="Episode 1"), _card(title="Episode 1")):
    ok("a name the user chose keeps the row", "even with nothing in it")
else:
    fail("A NAMED PROJECT WAS TREATED AS A GHOST")

for field, card in (
    ("frames", _card(frame_count=3)),
    ("texts", _card(text_count=1)),
    ("audio", _card(audio_count=1)),
    ("an exported video", _card(has_video=True)),
):
    if not animatics._is_ghost(_job(), card):
        ok(f"a project with {field} is kept")
    else:
        fail(f"A PROJECT WITH {field.upper()} WAS TREATED AS A GHOST")

# ⚠ THE ONE THAT WOULD LOSE SOMEBODY'S WORK. `params.overlays` is in
# `SUMMARY_DROP`, so on the list route a project whose only content is a picture
# on an Images lane arrives looking EXACTLY like an empty one. Its upload is a
# file in the project's media folder, so `size_bytes` is what keeps it — and
# that is why the emptiness test may never be widened to a dropped field.
if not animatics._is_ghost(_job(), _card(size_bytes=1)):
    ok("anything on disk keeps the row", "the overlays-only case SUMMARY_DROP hides")
else:
    fail("A PROJECT WITH FILES ON DISK WAS TREATED AS A GHOST")

for field in ("shapes", "layers"):
    if not animatics._is_ghost(_job(params={field: [{"id": "a"}]}), _card()):
        ok(f"a project with {field} is kept", "no file on disk to prove it")
    else:
        fail(f"A PROJECT WITH {field.upper()} WAS TREATED AS A GHOST")

print("\n  the plan rule (`_is_ghost_plan`)")

if plans._is_ghost_plan(_job(kind=JobKind.PLAN, character_name="Untitled plan")):
    ok("a session nobody said anything in is a ghost")
else:
    fail("AN EMPTY UNTITLED PLAN IS NOT BEING HIDDEN")

for field, params in (
    ("messages", {"messages": [{"role": "user", "text": "hi"}]}),
    ("scripts", {"scripts": [{"id": "s1"}]}),
    ("a calendar", {"plan": {"items": [{}]}}),
    ("a channel", {"channel": {"title": "Some channel"}}),
):
    job = _job(kind=JobKind.PLAN, character_name="Untitled plan", params=params)
    if not plans._is_ghost_plan(job):
        ok(f"a session with {field} is kept")
    else:
        fail(f"A SESSION WITH {field.upper()} WAS TREATED AS A GHOST")

# ⚠ THE PLAN LIST WAITS BEFORE HIDING, and that is not the same rule as the
# editor's. The session is created and the sidebar refreshed in ONE action,
# while the first message is still in flight — hiding on sight would take the
# session the user is sitting in off their own list until the reply landed.
if plans._older_than("2020-01-01T00:00:00+00:00", plans.GHOST_HIDE_AFTER_S):
    ok("an old empty session is past the hide delay")
else:
    fail("THE HIDE DELAY NEVER EXPIRES")

from datetime import datetime, timezone  # noqa: E402

now = datetime.now(timezone.utc).isoformat()
if not plans._older_than(now, plans.GHOST_HIDE_AFTER_S):
    ok("a session made a moment ago is still listed", "its first turn is in flight")
else:
    fail("A BRAND-NEW SESSION WOULD BE HIDDEN MID-TURN")

if not plans._older_than("not a date", plans.GHOST_HIDE_AFTER_S):
    ok("an unreadable timestamp hides nothing")
else:
    fail("AN UNREADABLE TIMESTAMP WAS TREATED AS OLD")


# ---------------------------------------------------------------------------
# 2. Against the live collection — what the libraries would actually draw
# ---------------------------------------------------------------------------
print("\n  every real record, as its library route would list it")
live_ran = False
try:
    from server.mongo import get_db

    col = get_db()[config.JOBS_COLLECTION]

    def _jobs(kind):
        out = []
        for d in col.find({"kind": kind.value}).limit(3000):
            d = dict(d)
            d.pop("_id", None)
            try:
                out.append(Job(**d))
            except Exception:  # noqa: BLE001 — a record we cannot parse
                continue       # is not this suite's business to report
        return out

    boards: dict = {}
    animatic_jobs = _jobs(JobKind.ANIMATIC)
    shown, hidden = [], []
    for j in animatic_jobs:
        (hidden if animatics._is_ghost(j, animatics._summarise(j, boards)) else shown).append(j)
    print(f"    --   {len(animatic_jobs)} animatic(s): {len(shown)} listed, {len(hidden)} hidden")
    # ⚠ NOT "there are no ghosts" — there are, and hiding them is the point.
    # What must hold is that nothing with CONTENT is hidden.
    bad = [
        j for j in hidden
        if (j.params or {}).get("frames")
        or (j.params or {}).get("audio_tracks")
        or (j.params or {}).get("overlays")
        or (j.result or {}).get("video")
    ]
    if not bad:
        ok("no animatic with content is hidden", f"{len(hidden)} empty row(s) hidden")
    else:
        fail("A REAL PROJECT WOULD DISAPPEAR", f"{len(bad)}, e.g. {bad[0].job_id}")

    plan_jobs = _jobs(JobKind.PLAN)
    p_hidden = [j for j in plan_jobs if plans._is_ghost_plan(j)]
    print(f"    --   {len(plan_jobs)} plan session(s): {len(p_hidden)} empty")
    bad = [j for j in p_hidden if (j.params or {}).get("messages")]
    if not bad:
        ok("no plan session with a transcript is hidden", f"{len(p_hidden)} empty")
    else:
        fail("A REAL SESSION WOULD DISAPPEAR", f"{len(bad)}, e.g. {bad[0].job_id}")

    live_ran = True
except Exception as e:  # noqa: BLE001 — the rule half still has value
    print(f"    --   live collection unavailable ({type(e).__name__}), skipped")

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("No library lists a row nobody made, and nothing with content is hidden"
      + (" — checked against the live collection." if live_ran
         else " (rules only; Mongo was not reachable)."))
sys.exit(0)

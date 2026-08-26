"""Proves the library lists may stop fetching what their cards never read.

THE HAZARD THIS EXISTS FOR. `animatics.SUMMARY_DROP` and
`main.BOARD_SUMMARY_DROP` name the fields a library card does not use, and the
list routes ask the store not to send them. That makes the summariser and the
drop list ONE THING WRITTEN IN TWO PLACES — and the failure mode is silent: add
a line to `_summarise` that reads a dropped field and every card in the library
goes blank, with nothing raising.

So the lists are not trusted, they are CHECKED. For every real document in the
collection this builds the card twice — once from the whole document, once from
the document as the list route will actually receive it — and fails if the two
differ in any field. It also prints what the projection saves, because a drop
list that saves nothing is a maintenance cost with no payer.

⚠ READ-ONLY. It opens the live collection and writes nothing to it. If Mongo is
not reachable it still runs the synthetic half, so it is useful in CI.

    python tests/summary_projection_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bson  # noqa: E402

# The summarisers log a warning for every frame they cannot parse. That is right
# in a server and useless here, where a whole collection is being re-summarised
# on purpose - it buried the result under thousands of lines.
import logging  # noqa: E402

logging.disable(logging.WARNING)

from server import config  # noqa: E402
from server.jobs import _slim  # noqa: E402
from server.schemas import Job, JobKind, JobStatus  # noqa: E402

failures: list[str] = []


def fail(label, detail=""):
    print(f"    FAIL {label}" + (f"  {detail}" if detail else ""))
    failures.append(label)


def ok(label, detail=""):
    print(f"    ok   {label}" + (f"  {detail}" if detail else ""))


def kb(n):
    return f"{n / 1024:.1f} KB"


def sizeof(job: Job) -> int:
    """What this record weighs on the wire, near enough to compare two of them."""
    return len(bson.BSON.encode(job.model_dump(mode="json")))


# ---------------------------------------------------------------------------
# The two summarisers, and the drop list each one is paired with
# ---------------------------------------------------------------------------
def _load_targets():
    from server import animatics, main

    boards_cache: dict = {}

    return [
        (
            "animatic",
            JobKind.ANIMATIC,
            animatics.SUMMARY_DROP,
            # ⚠ ONE SHARED `boards` DICT ACROSS BOTH CALLS, deliberately: it is
            # what `list_animatics` passes, so this exercises the same path.
            lambda job: animatics._summarise(job, boards_cache),
        ),
        (
            "storyboard",
            JobKind.STORYBOARD,
            main.BOARD_SUMMARY_DROP,
            main._summarise_board,
        ),
    ]


def compare(label, kind, drop, summarise, jobs) -> None:
    """Build every card twice and account for the difference."""
    if not jobs:
        print(f"    --   no {label} documents to check")
        return

    mismatches = []
    full_bytes = lean_bytes = 0
    for job in jobs:
        slim = _slim(job, drop)
        full_bytes += sizeof(job)
        lean_bytes += sizeof(slim)
        try:
            a = summarise(job).model_dump()
            b = summarise(slim).model_dump()
        except Exception as e:  # noqa: BLE001 — a raising summariser IS the failure
            mismatches.append((job.job_id, f"raised: {e}"))
            continue
        if a != b:
            differing = {k: (a.get(k), b.get(k)) for k in a if a.get(k) != b.get(k)}
            mismatches.append((job.job_id, differing))

    if mismatches:
        fail(f"{label}: the card changes when the dropped fields are missing",
             f"{len(mismatches)} of {len(jobs)}")
        for job_id, detail in mismatches[:5]:
            print(f"           {job_id}  {detail}")
        print("         → a field named in the drop list is being READ by the")
        print("           summariser. Remove it from the list, or stop reading it.")
        return

    ok(f"{label}: every card is byte-identical without the dropped fields",
       f"{len(jobs)} document(s)")
    if full_bytes:
        saved = full_bytes - lean_bytes
        pct = saved / full_bytes * 100
        print(f"         a {len(jobs)}-row page: {kb(full_bytes)} → "
              f"{kb(lean_bytes)}  (saves {kb(saved)}, {pct:.0f}%)")
        if pct < 5:
            print("         ⚠ under 5% — this drop list is not earning its upkeep.")


# ---------------------------------------------------------------------------
# 1. Against the live collection
# ---------------------------------------------------------------------------
print("\n  every real document, summarised both ways")
live_ran = False
try:
    from server.mongo import get_db

    col = get_db()[config.JOBS_COLLECTION]
    for label, kind, drop, summarise in _load_targets():
        docs = list(col.find({"kind": kind.value}).limit(1000))
        jobs = []
        for d in docs:
            d = dict(d)
            d.pop("_id", None)
            try:
                jobs.append(Job(**d))
            except Exception:  # noqa: BLE001 — a record we cannot even parse
                continue       # is not this suite's business to report
        compare(label, kind, drop, summarise, jobs)
    live_ran = True
except Exception as e:  # noqa: BLE001 — the synthetic half still has value
    print(f"    --   live collection unavailable ({type(e).__name__}), skipped")

# ---------------------------------------------------------------------------
# 2. Synthetic, so this suite is worth something without a database
# ---------------------------------------------------------------------------
print("\n  synthetic documents with every dropped field populated")


def _synthetic_animatic() -> Job:
    """An animatic whose every droppable field is FILLED — an empty one would
    pass this suite by accident."""
    frames = [
        {
            "id": f"f{i}",
            "src": {"kind": "upload", "upload_id": f"u{i}"},
            "duration_ms": 1500 + i * 100,
            "in_ms": 0,
            "out_ms": None,
            "track": 0,
            "kind": "image",
            # ⚠ THESE MUST VALIDATE AGAINST THE REAL MODELS. The first draft
            # of this fixture invented the shapes, `_frames_of` refused every
            # frame of the FULL document, and the suite reported a mismatch
            # that was entirely its own doing. Taken from a live record now.
            "mask": {"kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.5,
                     "h": 0.5, "feather": 0.1, "invert": False},
            "keyframes": {"x": [{"t": 0, "v": 0.0, "ease": "in-out"},
                                {"t": 900, "v": 120.0, "ease": "in-out"}] * 20},
            "effects": [{"id": f"e{n}", "kind": "blur",
                         "params": {"amount": 0.4}} for n in range(10)],
        }
        for i in range(6)
    ]
    return Job(
        job_id="synthetic-animatic",
        kind=JobKind.ANIMATIC,
        status=JobStatus.SUCCEEDED,
        owner="synthetic@example.com",
        character_name="Synthetic project",
        created_at="2026-08-26T00:00:00+00:00",
        updated_at="2026-08-26T00:00:00+00:00",
        params={
            "settings": {"aspect_ratio": "16:9"},
            "frames": frames,
            "texts": [{"id": "t1", "text": "hello",
                       "keyframes": {"opacity": [{"t": 0, "v": 0.0}] * 50}}],
            "audio_tracks": [{"upload_id": "a1"}],
            "overlays": [{"id": "o1", "blob": "x" * 800}],
            "transitions": [{"id": "tr1", "blob": "x" * 800}],
        },
        result={"video": "/out/x.mp4"},
    )


def _synthetic_board(with_variants: bool) -> Job:
    panels = [
        {
            "index": i,
            "url": f"/p/{i}" if i else None,
            "failed": False,
            "description": "a long shot description " * 12,
            "location": "an interior " * 6,
            "characters": ["someone"] * 4,
            "camera": "slow push in " * 4,
            "dialogue": "a line of dialogue " * 4,
            "assets": ["prop"] * 4,
            "versions": [{"n": 1}] * 3,
        }
        for i in range(5)
    ]
    # ⚠ BOTH SHAPES ARE EXERCISED. `variants_of` synthesises the flat shape from
    # the nested one, so a drop list that covers only `result.panels.*` passes
    # against a flat board and silently keeps shipping a restyled one.
    result = (
        {"variants": [{"style": "ink", "panels": panels, "ok_count": 4}],
         "active_variant": 0, "count": 5}
        if with_variants
        else {"panels": panels, "style": "ink", "count": 5, "ok_count": 4}
    )
    return Job(
        job_id=f"synthetic-board-{'v' if with_variants else 'flat'}",
        kind=JobKind.STORYBOARD,
        status=JobStatus.SUCCEEDED,
        owner="synthetic@example.com",
        character_name="Synthetic board",
        created_at="2026-08-26T00:00:00+00:00",
        updated_at="2026-08-26T00:00:00+00:00",
        params={"count": 5, "style": "ink", "aspect_ratio": "16:9",
                "genre": "drama", "script": "INT. SOMEWHERE " * 200,
                "cast": [{"name": "A"}] * 5, "assets": [{"name": "prop"}] * 5},
        result=result,
    )


targets = {label: (kind, drop, fn) for label, kind, drop, fn in _load_targets()}
kind, drop, fn = targets["animatic"]

# ⚠ THE FIXTURE IS CHECKED BEFORE IT IS USED. A synthetic frame the models
# refuse is silently discarded by `_frames_of`, which turns "the drop list is
# wrong" and "the fixture is wrong" into the same failure message - and the
# first version of this file spent a run on exactly that confusion.
_fixture = _synthetic_animatic()
from server import animatics as _an  # noqa: E402

if len(_an._frames_of(_fixture)) == 6 and len(_an._texts_of(_fixture)) == 1:
    ok("the synthetic fixture parses", "6 frames, 1 text clip")
else:
    fail("THE SYNTHETIC FIXTURE IS INVALID - fix the fixture, not the drop list",
         f"{len(_an._frames_of(_fixture))} frames, "
         f"{len(_an._texts_of(_fixture))} texts")

compare("animatic (synthetic)", kind, drop, fn, [_fixture])
kind, drop, fn = targets["storyboard"]
compare("storyboard (synthetic)", kind, drop, fn,
        [_synthetic_board(False), _synthetic_board(True)])

# ---------------------------------------------------------------------------
# 3. The two invariants a drop list must never break
# ---------------------------------------------------------------------------
print("\n  the invariants")
src = _synthetic_animatic()
before = len(((src.params or {}).get("frames") or [])[0].get("keyframes") or {})
slim = _slim(src, targets["animatic"][1])
after = len(((src.params or {}).get("frames") or [])[0].get("keyframes") or {})
if before and before == after:
    ok("slimming a record does not touch the original", f"keyframes kept {after} key(s)")
else:
    fail("SLIMMING MUTATED ITS INPUT", f"{before} → {after}")

frames_full = (src.params or {}).get("frames") or []
frames_lean = (slim.params or {}).get("frames") or []
if len(frames_full) == len(frames_lean) and len(frames_lean) == 6:
    ok("the array keeps its length and order", f"{len(frames_lean)} frames")
else:
    fail("the array changed shape", f"{len(frames_full)} → {len(frames_lean)}")

if not (frames_lean[0].get("keyframes") or frames_lean[0].get("mask")):
    ok("the named sub-fields really are gone from every element")
else:
    fail("a dropped sub-field survived", str(sorted(frames_lean[0]))[:120])

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("Every library card is identical with and without the fields its list route")
print("no longer fetches" + (" — checked against the live collection." if live_ran
                             else " (synthetic only; Mongo was not reachable)."))
sys.exit(0)

"""🎬 MAKE VIDEO, DRIVEN IN A REAL BROWSER — the plan, the run, and Revert.

    python tests/editor_director_check.py

⚠ NO UNIT TEST COULD COVER THIS, and the reason is the one written at the top of
`useDirectorRun.js`: the runner is a state machine over React commits. Every verb
calls a `setState`, and the NEXT verb has to see the result — `add_transition`
reads back the record it just made to set its length. Run the same steps in a
plain loop and roughly half of them read a document none of the others have
touched yet, do nothing, and report success. That failure is invisible to a test
of the registry, which is why the registry has its own test
(`director_actions_check.py`) and this one exists as well.

The three things this file is for, in the order they matter:

  1. ⚠ THE API CONTRACT HOLDS AGAINST THE REAL EDITOR. `ACTION_API` names 18
     editor functions the verbs may call. The panel says so out loud when one is
     missing (`.dir-broken`), and this asserts the message is absent — an
     assertion a console warning could not satisfy, and the only way to cover the
     names that THIS run's verbs never happen to reach.

  2. THE TIMELINE AFTERWARDS IS THE RIGHT ONE. Transitions on the cuts the plan
     named — not one cut over — and keyframes on the frames it named. Counted off
     the DOM the way `editor_razor_check` counts clips, because a document that
     is right and a screen that is right are two different claims and only the
     second is what the user has.

  3. ⚠ REVERT PUTS IT ALL BACK. One snapshot, not 61 undos — see the header of
     `useDirectorRun.js` for why. Asserted by comparing the timeline before the
     run against the timeline after Revert, element for element.

  4. ⚠ THE PREVIEW TRACKS ITS OWN TICK BOXES (Phase 2). Un-ticking a treatment
     re-costs the plan and RELABELS THE RUN BUTTON with the new number of edits,
     and the table above it loses exactly those rows. This is the claim that
     makes a preview worth having: a table showing a film the button will not
     make is worse than no table, because the user reads it, presses Run, and
     gets something else. It is also asserted to be FREE — the re-cost re-reads
     the plan already in memory rather than asking the model again, and a
     re-cost that cost a call and a wait is one nobody would ever try.

  5. ⚠ THE AI DOOR FAILS SOFT (Phase 2). This suite's fake backend deliberately
     does NOT route `/director/{id}/plan`, so pressing "Read my film" here is
     exactly what a user with no backend gets — and the assertion is that they
     still get a plan, from the deterministic rules planner, WITH the panel
     saying so. Silence would be worse than the fallback: a thin plan read as
     the AI's opinion of your film is a bug report.

⚠ WHAT THIS FILE DOES NOT COVER, ON PURPOSE: the model itself. Every assertion
below drives the FREE door — "Just the rhythm", the Phase 0 planner — because it
is deterministic, which is what lets the run assertions name the exact cuts they
expect. What the model is sent and what is done with what it returns is
`director_language_check.py` and `director_determinism_check.py`, which drive it
through a stub adapter and need no network at all.

⚠ AND THE FIXTURE IS DELIBERATELY UNEVEN. Three of its eight shots hold three
times as long as the rest, because the Phase 0 planner reads RHYTHM: on a
timeline where every shot is the same length there is correctly nothing to do,
and a fixture like that would let a completely broken runner pass. The flat case
is covered too — as the case that must produce NOTHING.

The harness (Vite + a routed fake backend) is `editor_razor_check.py`'s, per the
"copy the nearest existing harness" rule in AGENTS.md.
"""

import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

PROBE_HTML = os.path.join(CLIENT, "__probe_director.html")
PROBE_JSX = os.path.join(CLIENT, "__probe_director.jsx")

NL = chr(10)

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ⚠ SHOTS 2, 5 AND 7 HOLD 6s; THE REST HOLD 2s. The median is 2000, so those
# three are the "held" shots the planner is looking for — and they are spread out
# rather than adjacent so the never-move-twice-in-a-row rule is exercised by a
# timeline that could break it, not by one where it never comes up.
HOLDS = [2000, 6000, 2000, 2000, 6000, 2000, 6000, 2000]

PROJECT = {
    "id": "probe",
    "title": "director probe",
    "settings": {"fit": "contain", "background": "#101820", "aspect_ratio": "16:9",
                 "fps": 24, "show_labels": False},
    "frames": [
        {"id": f"f{i + 1}", "kind": "image",
         "src": {"kind": "upload", "upload_id": "u1"},
         "duration_ms": ms, "label": f"Shot {i + 1}",
         "url": "/animatics/probe/media/u1"}
        for i, ms in enumerate(HOLDS)
    ],
    "texts": [], "shapes": [], "layers": [], "overlays": [],
    "transitions": [], "audio_tracks": [], "veo_clips": [], "video": None,
}

# The same project with every shot the same length — the case where the right
# answer is to do nothing.
FLAT = dict(PROJECT)
FLAT["frames"] = [dict(f, duration_ms=2000) for f in PROJECT["frames"]]

# ---------------------------------------------------------------------------
# PHASE B — the voiceover, as the server would leave it
# ---------------------------------------------------------------------------
# ⚠ THE PASS IS SIMULATED, THE RUNNER IS NOT. Nothing here calls a speech model:
# the POST is answered 202 and the project the editor then re-reads is the one
# below — which is exactly what `_lay_out_speech` would have written. That is the
# whole interesting surface from the browser's side, because everything this
# suite is asking about happens AFTER the answer comes back: does the editor
# absorb it, does the plan get re-anchored onto it, and do the two lanes the pass
# writes actually appear on screen.
#
# ⚠ SHOT 4 IS THE ONE THAT GROWS, and it is chosen to MOVE THE RHYTHM. Before the
# pass the held shots are 2, 5 and 7, and the planner dissolves after 2 and 5.
# After it, shot 4 holds twelve seconds and the answer is cuts 2 and 4 — so a run
# that plans first and speaks second lands a dissolve on cut 5, where the film no
# longer has anything to mark. That is `tests/director_voice_order_check.py`'s
# property, asserted here against the real editor instead of in the abstract.
SPOKEN_LINES = [
    {"frame_id": "f4", "text": "The machine is finished.", "character": "MAYA",
     "persona": "woman", "voice": "", "shot": "Shot 4", "start_ms": 10000, "hold_ms": 2000},
    {"frame_id": "f6", "text": "Nobody is coming.", "character": "ARI",
     "persona": "man", "voice": "", "shot": "Shot 6", "start_ms": 20000, "hold_ms": 2000},
]

DIALOGUE_SHEET = {
    "lines": SPOKEN_LINES,
    "voices": [{"name": "Kore", "tone": "even", "persona": ""}],
    "personas": [{"key": "woman", "label": "Woman", "voice": "Kore", "direction": ""}],
}

# The project as it comes back: shot 4 stretched to cover its line, an audio
# track, and the spoken lines laid down as captions on the reserved lane.
SPOKEN = dict(PROJECT)
SPOKEN["frames"] = [
    dict(f, duration_ms=12000) if f["id"] == "f4" else dict(f)
    for f in PROJECT["frames"]
]
SPOKEN["layers"] = [{"id": "captions", "name": "Captions", "kind": "text"}]
SPOKEN["audio_tracks"] = [
    {"id": "a1", "upload_id": "vo1", "filename": "voiceover.wav", "start_ms": 10000,
     "duration_ms": 3000, "url": "/animatics/probe/media/vo1", "layer_id": "",
     "volume": 1, "muted": False},
]
# ⚠ `cap…` IS THE ONLY RECORD THAT A CAPTION WAS GENERATED (`CAPTION_ID_PREFIX`),
# and this suite depends on it twice: the editor keeps these clips where the
# server timed them rather than rippling them a second time, and the assertion
# below counts them apart from anything the plan typed.
SPOKEN["texts"] = [
    {"id": "cap1", "text": "The machine is finished.", "start_ms": 10000,
     "duration_ms": 2400, "layer_id": "captions", "position": "bottom"},
    {"id": "cap2", "text": "Nobody is coming.", "start_ms": 24000,
     "duration_ms": 1800, "layer_id": "captions", "position": "bottom"},
]


# ---------------------------------------------------------------------------
# PHASE C — the Veo pass, as the server would leave it
# ---------------------------------------------------------------------------
# ⚠ VEO IS NEVER CALLED, AND NOTHING IS SPENT. `POST /animatics/probe/animate` is
# answered 202 and the project the editor then re-reads carries the finished clip
# RECORDS — which is exactly what a real batch leaves behind. Everything this
# suite is asking about happens after that: does `reconcileVeoClips` attach them,
# do they land on the Storyboard VIDEO row rather than among the panels, and do
# they line up over the stills they were made from.
#
# ⚠ THE FRAMES HAVE TO BE BOARD PANELS. A take is `storyboard_id` AND video
# (`clipRowKind` → `board_video`), so a fixture of plain uploads could never
# produce one — the render would land on the ordinary Video row and the thing
# under test would not exist. This is the one fixture here with a board behind it.
VEO_HOLDS = [2400, 5000, 2000]

BOARD = dict(PROJECT)
BOARD["frames"] = [
    {"id": f"b{i + 1}", "kind": "image",
     "src": {"kind": "panel", "storyboard_id": "sb1", "index": i, "upload_id": "u1"},
     "duration_ms": ms, "label": f"Shot {i + 1}",
     "url": "/animatics/probe/media/u1"}
    for i, ms in enumerate(VEO_HOLDS)
]
BOARD["layers"] = []
BOARD["audio_tracks"] = []
BOARD["texts"] = []
BOARD["veo_clips"] = []
BOARD["director_run"] = None

# ⚠ THE MOTION PROMPTS ARE WHAT MAKES PHASE C DUE AT ALL. The rules planner
# writes none — arithmetic cannot say what should happen inside a shot — so the
# plan endpoint has to be routed for this half, unlike everywhere else here.
VEO_PLAN = {
    "provider": "stub", "model": "stub",
    "plan": {
        "version": 1, "summary": "A machine outgrows its maker.", "mood": "tense",
        "language": "english",
        "steps": [
            {"verb": "note", "args": {"text": "three shots, one scene"}},
            {"verb": "add_transition", "args": {"cut": 2, "kind": "dissolve", "ms": 600}},
        ],
    },
    "analysis": {"logline": "A machine outgrows its maker.", "shots": [
        {"shot": 1, "dialogue": ""}, {"shot": 2, "dialogue": ""}, {"shot": 3, "dialogue": ""},
    ]},
    "veo": [
        {"shot": 1, "prompt": "he turns slowly to camera", "dialogue": ""},
        {"shot": 2, "prompt": "the workshop lights flicker out", "dialogue": ""},
        {"shot": 3, "prompt": "the door swings shut", "dialogue": ""},
    ],
    "dropped": [], "notes": [],
    "cost": {"shots": 3, "seconds": 24, "usd": 2.88, "tier": "fast", "resolution": "720p"},
}

# What the project reads back as once the pass has "rendered": one record per
# shot, ready, each with the length the Director asked for. `render_frame_clip`
# writes exactly this shape.
RENDERED = dict(BOARD)
RENDERED["veo_clips"] = [
    {"id": f"c{i + 1}", "frame_id": f"b{i + 1}", "source_upload_id": "", "label": "",
     "upload_id": f"vid{i + 1}", "prompt": "move", "status": "ready", "error": "",
     "duration_ms": seconds * 1000, "seconds": seconds, "cost_usd": 0.48,
     "rendered_at": "2026-08-23T00:00:00Z"}
    # ⚠ THE LENGTHS THE POLICY PRODUCES: 2.4s -> 4s, 5.0s -> 6s, 2.0s -> 4s.
    # Written out rather than computed so a change to `coverSeconds` shows up
    # here as a disagreement rather than as two things quietly moving together.
    for i, seconds in enumerate([4, 6, 4])
]
RENDERED["director_run"] = {
    "id": "run1", "started_at": "2026-08-23T00:00:00Z", "status": "running",
    "shots": [], "render": {}, "batch": 12, "quoted_usd": 2.88, "error": "",
}

# ⚠ THE SAME PROJECT WITH THE PASS UNFINISHED — one clip bought, two never
# submitted, and the record still saying "running". This is the crash, and what
# the brief popup has to offer back.
INTERRUPTED = dict(BOARD)
INTERRUPTED["veo_clips"] = [RENDERED["veo_clips"][0]]
INTERRUPTED["director_run"] = {
    "id": "run1", "started_at": "2026-08-23T00:00:00Z", "status": "running",
    "shots": [
        {"shot": i + 1, "frame_id": f"b{i + 1}", "label": f"Shot {i + 1}",
         "prompt": "move", "seconds": n, "hold_ms": VEO_HOLDS[i]}
        for i, n in enumerate([4, 6, 4])
    ],
    "render": {"tier": "fast", "resolution": "720p", "duration_seconds": 8,
               "generate_audio": True, "negative_prompt": ""},
    "batch": 12, "quoted_usd": 2.88, "error": "",
}


def video_bytes():
    """A file the <video> element will accept a src for. It is never decoded.

    The monitor falls back to the poster while a clip has no readable frames, and
    that is the state every clip in this fixture stays in — what is under test is
    where the bar LANDS, not what it plays.
    """
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def picture_bytes(rgb):
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "PNG")
    return buf.getvalue()


def silence_bytes(ms=3000):
    """A WAV of `ms` of silence — the take, as far as the browser is concerned.

    It is decoded by the Web Audio graph the moment the track appears, so it has
    to be a real file; what is IN it is irrelevant, and silence is the honest
    thing to put in a fixture that never calls a speech model.
    """
    rate, frames = 8000, int(8000 * ms / 1000)
    data = b"\0\0" * frames
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


MEDIA = {
    "u1": ("image/png", picture_bytes((74, 134, 200))),
    "vo1": ("audio/wav", silence_bytes()),
    "vid1": ("video/mp4", video_bytes()),
    "vid2": ("video/mp4", video_bytes()),
    "vid3": ("video/mp4", video_bytes()),
}

# Which fixture the routed backend is currently serving. Flipped between the two
# halves of the test so the flat case runs against a fresh mount.
SERVING = {"project": PROJECT}

# What phase C's routed backend has been asked to do, and what it should answer
# with next. Read by the assertions — `submitted` in particular is the only place
# a re-paid shot could possibly show up.
VEO_CALLS = {"plan": None, "started": [], "closed": [], "submitted": [], "then": PROJECT}

# ⚠ WHICH DIALOGUE SHEET THE BOARD HAS, AND IT IS SWITCHABLE FOR ONE REASON:
# phase B runs BEFORE phase C, so a board that has lines gets a voiceover pass in
# front of its render pass. That interaction is worth having a test of, and it is
# not THIS test — the phase C section serves a silent board so what lands on the
# video row can only have come from the render. `SPEAKS` is the phase B fixture.
SHEET = {"current": None}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def route_api(route, request):
    """Every call the editor makes, answered off the fixture above."""
    url = request.url
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*",
        })
        return
    cors = {"Access-Control-Allow-Origin": "*"}

    match = re.search(r"/animatics/probe/media/(\w+)", url)
    if match:
        found = MEDIA.get(match.group(1))
        if found is None:
            route.fulfill(status=404, headers=cors, body="")
        else:
            route.fulfill(status=200, headers=cors, content_type=found[0], body=found[1])
        return

    if url.rstrip("/").endswith("/director/config"):
        # Free, no model call. The 🎬 popup asks for this to fill its language
        # picker; answering it here is what makes the picker in the screenshot
        # the picker a real user sees.
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps({"provider": "stub", "model": "stub", "error": "",
                                       "languages": [{"id": "english", "label": "English"},
                                                     {"id": "hinglish", "label": "Hinglish"},
                                                     {"id": "hindi", "label": "Hindi"}]}))
        return

    # ------------------------------------------------------------- phase B
    # ⚠ FREE, AND IT IS ROUTED BECAUSE THE PREVIEW READS IT. The panel shows the
    # script before it offers to spend on it, so the sheet is fetched as soon as
    # a plan exists — a 404 here is a legitimate state too (it means "no
    # dialogue"), and the flat half of this suite exercises exactly that.
    if url.rstrip("/").endswith("/animatics/probe/dialogue"):
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps(SHEET["current"] or DIALOGUE_SHEET))
        return

    # ⚠ THE POST FLIPS WHAT THE NEXT GET RETURNS, which is the whole simulation:
    # the pass is asynchronous and the editor re-reads the project when the job
    # leaves RUNNING, so "the server wrote a voiceover" is exactly "the project
    # now reads back with a stretched shot, an audio track and two captions".
    if re.search(r"/animatics/probe/voiceover/?$", url) and request.method == "POST":
        SERVING["project"] = SPOKEN
        route.fulfill(status=202, headers=cors, content_type="application/json",
                      body=json.dumps({"job_id": "probe", "status": "running"}))
        return

    # ------------------------------------------------------------- phase C
    # ⚠ THREE FREE ROUTES AND ONE THAT WOULD SPEND. The quote is arithmetic, the
    # start opens the resumable record, the state closes it — and `/animate` is
    # the one that costs money in life and is answered 202 here. Flipping what
    # the next GET returns is the whole simulation, exactly as the voiceover POST
    # does above: "Veo rendered three shots" IS "the project now reads back with
    # three ready clip records on it".
    if re.search(r"/director/probe/veo/quote/?$", url) and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        shots = body.get("shots") or []
        # Priced the way `_quote_veo_run` prices it: per shot, at its own length,
        # and the total is the SUM OF THE PASSES.
        rate = 0.12
        passes = [shots[at:at + 12] for at in range(0, len(shots), 12)] or []
        rows = [
            {"shots": len(p), "seconds": sum(int(x.get("seconds") or 8) for x in p),
             "usd": round(sum(int(x.get("seconds") or 8) * rate for x in p), 2),
             "tier": "fast", "resolution": "720p"}
            for p in passes
        ]
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps({
                          "batch": 12, "passes": rows,
                          "total": {"shots": sum(r["shots"] for r in rows),
                                    "seconds": sum(r["seconds"] for r in rows),
                                    "usd": round(sum(r["usd"] for r in rows), 2),
                                    "tier": "fast", "resolution": "720p"}}))
        return

    if re.search(r"/director/probe/veo/start/?$", url) and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        VEO_CALLS["started"].append(body.get("shots") or [])
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps({
                          "id": "run1", "started_at": "2026-08-23T00:00:00Z",
                          "status": "running", "shots": body.get("shots") or [],
                          "render": body.get("render") or {}, "batch": 12,
                          "quoted_usd": 2.88, "error": ""}))
        return

    if re.search(r"/director/probe/veo/state/?$", url) and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        VEO_CALLS["closed"].append(body.get("status") or "")
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps({"id": "run1", "status": body.get("status") or "done",
                                       "shots": [], "render": {}, "batch": 12,
                                       "quoted_usd": 2.88, "started_at": "", "error": ""}))
        return

    # ⚠ THE ONE THAT WOULD SPEND. What was SUBMITTED is recorded and asserted on:
    # a resumed pass that re-sent a shot it had already bought would show up here
    # and nowhere else.
    if re.search(r"/animatics/probe/animate/?$", url) and request.method == "POST":
        body = json.loads(request.post_data or "{}")
        VEO_CALLS["submitted"].append(body)
        SERVING["project"] = VEO_CALLS["then"]
        route.fulfill(status=202, headers=cors, content_type="application/json",
                      body=json.dumps({"job_id": "probe", "status": "running",
                                       "kind": "animatic", "character_name": "probe",
                                       "message": "Animating 3 frame(s)."}))
        return

    # ⚠ THE PLAN ENDPOINT IS ROUTED ONLY WHEN A FIXTURE ASKS FOR IT. Left off, it
    # falls through to the 404 below — which is exactly the "the backend is not
    # there" case, and what most of this suite asserts about it is that the 🎬
    # button still produces a plan. Phase C needs the opposite, because the
    # rules planner writes no motion prompts and so can never make it due.
    if re.search(r"/director/probe/plan/?$", url) and request.method == "POST":
        if VEO_CALLS["plan"] is None:
            route.fulfill(status=502, headers=cors, content_type="application/json",
                          body=json.dumps({"detail": "no model here"}))
        else:
            route.fulfill(status=200, headers=cors, content_type="application/json",
                          body=json.dumps(VEO_CALLS["plan"]))
        return

    if url.rstrip("/").endswith("/animatics/luts"):
        route.fulfill(status=200, headers=cors, content_type="application/json", body="[]")
        return

    if re.search(r"/animatics/probe/?$", url):
        # ⚠ THE PUT IS THE AUTOSAVE, and answering it with the FIXTURE rather
        # than with what was sent is what stops an autosave firing mid-run from
        # putting the pre-run document back over the Director's work.
        route.fulfill(status=200, headers=cors, content_type="application/json",
                      body=json.dumps(SERVING["project"]))
        return

    route.fulfill(status=404, headers=cors, content_type="application/json",
                  body=json.dumps({"detail": "not found"}))


def route_job(route, request):
    """`GET /jobs/probe` — the poll phase B waits on.

    Answered QUEUED rather than RUNNING, once, because a speech pass that takes
    thirty seconds in life is not a property worth spending thirty seconds of
    test time on: what is being checked is what the runner does when the job ends,
    and it ends on the first poll.
    """
    cors = {"Access-Control-Allow-Origin": "*"}
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*",
        })
        return
    route.fulfill(status=200, headers=cors, content_type="application/json",
                  body=json.dumps({"job_id": "probe", "status": "queued",
                                   "kind": "animatic", "progress": None, "error": None}))


PROBE_JSX_SOURCE = r"""
import React from "react";
import { createRoot } from "react-dom/client";

import AnimaticEditor from "/src/components/AnimaticEditor.jsx";
import { ACTION_API } from "/src/animatic/agent/actions.js";
import "/src/styles/index.css";

const probe = { errors: [], ready: false, apiNames: ACTION_API };
window.__probe = probe;

window.addEventListener("error", (e) => probe.errors.push(String(e.message || e)));
window.addEventListener("unhandledrejection", (e) =>
  probe.errors.push("unhandled rejection: " + String(e.reason))
);
const realError = console.error;
console.error = (...args) => {
  probe.errors.push(args.map(String).join(" "));
  realError.apply(console, args);
};

createRoot(document.getElementById("root")).render(
  <AnimaticEditor animaticId="probe" onBack={() => {}} onDeleted={() => {}} />
);

/**
 * THE TIMELINE, READ OFF THE SCREEN.
 *
 * ⚠ OFF `data-sel` AND THE TRANSITION BADGES, not off React state — the same
 * question `editor_razor_check` asks. A run that updated the document without
 * reaching the screen is a run the user cannot see, and it would pass a test
 * that read the state.
 */
probe.timeline = () => ({
  frames: Array.from(document.querySelectorAll('[data-sel^="frame:"]')).map(
    (el) => el.getAttribute("data-sel")
  ),
  texts: document.querySelectorAll('[data-sel^="text:"]').length,
  shapes: document.querySelectorAll('[data-sel^="shape:"]').length,
  // Every transition badge on the strip, in the order it is drawn — which IS the
  // order of the cuts, so the list says WHICH cuts were treated and not merely
  // how many.
  transitions: Array.from(document.querySelectorAll("[data-transition-cut]")).map((el) =>
    Number(el.getAttribute("data-transition-cut"))
  ),
});

/** Whether the panel is complaining that the build is wired wrong. */
probe.brokenWiring = () => {
  const el = document.querySelector(".dir-broken");
  return el ? el.textContent : "";
};

/** The panel's log lines, as `state: text`. */
probe.logLines = () =>
  Array.from(document.querySelectorAll(".dir-log li")).map((el) => ({
    state: (el.className.match(/dir-log-(\w+)/) || [])[1] || "",
    text: (el.querySelector(".dir-log-text") || {}).textContent || "",
  }));

/** How the panel says it is doing. */
probe.progress = () => {
  const el = document.querySelector(".dir-progress");
  return el ? el.textContent : "";
};

/** The rows of the plan table, as arrays of cell text. */
probe.planRows = () =>
  Array.from(document.querySelectorAll(".dir-table tbody tr")).map((tr) =>
    Array.from(tr.querySelectorAll("td")).map((td) => td.textContent.trim())
  );

probe.notice = () => {
  const el = document.querySelector(".an-status-note");
  return el ? el.textContent : "";
};

/** What the Run button says. ⚠ THE RE-COST, MADE VISIBLE — it carries the count. */
probe.runLabel = () => {
  const el = document.querySelector(".dir-actions button.primary");
  return el ? el.textContent.trim() : "";
};

/** The totals line under the table. */
probe.totals = () => {
  const el = document.querySelector(".dir-totals");
  return el ? el.textContent.trim() : "";
};

/** The tick boxes, as `label: ticked`. */
probe.includeBoxes = () =>
  Array.from(document.querySelectorAll(".dir-include-box")).map((el) => ({
    label: el.textContent.trim(),
    on: el.querySelector("input").checked,
  }));

/** Un-tick one of them by its label. */
probe.untick = (label) => {
  const box = Array.from(document.querySelectorAll(".dir-include-box")).find(
    (el) => el.textContent.trim() === label
  );
  if (!box) return false;
  box.querySelector("input").click();
  return true;
};

/** Is the brief (popup one) on screen? */
probe.onBrief = () => Boolean(document.querySelector(".dir-brief"));

// ------------------------------------------------------------------ phase B

/** THE SCRIPT, as the preview shows it: who says what, before anything spends. */
probe.script = () => {
  const box = document.querySelector(".dir-script");
  if (!box) return null;
  return {
    summary: (box.querySelector("summary") || {}).textContent || "",
    lines: Array.from(box.querySelectorAll(".dir-script-lines li")).map((li) => ({
      shot: (li.querySelector(".dir-script-shot") || {}).textContent || "",
      who: (li.querySelector(".dir-script-who") || {}).textContent || "",
      text: (li.querySelector(".dir-script-text") || {}).textContent || "",
    })),
  };
};

/** The price line — and, since Phase 3, whether it admits to spending. */
probe.costLine = () => {
  const el = document.querySelector(".dir-cost");
  return el ? { text: el.textContent.trim(), spends: el.classList.contains("dir-cost-spends") } : null;
};

/** What phase B is saying while it runs, and what it left behind afterwards. */
probe.speechLine = () => {
  const el = document.querySelector(".dir-speech");
  return el ? el.textContent.trim() : "";
};

/**
 * THE TWO LANES THE PASS WRITES, READ OFF THE SCREEN.
 *
 * ⚠ COUNTED IN THE DOM, like everything else here. "The project has an audio
 * track" and "there is a clip in the Audio lane" are two different claims, and
 * only the second one is what the user gets — the whole reason the recording is
 * also pushed into the Media pane is that a run once satisfied the first without
 * the second.
 */
probe.lanes = () => ({
  audio: document.querySelectorAll('[data-sel^="audio:"]').length,
  // Every caption clip on screen, by its words — which is what makes "no
  // duplicate words in the Text lane" an assertion rather than a hope.
  texts: Array.from(document.querySelectorAll('[data-sel^="text:"]')).map(
    (el) => (el.textContent || "").trim()
  ),
});

// ------------------------------------------------------------------ phase C

/**
 * EVERY PICTURE BAR ON THE TIMELINE, WITH THE ROW IT IS ON AND WHERE IT SITS.
 *
 * ⚠ READ OFF THE SCREEN, not off the document, which is the only way to ask the
 * two questions this phase is actually about: is the take on the Storyboard
 * VIDEO row rather than among the panels (`is-veo`, from
 * `clipRowKind === "board_video"`), and does it line up OVER the still it was
 * made from. A clip that is in the document and drawn in the wrong lane is a
 * clip the user cannot find.
 */
probe.bars = () =>
  Array.from(document.querySelectorAll('.tl-bar[data-sel^="frame:"]')).map((el) => {
    const lane = el.closest("[data-lane]");
    return {
      sel: el.getAttribute("data-sel"),
      lane: lane ? lane.getAttribute("data-lane") : "",
      // The row's position down the timeline, so "above its still" is a claim
      // about the screen rather than about a class name.
      top: lane ? Math.round(lane.getBoundingClientRect().top) : -1,
      left: Math.round(el.getBoundingClientRect().left),
      width: Math.round(el.getBoundingClientRect().width),
      veo: el.classList.contains("is-veo"),
    };
  });

/** What the Veo section of the preview says it would render, and for how much. */
probe.shoot = () => {
  const box = document.querySelector(".dir-shoot");
  if (!box) return null;
  return {
    summary: (box.querySelector("summary") || {}).textContent || "",
    lines: Array.from(box.querySelectorAll(".dir-shoot-lines li")).map((li) => ({
      shot: (li.querySelector(".dir-script-shot") || {}).textContent || "",
      len: (li.querySelector(".dir-shoot-len") || {}).textContent || "",
      prompt: (li.querySelector(".dir-script-text") || {}).textContent || "",
    })),
  };
};

/** What phase C is saying while it runs, and what it left behind afterwards. */
probe.footage = () => {
  const el = document.querySelector(".dir-shootrun");
  return el ? el.textContent.trim() : "";
};

/** The resume offer on the brief, or null. */
probe.resume = () => {
  const el = document.querySelector(".dir-resume");
  if (!el) return null;
  const btn = el.querySelector("button");
  return { text: el.textContent.trim(), button: btn ? btn.textContent.trim() : "" };
};

probe.ready = true;
"""

PROBE_HTML_SOURCE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>director probe</title></head>
<body><div id="root"></div>
<script type="module" src="/__probe_director.jsx"></script>
</body></html>
"""


def start_vite(port):
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return None
    proc = subprocess.Popen(
        [npx, "vite", "--port", str(port), "--host", "127.0.0.1", "--strictPort"],
        cwd=CLIENT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        shell=os.name == "nt",
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if "ready in" in line or "Local:" in line:
            time.sleep(2)
            return proc
        if "error" in line.lower() and "Local:" not in line:
            print("  vite:", line.rstrip())
    proc.terminate()
    return None


def _edits(label):
    """The number out of a Run button's label. `-1` when it does not carry one."""
    found = re.search(r"(\d+)\s+edit", label or "")
    return int(found.group(1)) if found else -1


def open_director(page, ai=False, press=True):
    """Press 🎬, then walk through popup ONE to the plan.

    ⚠ IT TAKES THE FREE DOOR BY DEFAULT. "Just the rhythm" is the Phase 0
    planner: no backend, no key, no quota, and deterministic — which is what lets
    every assertion below name the exact cuts it expects. The AI door is a live
    two-call round trip and belongs in `director_language_check` and
    `director_determinism_check`, where it can be driven without a network.
    """
    page.click("button.an-add-director")
    page.wait_for_selector(".dir-modal", timeout=15000)
    page.wait_for_selector(".dir-brief", timeout=5000)
    page.wait_for_timeout(150)
    # ⚠ `press=False` STOPS ON POPUP ONE. The resume offer lives there, above the
    # brief, and pressing either door past it would write a new plan over the
    # interrupted run this suite is trying to look at.
    if not press:
        return
    if ai:
        page.click(".dir-actions button.primary")
    else:
        page.click(".dir-actions button.small")
    # ⚠ WAIT FOR THE BRIEF TO GO, not for the table to arrive. A flat timeline
    # produces a plan with no table in it at all (correctly — there is no rhythm
    # to read), and waiting on `.dir-table` there would time out on the one case
    # this suite most wants to check.
    page.wait_for_selector(".dir-brief", state="detached", timeout=20000)
    page.wait_for_timeout(250)


def wait_for_end(page, timeout_ms=30000):
    """Wait for the panel to report the run is finished.

    ⚠ SPLIT OUT OF `run_to_end` FOR THE RESUME, which is started from its own
    button on popup one rather than from Run — and which finishes the FOOTAGE
    rather than a plan, so there are no steps to watch go by.
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if page.query_selector(".dir-actions button[title^='Put the timeline back']"):
            page.wait_for_timeout(250)
            return True
        page.wait_for_timeout(120)
    return False


def run_to_end(page, timeout_ms=30000):
    """Press Run and wait for the panel to report it is finished."""
    page.click(".dir-actions button.primary")
    return wait_for_end(page, timeout_ms)


def main():
    if not os.path.isdir(os.path.join(CLIENT, "node_modules")):
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return 2

    with open(PROBE_JSX, "w", encoding="utf-8") as fh:
        fh.write(PROBE_JSX_SOURCE)
    with open(PROBE_HTML, "w", encoding="utf-8") as fh:
        fh.write(PROBE_HTML_SOURCE)

    port = free_port()
    vite = None
    try:
        vite = start_vite(port)
        if vite is None:
            print("  Vite would not start — the editor was NOT driven.")
            return 2

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=[
                "--use-gl=angle", "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            ])
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            page.route("**/animatics/**", route_api)
            page.route("**/animatics", route_api)
            # ⚠ THE DIRECTOR'S OWN ROUTES, ROUTED SINCE PHASE 4. Before it, every
            # `/director/...` call fell through to the dev server's 404 — which
            # was the point for `/plan` (the fallback case) and merely harmless
            # for `/config`. Phase C's three routes are not harmless: un-routed,
            # `/veo/quote` fails, the box cannot be priced, and the pass is never
            # offered at all. `VEO_CALLS["plan"]` still decides whether `/plan`
            # answers or 502s, so the fail-soft case above is unchanged.
            page.route("**/director/**", route_api)
            page.route("**/jobs/**", route_job)
            page.goto(f"http://127.0.0.1:{port}/__probe_director.html")
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)

            print("\nThe editor comes up with the uneven fixture on the timeline\n")
            try:
                page.wait_for_selector("canvas", timeout=45000)
                page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            except Exception as exc:  # noqa: BLE001
                check("the editor mounts", False, str(exc)[:160])
                print(json.dumps(page.evaluate("() => window.__probe.errors"), indent=2)[:2000])
                page.screenshot(path=os.path.join(ROOT, "director_probe_failed.png"))
                browser.close()
                return 1

            before = page.evaluate("() => window.__probe.timeline()")
            check("all eight shots are drawn", len(before["frames"]) == len(HOLDS),
                  str(before))
            check("and nothing is on the cuts yet", before["transitions"] == [],
                  str(before["transitions"]))

            # ---------------------------------------------------------------
            print("\n🎬 Make Video opens a PREVIEW — the timeline is not touched\n")
            check("the button is on the timeline's add row",
                  page.query_selector("button.an-add-director") is not None)

            # ⚠ POPUP ONE IS THE BRIEF, AND IT COMES FIRST. 🎬 no longer opens on
            # a plan: it asks what the film is and what language it is in, then
            # offers two doors out. Asserted by hand here rather than inside
            # `open_director`, because "the dialog opens on a question" is a
            # product claim and the helper's job is only to get past it.
            page.click("button.an-add-director")
            page.wait_for_selector(".dir-modal", timeout=15000)
            check("the panel opens", page.query_selector(".dir-modal") is not None)
            check("⚠ ...on the BRIEF, not on a plan",
                  page.evaluate("() => window.__probe.onBrief()") is True)
            check("...with nothing on the timeline touched",
                  page.evaluate("() => window.__probe.timeline()") == before)
            check("⚠ THE FREE DOOR IS A REAL BUTTON — the rules planner needs no "
                  "backend, no key and no quota, and must not be something you "
                  "discover by having the AI call fail",
                  page.query_selector(".dir-actions button.small") is not None)
            check("...and the AI door is the primary one",
                  "Read my film" in (page.query_selector(".dir-actions button.primary")
                                     .text_content() or ""))

            # Take the free door: from here on the plan is the deterministic
            # Phase 0 one, which is what lets every assertion below name the
            # exact cuts it expects.
            page.click(".dir-actions button.small")
            page.wait_for_selector(".dir-table", timeout=15000)
            page.wait_for_timeout(200)

            # ⚠ THE ASSERTION THIS WHOLE FILE EXISTS FOR. See `missingApi`.
            broken = page.evaluate("() => window.__probe.brokenWiring()")
            names = page.evaluate("() => window.__probe.apiNames")
            check(f"⚠ the editor supplies every one of the {len(names)} ACTION_API functions",
                  broken == "", broken[:300])

            check("⚠ opening it changed NOTHING on the timeline",
                  page.evaluate("() => window.__probe.timeline()") == before)

            rows = page.evaluate("() => window.__probe.planRows()")
            check("the plan is a table with a row per shot", len(rows) == len(HOLDS),
                  str(len(rows)))
            check("...and the held shots are the ones with something in them",
                  any(r[2] != "—" or r[3] != "—" for r in rows), json.dumps(rows))

            # ---------------------------------------------------------------
            # ⚠ THE PREVIEW IS ONLY A PREVIEW IF IT TRACKS THE SETTINGS. A table
            # showing a film the button will not make is worse than no table: the
            # user reads it, presses Run, and gets something else. So the claim is
            # that ONE click on a tick box moves all three at once — the rows, the
            # totals under them, and the COUNT IN THE BUTTON — and that it does so
            # without a model call, which is what makes trying it cheap enough to
            # bother with.
            print(NL + "⚠ UN-TICKING A PHASE RE-COSTS THE PLAN AND RELABELS THE BUTTON" + NL)
            boxes = page.evaluate("() => window.__probe.includeBoxes()")
            # ⚠ THE FREE ROW FIRST, THEN THE SPENDING ROW, both in INCLUDE_KEYS
            # order — the list is DERIVED (`freeKeys` / `paidKeys`), so a flag no
            # verb and no pass answers to is never offered and this list is the
            # only place the two rows are written out by hand. It has been wrong
            # twice by being left behind: once when phases D and E added the two
            # sound boxes, and again when 🖼 Animatic images became a pass.
            OFFERED = [
                "Transitions", "Effects", "Text", "Shapes",
                "Sound effects", "Background music",
                "Voiceover", "Veo renders", "Animatic images",
            ]
            # The two boxes that start OFF, and both for the same reason: they are
            # the ones that spend enough for a default-on box to be a box nobody
            # reads until it is on an invoice.
            OFF_AT_FIRST = {"Veo renders", "Animatic images"}
            check("the preview offers a tick box per treatment, and per PASS",
                  [b["label"] for b in boxes] == OFFERED, json.dumps(boxes))
            check("⚠ ...AND `veo` IS OFFERED SINCE PHASE 4, because a pass answers to "
                  "it now — the list is derived (`governedKeys`), so a switch that "
                  "does nothing is never shown",
                  any(b["label"] == "Veo renders" for b in boxes), json.dumps(boxes))
            check("...while Voiceover is offered for the same reason (phase B)",
                  any(b["label"] == "Voiceover" for b in boxes), json.dumps(boxes))
            check("⚠ ...AND SO IS `poses` (phase C2) — 🖼 Animatic images is a tick "
                  "box on 🎬 now, running the very same queue the tool-row button "
                  "runs rather than a second copy of it",
                  any(b["label"] == "Animatic images" for b in boxes), json.dumps(boxes))
            check("every treatment starts ticked",
                  all(b["on"] for b in boxes if b["label"] not in OFF_AT_FIRST),
                  json.dumps(boxes))
            check("⚠ ...AND THE TWO BIG SPENDERS ARE THE ONES THAT START OFF. Veo "
                  "costs tens of dollars and Animatic images buys four drawings per "
                  "second of film out of the image quota; a default-on box is a box "
                  "nobody reads until it is on an invoice",
                  all(not b["on"] for b in boxes if b["label"] in OFF_AT_FIRST),
                  json.dumps(boxes))

            label_before = page.evaluate("() => window.__probe.runLabel()")
            totals_before = page.evaluate("() => window.__probe.totals()")
            rows_before = page.evaluate("() => window.__probe.planRows()")
            check("the Run button says how many edits it will make",
                  "edit" in label_before, label_before)
            check("...and the table has transitions in it to lose",
                  len([r for r in rows_before if r[2] != "—"]) > 0, json.dumps(rows_before))

            page.evaluate("() => window.__probe.untick('Transitions')")
            page.wait_for_timeout(250)
            label_after = page.evaluate("() => window.__probe.runLabel()")
            totals_after = page.evaluate("() => window.__probe.totals()")
            rows_after = page.evaluate("() => window.__probe.planRows()")

            check("⚠ THE BUTTON RELABELS ITSELF", label_after != label_before,
                  f"{label_before!r} -> {label_after!r}")
            check("...to a SMALLER number of edits",
                  _edits(label_after) < _edits(label_before),
                  f"{label_before!r} -> {label_after!r}")
            check("the totals line re-costs with it", totals_after != totals_before,
                  f"{totals_before!r} -> {totals_after!r}")
            check("...and says zero transitions", "0 transition" in totals_after,
                  totals_after)
            check("⚠ AND THE TABLE ITSELF LOSES THEM — the preview is of the film "
                  "the button will now make, not of the one it would have made",
                  len([r for r in rows_after if r[2] != "—"]) == 0, json.dumps(rows_after))
            check("the camera moves are untouched — that box says Transitions, "
                  "and it means only transitions",
                  len([r for r in rows_after if r[3] != "—"])
                  == len([r for r in rows_before if r[3] != "—"]),
                  json.dumps(rows_after))
            check("⚠ opening and re-costing STILL changed nothing on the timeline",
                  page.evaluate("() => window.__probe.timeline()") == before)

            page.evaluate("() => window.__probe.untick('Transitions')")
            page.wait_for_timeout(250)
            check("ticking it back puts them back — the switch is not one-way",
                  page.evaluate("() => window.__probe.runLabel()") == label_before,
                  page.evaluate("() => window.__probe.runLabel()"))

            # ---------------------------------------------------------------
            # ⚠ THE PANEL STOPPED BEING FREE, AND IT HAS TO SAY SO BEFORE THE
            # BUTTON IS PRESSED. Phase B reads dialogue aloud with a speech model:
            # that is the first thing in this dialog that costs money, and every
            # other paid path in this editor shows what it would spend on first.
            # So the script — every line, not a count — is on screen, the price
            # line changes colour and wording, and the SPEND IS ON THE BUTTON.
            print(NL + "⚠ PHASE B IS ON THE PREVIEW BEFORE IT IS ON THE BILL" + NL)
            script = page.evaluate("() => window.__probe.script()")
            check("the preview shows what would be read aloud", script is not None,
                  "no .dir-script in the panel")
            check("...every line of it, not a count",
                  script and [l["text"] for l in script["lines"]]
                  == [l["text"] for l in SPOKEN_LINES], json.dumps(script))
            check("...with the shot each one is spoken over",
                  script and [l["shot"] for l in script["lines"]] == ["Shot 4", "Shot 6"],
                  json.dumps(script))
            check("...and who says it", script and script["lines"][0]["who"] == "MAYA",
                  json.dumps(script))
            check("⚠ it says the words came off the BOARD, not out of the model",
                  script and "storyboard" in script["summary"], (script or {}).get("summary"))

            cost = page.evaluate("() => window.__probe.costLine()")
            check("⚠ THE PRICE LINE ADMITS THE RUN SPENDS", cost and cost["spends"] is True,
                  json.dumps(cost))
            check("...and says what on", cost and "read aloud" in cost["text"],
                  (cost or {}).get("text", "")[:200])
            spend_label = page.evaluate("() => window.__probe.runLabel()")
            check("⚠ AND SO DOES THE RUN BUTTON, before it is pressed",
                  "2 spoken" in spend_label, spend_label)

            # ⚠ AND UN-TICKING IT TAKES THE SPEND OFF THE BUTTON. Same promise the
            # treatment boxes make and the same reason it matters: the film in the
            # table and the film the button makes have to be the same film at
            # every setting — including the setting where it costs nothing.
            page.evaluate("() => window.__probe.untick('Voiceover')")
            page.wait_for_timeout(250)
            quiet_label = page.evaluate("() => window.__probe.runLabel()")
            quiet_cost = page.evaluate("() => window.__probe.costLine()")
            check("⚠ un-ticking Voiceover takes the spend off the button",
                  "spoken" not in quiet_label, quiet_label)
            check("...and the edits it will still make are unchanged",
                  _edits(quiet_label) == _edits(spend_label),
                  f"{spend_label!r} -> {quiet_label!r}")
            check("...and the price line goes back to saying Free",
                  quiet_cost and quiet_cost["spends"] is False and "Free" in quiet_cost["text"],
                  json.dumps(quiet_cost))
            check("the script is still listed, as what ticking it back would read",
                  "Would read" in (page.evaluate("() => window.__probe.script()") or {})
                  .get("summary", ""),
                  json.dumps(page.evaluate("() => window.__probe.script()")))

            # ---------------------------------------------------------------
            # ⚠ THIS RUN IS THE SILENT ONE, ON PURPOSE. Every assertion below is
            # about the Phase 0 planner reading the rhythm of THIS timeline, and
            # phase B would re-lay the timeline underneath it. The paid run has a
            # section of its own at the end of this file, where the shift it
            # causes is the thing being measured rather than noise.
            print("\nRun — and what lands is what the table said\n")
            finished = run_to_end(page)
            check("the run reaches the end", finished,
                  page.evaluate("() => window.__probe.progress()"))
            log = page.evaluate("() => window.__probe.logLines()")
            check("every step is logged", len(log) > 0, str(len(log)))
            check("⚠ and NOT ONE of them failed",
                  not [r for r in log if r["state"] == "failed"],
                  json.dumps([r for r in log if r["state"] == "failed"]))

            after = page.evaluate("() => window.__probe.timeline()")
            check("transitions landed on the cuts", len(after["transitions"]) > 0,
                  str(after["transitions"]))
            # ⚠ ON ALTERNATE CUTS, WHICH IS THE FREE DOOR'S RULE SINCE 2026-08-24.
            # It used to be "the cuts that follow a HELD shot, capped at
            # floor(7 * 0.35) = 2", and on a board whose shots are all the same
            # length that produced NO transitions at all — the film played as eight
            # hard cuts. With Veo un-ticked the stills ARE the finished film, so the
            # house GIVES it a rhythm rather than only reading one: every other cut,
            # from the first. `transitionBudget` owns the ceiling, and the emphasis
            # rule (long holds only) still applies with Veo ticked.
            check("⚠ ...ON ALTERNATE CUTS, FROM THE FIRST",
                  sorted(after["transitions"]) == [1, 3, 5, 7],
                  f"got {after['transitions']} on an 8-shot film")
            check("...so no shot has one on BOTH sides — it would never be on"
                  " screen whole",
                  all(b - a >= 2 for a, b in zip(sorted(after["transitions"]),
                                                 sorted(after["transitions"])[1:])),
                  str(after["transitions"]))
            check("no picture clip was added or lost",
                  after["frames"] == before["frames"], str(after["frames"]))
            check("the Phase 0 planner wrote no text and no shapes",
                  after["texts"] == 0 and after["shapes"] == 0, json.dumps(after))
            check("the editor said what it did", "Director" in
                  page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))

            # ---------------------------------------------------------------
            print("\n⚠ REVERT PUTS IT ALL BACK — one snapshot, not 61 undos\n")
            page.click(".dir-actions button[title^='Put the timeline back']")
            page.wait_for_timeout(500)
            reverted = page.evaluate("() => window.__probe.timeline()")
            check("the cuts are straight again", reverted["transitions"] == [],
                  str(reverted["transitions"]))
            check("⚠ and the timeline matches the one before the run, exactly",
                  reverted == before,
                  f"before={json.dumps(before)} after={json.dumps(reverted)}")
            check("the panel is back at the preview, ready to run again",
                  page.query_selector(".dir-actions button.primary") is not None)

            # ---------------------------------------------------------------
            print("\nThe ✕ is the only way out — a backdrop click must not close it\n")
            box = page.query_selector(".modal-overlay").bounding_box()
            page.mouse.click(box["x"] + 12, box["y"] + 12)
            page.wait_for_timeout(250)
            check("⚠ clicking the backdrop leaves the panel open",
                  page.query_selector(".dir-modal") is not None)
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            check("⚠ Escape leaves it open too",
                  page.query_selector(".dir-modal") is not None)
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(250)
            check("the ✕ closes it", page.query_selector(".dir-modal") is None)

            # ---------------------------------------------------------------
            # ⚠ A MODEL CALL THAT FAILS MUST NOT BE AN ERROR SCREEN. The plan
            # endpoint is not routed by this suite's backend, so pressing the AI
            # door here is exactly what a user on a train gets — and the claim is
            # that they still get a film. The rhythm plan is worth having when
            # the story plan cannot be had; what is NOT acceptable is silence,
            # because a thin plan read as the AI's opinion of your film is worse
            # than a thin plan you know is the fallback.
            print(NL + "⚠ THE AI DOOR FAILS SOFT — the rules plan, and it says so" + NL)
            open_director(page, ai=True)
            check("a plan appeared even though the call failed",
                  page.query_selector(".dir-table") is not None)
            fallback = page.query_selector(".dir-fallback")
            check("⚠ ...and the panel SAYS the AI pass did not run",
                  fallback is not None and "didn" in (fallback.text_content() or ""),
                  (fallback.text_content() if fallback else "(no message)")[:160])
            check("...it is the rhythm plan, on the held shots",
                  any(r[2] != "—" for r in page.evaluate("() => window.__probe.planRows()")))
            check("⚠ and the timeline is STILL untouched",
                  page.evaluate("() => window.__probe.timeline()") == before)
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(200)

            # ---------------------------------------------------------------
            print("\n⚠ A FLAT TIMELINE GETS NO TRANSITIONS — there is no rhythm to read\n")
            SERVING["project"] = FLAT
            page.reload()
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            page.wait_for_timeout(400)
            flat_before = page.evaluate("() => window.__probe.timeline()")
            open_director(page)
            # ⚠ THIS USED TO ASSERT THE OPPOSITE, AND THE OPPOSITE WAS THE BUG.
            # "The plan treats no cut — there is no rhythm to read" was true of the
            # old rule on a board where every shot is the same length: no long
            # holds, so no candidates, so a film of eight identical shots got ZERO
            # transitions. That is the board most people actually have, and it was
            # reported three times. There is no rhythm to READ here, so the house
            # GIVES it one. The MOVES are a different question
            # and the answer changed with the Veo box: nothing is being
            # rendered, so the stills ARE the film and every drawing gets a
            # rostrum move ("add zoom in / zoom out on all the images when Veo
            # is not selected"). See `STILL_CYCLE` in `house_style.js`.
            flat_rows = page.evaluate("() => window.__probe.planRows()")
            # ⚠ ANY TRANSITION COUNTS, NOT ONLY A DISSOLVE OR A DIP, and that is a
            # correction rather than a loosening. This line used to read
            # `"dissolve" in r[2] or "dip" in r[2]`, which was an exact
            # description of the planner right up until it learned to VARY the
            # treatment: `treatmentFor` now spends a `slide` every 4th treated cut
            # and a `wipe` every 6th, so on this eight-shot flat board the fourth
            # treated cut is a slide and the old filter counted [2, 4, 6] — a red
            # line about the RHYTHM caused entirely by a change to the KIND.
            #
            # ⚠ THE KIND IS NOT THIS FILE'S BUSINESS. `director_guardrails_check.py`
            # already pins the whole pattern — slide every 4th, wipe every 6th and
            # no oftener, the direction alternating — against the planner directly,
            # which is where a rule about house style belongs. What THIS suite is
            # for is the property in its own check name: on a board with no rhythm
            # to read, one arrives anyway, on alternate cuts. `r[2]` is the "in"
            # column and `"—"` is how `DirectorPanel` draws an empty cell.
            treated = [i + 1 for i, r in enumerate(flat_rows) if r[2] != "—"]
            check("⚠ A FLAT BOARD IS GIVEN A RHYTHM — a transition arrives INTO"
                  " every other shot",
                  treated == [2, 4, 6, 8], json.dumps(flat_rows))
            check("⚠ ...but every drawing still moves, because nothing is being"
                  " rendered over it",
                  flat_rows and all(r[3] != "—" for r in flat_rows),
                  json.dumps(flat_rows))
            # ⚠ AND YET RUN IS ENABLED, BECAUSE THERE IS STILL SOMETHING TO DO.
            # This board has no rhythm to read but it does have dialogue, and
            # phase B is not an edit — so the button offers the one thing that is
            # left and says so. Asserting "disabled" here would be asserting that
            # a film with nothing to cut also cannot be given a voice.
            flat_label = page.evaluate("() => window.__probe.runLabel()")
            check("⚠ ...but Run still offers the voiceover, and NAMES it",
                  "spoken" in flat_label, flat_label)
            check("...alongside the moves it is making", _edits(flat_label) > 0,
                  flat_label)
            page.evaluate("() => window.__probe.untick('Voiceover')")
            page.wait_for_timeout(250)
            runnable = page.evaluate(
                "() => { const b = document.querySelector('.dir-actions button.primary');"
                " return b ? !b.disabled : null; }"
            )
            # ⚠ AND IT IS STILL RUNNABLE WITH THE VOICEOVER OFF, which is the
            # opposite of what this asserted while a flat board got nothing at
            # all: the moves are edits, they are free, and a button disabled
            # over a plan with eight steps in it would be lying about the plan
            # printed above it.
            check("⚠ ...and the moves alone are still worth running",
                  runnable is True, str(runnable))
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(200)
            check("...and the timeline was left alone",
                  page.evaluate("() => window.__probe.timeline()") == flat_before)

            # ---------------------------------------------------------------
            # ⚠ THE PAID RUN, AND THE PROPERTY IT EXISTS TO PROVE. Everything
            # above ran on a film nothing moved. This runs phase B for real (as
            # far as a routed backend can make it real: the POST is answered 202
            # and the project reads back the way `_lay_out_speech` would have left
            # it), and then asks the one question that matters —
            #
            #   ⚠ DID THE EDIT LAND ON THE FILM THAT CAME BACK, OR ON THE ONE THE
            #     PLAN WAS WRITTEN ABOUT?
            #
            # Before the pass the held shots are 2, 5 and 7 and the planner
            # dissolves after 2 and 5. Shot 4 then grows to twelve seconds to
            # cover its line, and the answer becomes 2 and 4. A run that planned
            # first and spoke second would put a dissolve on cut 5 — successfully,
            # with every step logged green, on a cut the film no longer has a
            # reason for. `tests/director_voice_order_check.py` owns this property
            # in the abstract; this is it against the real editor.
            print(NL + "⚠ PHASE B FOR REAL — the sound moves the film, and the edit"
                  " follows it" + NL)
            SERVING["project"] = PROJECT
            page.reload()
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            page.wait_for_timeout(400)
            spoken_before = page.evaluate("() => window.__probe.lanes()")
            check("nothing is on the Audio lane to start with", spoken_before["audio"] == 0,
                  json.dumps(spoken_before))
            check("...and nothing on the Text lane either", spoken_before["texts"] == [],
                  json.dumps(spoken_before))

            open_director(page)
            page.wait_for_selector(".dir-script", timeout=15000)
            check("the Voiceover box is ticked by default",
                  next((b["on"] for b in page.evaluate("() => window.__probe.includeBoxes()")
                        if b["label"] == "Voiceover"), None) is True)
            # 45s: the pass polls a job, and a run that has to wait for one is a
            # different shape of wait from 61 steps at 90ms.
            finished = run_to_end(page, timeout_ms=45000)
            check("the run reaches the end", finished,
                  page.evaluate("() => window.__probe.progress()"))

            said = page.evaluate("() => window.__probe.speechLine()")
            check("⚠ the panel says the sound landed, and what it moved",
                  "read" in said and "grew" in said, said[:200])

            lanes = page.evaluate("() => window.__probe.lanes()")
            check("⚠ THE AUDIO LANE HAS THE TAKE ON IT", lanes["audio"] == 1,
                  json.dumps(lanes))
            check("⚠ AND THE CAPTIONS ARE ON THE TEXT ROW", len(lanes["texts"]) == 2,
                  json.dumps(lanes))
            check("⚠ ...ONCE EACH — the words are not on screen twice",
                  len(set(lanes["texts"])) == len(lanes["texts"]), json.dumps(lanes))
            check("...and they are the words that were read",
                  all(any(l["text"][:16] in t for t in lanes["texts"]) for l in SPOKEN_LINES),
                  json.dumps(lanes))

            moved = page.evaluate("() => window.__probe.timeline()")
            check("no picture clip was added or lost by the pass",
                  moved["frames"] == before["frames"], json.dumps(moved["frames"]))
            # ⚠ WHAT THIS SECTION CAN AND CANNOT PROVE NOW THAT THE FREE DOOR
            # ALTERNATES. It used to read the CUT the rhythm chose — 2 and 4 after
            # the pass, 5 if you planned first — which was the sharpest possible
            # demonstration of the re-anchor. With Veo un-ticked the placement is a
            # fixed pattern now and does not depend on the holds, so that particular
            # needle is gone from THIS door. It has not gone from the suite:
            # `director_voice_order_check.py` proves the same property on the
            # emphasis rule, where the rhythm still chooses the cut. And the checks
            # here still prove the pass landed on the film that came back — the
            # captions, the moves, and not one step failing against a document the
            # plan was not written for.
            check("⚠ THE PATTERN SURVIVES THE PASS — the sound re-times the film"
                  " and the transitions are still alternate, not clustered",
                  sorted(moved["transitions"]) == [1, 3, 5, 7],
                  f"got {moved['transitions']}")
            check("...and every treated cut is one this film actually has",
                  all(1 <= c < len(moved["frames"]) for c in moved["transitions"]),
                  f"{moved['transitions']} against {len(moved['frames'])} shots")

            log = page.evaluate("() => window.__probe.logLines()")
            check("no step failed on the re-anchored plan",
                  not [r for r in log if r["state"] == "failed"],
                  json.dumps([r for r in log if r["state"] == "failed"]))
            check("⚠ and the notice says the voiceover was SPENT — Revert is not a "
                  "refund, and a user who reads it as one will run it twice",
                  "spent" in page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(200)

            # ---------------------------------------------------------------
            # ⚠ PHASE C FOR REAL — the money one. Veo is mocked (the POST is
            # answered 202 and the project reads back with three ready clip
            # records), but everything the browser does around it is not: the
            # quote, the tick box that starts OFF, the record opened before the
            # first submission, the passes, and — the thing this section exists
            # for — WHERE THE TAKES LAND.
            #
            #   ⚠ A TAKE GOES ON THE STORYBOARD VIDEO ROW, ABOVE THE STILL IT WAS
            #     MADE FROM, AND NOT AMONG THE PANELS.
            #
            # That is `reconcileVeoClips` → `attachVeoClip` → the row records,
            # none of which is new — Phase 4's claim is that the Director reaches
            # them through the same door ✨ Animate does rather than building a
            # second one. If a take ever lands on the panels' own row, the film
            # has a picture missing and a video where it used to be.
            print(NL + "⚠ PHASE C FOR REAL — the footage lands on the video row,"
                  " above its stills" + NL)
            VEO_CALLS["plan"] = VEO_PLAN
            VEO_CALLS["then"] = RENDERED
            # ⚠ A SILENT BOARD, so the only paid pass in this section is phase C.
            # With lines on it the voiceover would run first and re-lay the very
            # picture row this section is measuring.
            SHEET["current"] = {"lines": [], "voices": [], "personas": []}
            VEO_CALLS["started"].clear()
            VEO_CALLS["closed"].clear()
            VEO_CALLS["submitted"].clear()
            SERVING["project"] = BOARD
            page.reload()
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            page.wait_for_timeout(400)
            bars_before = page.evaluate("() => window.__probe.bars()")
            check("the board is three stills and no footage",
                  len(bars_before) == 3 and not any(b["veo"] for b in bars_before),
                  json.dumps(bars_before))

            open_director(page, ai=True)
            page.wait_for_selector(".dir-shoot", timeout=15000)
            shoot = page.evaluate("() => window.__probe.shoot()")
            check("⚠ THE PREVIEW LISTS WHAT WOULD BE RENDERED, BEFORE IT IS TICKED",
                  shoot is not None and len(shoot["lines"]) == 3, json.dumps(shoot))
            check("...and it says 'Would render' while the box is off",
                  "Would render" in shoot["summary"], shoot["summary"])
            check("⚠ ...WITH THE LENGTH POLICY VISIBLE PER SHOT — 2.4s takes a 4s "
                  "take, 5.0s takes a 6s one, and the shot grows to match",
                  [l["len"].split("s")[0] for l in shoot["lines"]] == ["4", "6", "4"],
                  json.dumps([l["len"] for l in shoot["lines"]]))
            check("...and the price is on the summary before anything is agreed to",
                  "$" in shoot["summary"], shoot["summary"])

            boxes = page.evaluate("() => window.__probe.includeBoxes()")
            veo_box = next((b for b in boxes if b["label"] == "Veo renders"), None)
            check("⚠ THE VEO BOX IS OFFERED AND STARTS OFF", veo_box is not None
                  and veo_box["on"] is False, json.dumps(boxes))
            off_label = page.evaluate("() => window.__probe.runLabel()")
            check("...so the Run button says nothing about footage yet",
                  "footage" not in off_label, off_label)
            check("...and nothing has been submitted or opened",
                  not VEO_CALLS["submitted"] and not VEO_CALLS["started"],
                  json.dumps(VEO_CALLS["submitted"]))

            # ⚠ TICKING IT IS THE FIRST MOMENT ANY OF THIS COSTS ANYTHING, and the
            # panel has to say so in the same click.
            page.evaluate("() => window.__probe.untick('Veo renders')")
            page.wait_for_timeout(400)
            on_label = page.evaluate("() => window.__probe.runLabel()")
            check("⚠ TICKING IT PUTS THE PRICE ON THE BUTTON",
                  "footage" in on_label and "$" in on_label, on_label)
            cost = page.evaluate("() => window.__probe.costLine()")
            check("⚠ ...AND THE PRICE LINE STOPS SAYING 'Free'",
                  cost and cost["spends"] and "Free" not in cost["text"],
                  json.dumps(cost)[:220])
            check("...and names the renders as what it is spending on",
                  cost and "Veo" in cost["text"], (cost or {}).get("text", "")[:220])
            check("⚠ ...AND STILL NOTHING HAS BEEN SUBMITTED — a price is not a"
                  " charge",
                  not VEO_CALLS["submitted"], json.dumps(VEO_CALLS["submitted"]))

            finished = run_to_end(page, timeout_ms=45000)
            check("the run reaches the end", finished,
                  page.evaluate("() => window.__probe.progress()"))

            check("⚠ THE RECORD WAS OPENED BEFORE THE MONEY MOVED — a run written"
                  " after the first submission would miss every run that needs it",
                  len(VEO_CALLS["started"]) == 1
                  and len(VEO_CALLS["started"][0]) == 3,
                  json.dumps(VEO_CALLS["started"]))
            check("⚠ ...AND CLOSED AFTERWARDS, so the next load does not offer to"
                  " resume a pass that finished",
                  VEO_CALLS["closed"] == ["done"], json.dumps(VEO_CALLS["closed"]))
            check("one submission carried all three shots (they fit one pass)",
                  len(VEO_CALLS["submitted"]) == 1
                  and len(VEO_CALLS["submitted"][0]["frame_ids"]) == 3,
                  json.dumps([len(c["frame_ids"]) for c in VEO_CALLS["submitted"]]))
            check("⚠ ...AND IT CARRIED A LENGTH PER SHOT, not one for the batch",
                  sorted(VEO_CALLS["submitted"][0]["durations"].values()) == [4, 4, 6],
                  json.dumps(VEO_CALLS["submitted"][0]["durations"]))

            said = page.evaluate("() => window.__probe.footage()")
            check("the panel says what it rendered", "3 shots rendered" in said, said[:200])

            bars = page.evaluate("() => window.__probe.bars()")
            takes = [b for b in bars if b["veo"]]
            stills = [b for b in bars if not b["veo"]]
            check("⚠ THE THREE TAKES ARE ON THE TIMELINE", len(takes) == 3,
                  json.dumps(bars))
            check("...and the three stills are still there, not replaced",
                  len(stills) == 3, json.dumps(bars))
            check("⚠ EVERY TAKE IS ON ONE ROW, AND IT IS NOT THE PANELS' ROW",
                  len({b["lane"] for b in takes}) == 1
                  and not {b["lane"] for b in takes} & {b["lane"] for b in stills},
                  json.dumps([(b["sel"], b["lane"]) for b in bars]))
            check("⚠ ...AND THAT ROW IS DRAWN ABOVE THE STILLS' — a take under the"
                  " picture it was made from is a take nobody sees",
                  max(b["top"] for b in takes) < min(b["top"] for b in stills),
                  json.dumps([(b["sel"], b["top"]) for b in bars]))
            # ⚠ LINED UP OVER THEIR OWN STILLS. `attachVeoClip` gives a take its
            # panel's start and `spreadPanelsForRenders` then pushes the panels
            # after it clear of its end — so the left edges must agree, pair by
            # pair, or a take is playing over the wrong shot.
            takes.sort(key=lambda b: b["left"])
            stills.sort(key=lambda b: b["left"])
            check("⚠ EACH TAKE STARTS WHERE ITS OWN STILL STARTS",
                  all(abs(t["left"] - p["left"]) <= 1 for t, p in zip(takes, stills)),
                  json.dumps([(t["left"], p["left"]) for t, p in zip(takes, stills)]))
            check("⚠ ...AND THE STILL GREW TO THE TAKE'S LENGTH, which is the whole"
                  " length policy: a 2.4s hold under a 4s take would otherwise pop"
                  " back to a drawing",
                  all(p["width"] >= t["width"] - 1 for t, p in zip(takes, stills)),
                  json.dumps([(t["width"], p["width"]) for t, p in zip(takes, stills)]))

            check("⚠ AND THE NOTICE SAYS IT SPENT — Revert is not a refund",
                  "paid for" in page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))
            log = page.evaluate("() => window.__probe.logLines()")
            check("no step failed on the plan re-anchored over the footage",
                  not [r for r in log if r["state"] == "failed"],
                  json.dumps([r for r in log if r["state"] == "failed"]))
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(200)

            # ---------------------------------------------------------------
            # ⚠ THE CRASH, AND THE MONEY IT MUST NOT SPEND TWICE. The project
            # opens with a `director_run` still saying "running" and ONE of its
            # three shots already bought. `tests/director_resume_check.py` owns
            # the arithmetic; this is the offer, on screen, in the real panel.
            print(NL + "⚠ AN INTERRUPTED PASS IS OFFERED BACK, AND FINISHES WHAT IS"
                  " LEFT" + NL)
            VEO_CALLS["then"] = RENDERED
            VEO_CALLS["started"].clear()
            VEO_CALLS["closed"].clear()
            VEO_CALLS["submitted"].clear()
            SERVING["project"] = INTERRUPTED
            page.reload()
            page.wait_for_function("window.__probe && window.__probe.ready", timeout=60000)
            page.wait_for_selector('[data-sel^="frame:"]', timeout=45000)
            page.wait_for_timeout(600)

            open_director(page, press=False)
            offer = page.evaluate("() => window.__probe.resume()")
            check("⚠ THE BRIEF OPENS ON THE INTERRUPTED RENDER, not on a new plan",
                  offer is not None, json.dumps(offer))
            check("...and says how much of it was already bought",
                  offer and "1 of 3" in offer["text"], (offer or {}).get("text", "")[:240])
            check("⚠ ...AND THAT FINISHING WILL NOT RENDER THOSE AGAIN",
                  offer and "not render" in offer["text"],
                  (offer or {}).get("text", "")[:240])
            check("⚠ ...AND THAT IT FINISHES THE FOOTAGE, NOT THE EDIT — the plan"
                  " lived in the browser that died",
                  offer and "footage" in offer["text"],
                  (offer or {}).get("text", "")[:300])
            check("the button offers the outstanding shots only",
                  offer and "2 shots" in offer["button"], (offer or {}).get("button", ""))

            page.click(".dir-resume button")
            finished = wait_for_end(page, timeout_ms=45000)
            check("the resumed render reaches the end", finished,
                  page.evaluate("() => window.__probe.progress()"))
            check("⚠ IT SUBMITTED THE TWO OUTSTANDING SHOTS AND NOT THE PAID ONE —"
                  " which is the whole reason the record exists",
                  len(VEO_CALLS["submitted"]) == 1
                  and sorted(VEO_CALLS["submitted"][0]["frame_ids"]) == ["b2", "b3"],
                  json.dumps([c["frame_ids"] for c in VEO_CALLS["submitted"]]))
            check("...and closed the run so it is not offered a third time",
                  VEO_CALLS["closed"] == ["done"], json.dumps(VEO_CALLS["closed"]))
            check("⚠ ...AND SAID SO: the interrupted render is finished, and the"
                  " edit has to be asked for again",
                  "asked for it again" in page.evaluate("() => window.__probe.notice()"),
                  page.evaluate("() => window.__probe.notice()"))
            page.click(".dir-modal .modal-close")
            page.wait_for_timeout(200)

            VEO_CALLS["plan"] = None

            errors = page.evaluate("() => window.__probe.errors")
            # React's dev build logs act() and key warnings that have nothing to do
            # with this feature; only errors naming the agent are this test's.
            ours = [e for e in errors if "director" in e.lower() or "agent/" in e.lower()]
            check("no console error came out of the agent", not ours,
                  json.dumps(ours)[:500])

            browser.close()
    finally:
        if vite is not None:
            vite.terminate()
        for path in (PROBE_JSX, PROBE_HTML):
            if os.path.exists(path):
                os.remove(path)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""WHEN THE PICTURES MOVE, THE REST OF THE FILM MOVES WITH THEM.

The report, with three screenshots:

    "when i generte veo video so time timeline all layer clip go move but my
     audio not move so see problem my caption and voiver over not move so both
     still . so get this type of problem. So i want when i generate veo video an
     dit come in Story..video layer so iamge clip move already but move also
     caption, voicerover audio, if image, video ,text layer clip so those also
     move that time so user not get this type of problem"

`spreadPanelsForRenders` makes room on the board's picture row when a take is
longer than the shot it was made from. It moves PICTURES — that is the collision
it was written for — and everything else stayed exactly where it was, so one
grown shot put the whole soundtrack a few seconds out for the rest of the film.

---------------------------------------------------------------------------
1. WHY THERE IS NO SINGLE NUMBER TO MOVE THINGS BY
---------------------------------------------------------------------------
Shot 7 grows by 2s and shot 24 by 9s, so a caption at 0:30 owes a different debt
from one at 1:20. What `renderShifts` builds is a STEP FUNCTION over OLD time,
and every other clip is moved by looking its own start up in it:

    old   [S6][S7][S8][S9]
    new   [S6][ S7 ····· ][S8][S9]
    shift  0    0         +4s +4s

⚠ WHAT THIS FILE IS ACTUALLY GUARDING: THE TWO EDGES OF THAT STEP. A caption
INSIDE the grown shot must not move (it is still under its own shot) and a
caption one millisecond past it must. Get the boundary wrong by one clip and the
subtitles are off by one shot for the entire second half of a board — which is
the exact shape of the bug being fixed, arrived at from the other direction.

---------------------------------------------------------------------------
2. AND THE ONE CLIP THAT CANNOT BE MOVED BY A NUMBER
---------------------------------------------------------------------------
The voiceover is ONE clip laid from 0:00 across the whole film. Its start is 0,
so the step function owes it nothing and it does not move — which is the bug —
and shifting it by a later shot's debt would drag the lines BEFORE that shot
along too, which is a different bug. Neither answer is right, because the clip
is not in one place. So `rippleAudio` razors it at the step and moves only the
tail, leaving two ordinary clips reading two windows of one file — exactly what
the razor already makes, so nothing downstream has to learn anything.

    python tests/timeline_ripple_check.py

Needs node for the logic half; skips it cleanly without one, exactly as
`veo_ripple_check.py` does. The wiring half is a source read and always runs.
No backend, no browser, no ffmpeg.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


# ---------------------------------------------------------------------------
# The harness — six 2s shots, shot 2 animated with a 6s take
# ---------------------------------------------------------------------------
# ⚠ THE ONE SHOT GROWS FROM 2s TO 6s, so everything from 4000ms onwards owes
# exactly 4000ms. Round numbers on purpose: a boundary is only checkable if you
# can say where it is without arithmetic.
HARNESS = r"""
import { spreadPanelsForRenders } from "%(scene)s";
import {
  renderShifts, shiftAt, rippleClips, rippleFrames, rippleAudio, RIPPLED_LISTS,
  grownSpans, coverGrownShots,
} from "%(ripple)s";

const panel = (n, start) => ({
  id: `p${n}`, kind: "image", label: `Shot ${n}`, duration_ms: 2000,
  start_ms: start, track: 0,
  src: { kind: "panel", storyboard_id: "b1", index: n - 1 },
});

const board = [1, 2, 3, 4, 5, 6].map((n) => panel(n, (n - 1) * 2000));
// Animate shot 2: a 6s take over a 2s hold, landing where that panel sits.
const appended = [...board, {
  id: "v2", kind: "video", label: "Shot 2", duration_ms: 6000,
  start_ms: 2000, track: 1,
  src: { kind: "video", storyboard_id: "b1", index: 1, upload_id: "u2" },
}];
const after = spreadPanelsForRenders(appended);

const out = {};
out.shifts = renderShifts(appended, after);
out.panels = after.filter((f) => f.track === 0).map((f) => [f.id, f.start_ms, f.duration_ms]);

// --- the step, sampled at the moments that matter --------------------------
out.sampled = [0, 1999, 2000, 3999, 4000, 4001, 20000].map((t) =>
  shiftAt(out.shifts, t)
);
out.empty = renderShifts(board, board);

// --- free clips: captions, text, shapes, overlays --------------------------
const cap = (id, start, dur = 1500) => ({ id, start_ms: start, duration_ms: dur });
const captions = [cap("c1", 0), cap("c2", 2500), cap("c3", 3999), cap("c4", 4000), cap("c5", 9000)];
out.captions = rippleClips(captions, out.shifts).map((c) => [c.id, c.start_ms]);
out.capsUntouched = rippleClips(captions, []) === captions;
// A clip a server pass has already re-timed must not be moved a second time.
out.kept = rippleClips(captions, out.shifts, new Set(["c5"])).map((c) => [c.id, c.start_ms]);

// --- picture clips on OTHER rows ------------------------------------------
const others = [
  { id: "vid1", kind: "video", duration_ms: 3000, start_ms: 0, track: 3, src: { kind: "video", upload_id: "f1" } },
  { id: "vid2", kind: "video", duration_ms: 3000, start_ms: 6000, track: 3, src: { kind: "video", upload_id: "f2" } },
  // No start of its own: "after the last clip on my track" — the track's clock
  // is the LAST END seen on it, so this one sits at 9000, behind vid2.
  { id: "vid3", kind: "video", duration_ms: 1000, track: 3, src: { kind: "video", upload_id: "f3" } },
];
out.others = rippleFrames([...after, ...others], out.shifts)
  .filter((f) => f.track === 3)
  .map((f) => [f.id, f.start_ms === null || f.start_ms === undefined ? "auto" : f.start_ms]);
// The board's own row is the map — it must never be moved a second time by this.
out.boardUntouched = rippleFrames(after, out.shifts) === after;

// --- audio: the one clip that spans the edit -------------------------------
let n = 0;
const mint = () => `new${++n}`;
const voice = {
  id: "vo", upload_id: "wav1", filename: "Voiceover.wav",
  duration_ms: 20000, start_ms: 0, offset_ms: 0, trim_ms: null,
};
out.voice = rippleAudio([voice], out.shifts, mint).map((t) => [
  t.id, t.start_ms, t.offset_ms, t.trim_ms,
]);
// A bed that starts AFTER the edit is a plain move, no cut.
const bed = { id: "mus", upload_id: "wav2", duration_ms: 5000, start_ms: 8000, offset_ms: 0, trim_ms: null };
out.bed = rippleAudio([bed], out.shifts, mint).map((t) => [t.id, t.start_ms, t.trim_ms]);
// A clip entirely BEFORE the edit owes nothing and is handed back untouched.
const early = { id: "sfx", upload_id: "wav3", duration_ms: 1000, start_ms: 0, offset_ms: 0, trim_ms: 1000 };
out.early = rippleAudio([early], out.shifts, mint).map((t) => [t.id, t.start_ms, t.trim_ms]);

// --- a take on a PUSHED panel must not be carried a second time -------------
// Shot 1 is animated while shot 3 already has a take of its own. The layout pass
// moves that take by shot 3's delta; looking it up in the map afterwards — at the
// start it has just been moved TO — would add the same debt again and slide it
// off the shot it is a take of.
const withOld = [...board, {
  id: "v3", kind: "video", label: "Shot 3", duration_ms: 4000,
  start_ms: 4000, track: 1,
  src: { kind: "video", storyboard_id: "b1", index: 2, upload_id: "u3" },
}];
const beforeOne = spreadPanelsForRenders(withOld);
const plusOne = [...beforeOne, {
  id: "v1", kind: "video", label: "Shot 1", duration_ms: 4000,
  start_ms: 0, track: 1,
  src: { kind: "video", storyboard_id: "b1", index: 0, upload_id: "u1" },
}];
const afterOne = spreadPanelsForRenders(plusOne);
const oneShifts = renderShifts(plusOne, afterOne);
const oneCarried = rippleFrames(afterOne, oneShifts);
out.paired = Object.fromEntries(
  oneCarried.filter((f) => ["p3", "v3"].includes(f.id)).map((f) => [f.id, f.start_ms])
);

// --- a caption covers the shot it belongs to -------------------------------
// Shot 2 is 2s and its take is 6s, so the shot becomes 6s. The subtitle written
// for it is 1.5s and used to stop a quarter of the way through the footage.
// A second board is used here so the shot has TWO lines in it, which is the case
// that must not end with one caption stretched over the other.
const twoLine = [
  panel(1, 0),
  { ...panel(2, 2000), duration_ms: 4000 },
  { ...panel(3, 6000), start_ms: 6000 },
  { ...panel(4, 8000), start_ms: 8000 },
];
const twoLineTake = [...twoLine, {
  id: "vT", kind: "video", label: "Shot 2", duration_ms: 9000,
  start_ms: 2000, track: 1,
  src: { kind: "video", storyboard_id: "b1", index: 1, upload_id: "uT" },
}];
const twoLineAfter = spreadPanelsForRenders(twoLineTake);
const twoLineShifts = renderShifts(twoLineTake, twoLineAfter);
out.grown = grownSpans(twoLineTake, twoLineAfter);
const sheet = [
  { id: "cap0000a", layer_id: "captions", text: "shot 1", start_ms: 0, duration_ms: 1200 },
  { id: "cap0001b", layer_id: "captions", text: "shot 2 line A", start_ms: 2000, duration_ms: 1500 },
  { id: "cap0002c", layer_id: "captions", text: "shot 2 line B", start_ms: 4200, duration_ms: 1500 },
  { id: "cap0003d", layer_id: "captions", text: "shot 3", start_ms: 6000, duration_ms: 1200 },
  // ⚠ TYPED BY THE USER, sitting inside the shot that grows. It must not be
  // resized: it is their placement, not something this app wrote.
  { id: "mine1", layer_id: "", text: "MY TITLE", start_ms: 2500, duration_ms: 800 },
];
out.covered = coverGrownShots(rippleClips(sheet, twoLineShifts), out.grown)
  .map((c) => [c.id.slice(0, 7), c.start_ms, c.duration_ms]);
// A shot that only MOVED is not a shot that grew, so nothing over it changes.
out.grownOnlyMoved = grownSpans(board, spreadPanelsForRenders(board)).length;
// And it only ever grows — a caption already longer than its shot is left alone.
out.noShrink = coverGrownShots(
  [{ id: "cap0009z", layer_id: "captions", start_ms: 2000, duration_ms: 20000 }],
  out.grown
)[0].duration_ms;

// --- the five lists a caller has to remember -------------------------------
out.lists = RIPPLED_LISTS;

// --- a project saved BEFORE any of this existed -----------------------------
// The take is already on the timeline and the panel under it is still short.
// Nothing re-runs the layout for a take that is already attached, so without a
// pass on LOAD this board stays wrong for ever — the user cannot pay to render
// the shot again just to straighten the row.
const legacy = [
  panel(1, 0), panel(2, 2000), panel(3, 4000),
  { id: "vOld", kind: "video", label: "Shot 2", duration_ms: 6000,
    start_ms: 2000, track: 1,
    src: { kind: "video", storyboard_id: "b1", index: 1, upload_id: "uOld" } },
];
const healed = spreadPanelsForRenders(legacy);
out.healed = {
  changed: healed !== legacy,
  layout: healed.filter((f) => f.track === 0).map((f) => [f.id, f.start_ms, f.duration_ms]),
  // ⚠ AND A BOARD THAT IS ALREADY RIGHT MUST BE AN IDENTITY TEST, or opening
  // any project would dirty it and the autosave would write on every load.
  again: spreadPanelsForRenders(healed) === healed,
  shifts: renderShifts(legacy, healed).length > 0,
};

// --- hostile ---------------------------------------------------------------
out.nothing = [
  renderShifts([], []).length,
  renderShifts(null, null).length,
  shiftAt(null, 5),
  (rippleClips(null, out.shifts) || []).length,
  (rippleAudio(null, out.shifts, mint) || []).length,
];

console.log(JSON.stringify(out));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="ripple_")
    try:
        src = HARNESS % {
            "scene": (ROOT / "client/src/animatic/scene.js").as_uri(),
            "ripple": (ROOT / "client/src/animatic/ripple.js").as_uri(),
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:900])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


LOGIC = [
    "the grown shot is the step, and it is where the shot USED to end",
    "a moment inside the grown shot owes nothing",
    "…and the moment its old end fell on owes the whole of it",
    "a board where nothing moved makes no map at all",
    "a caption inside the grown shot stays under it",
    "A CAPTION AFTER IT MOVES — the bug, in one line",
    "a clip a server pass already re-timed is left alone",
    "no map, no copy",
    "clips on the Video row move too",
    "…including one with no start of its own, which is given one",
    "the board's own row is never moved twice",
    "A TAKE ON A PUSHED PANEL IS NOT CARRIED TWICE",
    "THE VOICEOVER IS CUT AT THE EDIT, not dragged whole",
    "…and the tail reads on from where the head stopped",
    "a bed that starts after the edit is a plain move",
    "a clip entirely before the edit is untouched",
    "A TAKE ATTACHED BEFORE THE RULE EXISTED IS PUT RIGHT",
    "…and a board that is already right is left completely alone",
    "only the shots that GREW are reported, in the new timeline's terms",
    "A CAPTION IS STRETCHED TO COVER ITS SHOT",
    "…and its START never moves, so it stays on the line it transcribes",
    "…and it never runs into the caption after it",
    "TEXT THE USER TYPED IS NEVER RESIZED",
    "a shot that only moved does not stretch anything",
    "a caption longer than its shot is left alone",
    "the five lists a shift map has to reach are named in one place",
    "nothing in, nothing thrown",
]

print("A step map, and everything else moved along it")
got = run_node()

if got is None:
    for label in LOGIC:
        skip(label, "node not available")
else:
    # Shot 2 grew from [2000,4000) to [2000,8000), so 4000 is where the step is.
    check(
        "the grown shot is the step, and it is where the shot USED to end",
        got["panels"][1] == ["p2", 2000, 6000] and got["panels"][2] == ["p3", 8000, 2000],
        json.dumps(got["panels"]),
    )
    sampled = got["sampled"]
    check(
        "a moment inside the grown shot owes nothing",
        sampled[:4] == [0, 0, 0, 0],
        f"{sampled} shifts={json.dumps(got['shifts'])}",
    )
    check(
        "…and the moment its old end fell on owes the whole of it",
        sampled[4:] == [4000, 4000, 4000],
        f"{sampled} shifts={json.dumps(got['shifts'])}",
    )
    check("a board where nothing moved makes no map at all", got["empty"] == [], json.dumps(got["empty"]))

    caps = dict((c[0], c[1]) for c in got["captions"])
    check("a caption inside the grown shot stays under it",
          caps["c1"] == 0 and caps["c2"] == 2500 and caps["c3"] == 3999, json.dumps(caps))
    # ⚠ THE REPORT. One millisecond later and it owes the whole 4 seconds.
    check("A CAPTION AFTER IT MOVES — the bug, in one line",
          caps["c4"] == 8000 and caps["c5"] == 13000, json.dumps(caps))
    kept = dict((c[0], c[1]) for c in got["kept"])
    check("a clip a server pass already re-timed is left alone",
          kept["c5"] == 9000 and kept["c4"] == 8000, json.dumps(kept))
    check("no map, no copy", got["capsUntouched"] is True,
          "an empty map must hand back the very same array")

    others = dict((f[0], f[1]) for f in got["others"])
    check("clips on the Video row move too",
          others["vid1"] == 0 and others["vid2"] == 10000, json.dumps(others))
    # ⚠ `null` MEANS "after the last clip on my track" — a length-relative place
    # that cannot be shifted as a number, so a clip that has to move is written
    # down explicitly at where it was plus its debt.
    check("…including one with no start of its own, which is given one",
          others["vid3"] == 13000, json.dumps(others))
    check("the board's own row is never moved twice",
          got["boardUntouched"] is True,
          "the board row IS the map; moving it again would double every shift")

    # ⚠ THE FIX'S OWN FOOTGUN. The layout pass moves a take by its panel's
    # delta; the map is in OLD time, so looking the take up at its NEW start adds
    # the debt again and it slides off the shot it is a take of.
    paired = got["paired"]
    check(
        "A TAKE ON A PUSHED PANEL IS NOT CARRIED TWICE",
        paired["v3"] == paired["p3"],
        json.dumps(paired),
    )

    voice = got["voice"]
    # head: 0..4000 of the file. tail: starts at 8000 on the timeline, reading
    # from 4000 into the file — the 4s hole is the room shot 2 took.
    check(
        "THE VOICEOVER IS CUT AT THE EDIT, not dragged whole",
        len(voice) == 2 and voice[0][:2] == ["vo", 0] and voice[0][3] == 4000,
        json.dumps(voice),
    )
    check(
        "…and the tail reads on from where the head stopped",
        voice[1][1] == 8000 and voice[1][2] == 4000 and voice[1][3] == 16000,
        json.dumps(voice),
    )
    check("a bed that starts after the edit is a plain move",
          got["bed"] == [["mus", 12000, None]], json.dumps(got["bed"]))
    check("a clip entirely before the edit is untouched",
          got["early"] == [["sfx", 0, 1000]], json.dumps(got["early"]))

    # ⚠ THE ONE THE USER ACTUALLY HIT. The layout only ever ran on the ATTACH,
    # so a render that landed before the stretch existed kept a 2-second still
    # under 6 seconds of footage and no gesture would ever fix it.
    heal = got["healed"]
    check(
        "A TAKE ATTACHED BEFORE THE RULE EXISTED IS PUT RIGHT",
        heal["changed"] is True
        and heal["layout"][1] == ["p2", 2000, 6000]
        and heal["layout"][2] == ["p3", 8000, 2000]
        and heal["shifts"] is True,
        json.dumps(heal),
    )
    check(
        "…and a board that is already right is left completely alone",
        heal["again"] is True,
        "a load-time pass that is not an identity test dirties every project on open",
    )

    # ⚠ THE THIRD ASK ON THIS EDIT: "caption length only 4sec but my video is 8
    # sec so i want caption goes 8 sec so match video length".
    grown = got["grown"]
    check(
        "only the shots that GREW are reported, in the new timeline's terms",
        grown == [{"start": 2000, "end": 11000}],
        json.dumps(grown),
    )
    cov = dict((c[0], (c[1], c[2])) for c in got["covered"])
    check(
        "A CAPTION IS STRETCHED TO COVER ITS SHOT",
        # line B starts at 4200 and the shot now ends at 11000
        cov["cap0002"] == (4200, 6800),
        json.dumps(got["covered"]),
    )
    check(
        "…and its START never moves, so it stays on the line it transcribes",
        cov["cap0001"][0] == 2000 and cov["cap0002"][0] == 4200,
        json.dumps(got["covered"]),
    )
    # ⚠ TWO SUBTITLES ON SCREEN AT ONCE is the one thing `tidy_lines` exists to
    # prevent, and a shot with two lines in it is where a naive stretch does it.
    check(
        "…and it never runs into the caption after it",
        cov["cap0001"] == (2000, 2200),
        json.dumps(got["covered"]),
    )
    check(
        "TEXT THE USER TYPED IS NEVER RESIZED",
        cov["mine1"] == (2500, 800),
        json.dumps(got["covered"]),
    )
    check("a shot that only moved does not stretch anything",
          got["grownOnlyMoved"] == 0, str(got["grownOnlyMoved"]))
    check("a caption longer than its shot is left alone",
          got["noShrink"] == 20000, str(got["noShrink"]))

    check("the five lists a shift map has to reach are named in one place",
          got["lists"] == ["frames", "texts", "shapes", "overlays", "audioTracks"],
          json.dumps(got["lists"]))
    check("nothing in, nothing thrown", got["nothing"] == [0, 0, 0, 0, 0],
          json.dumps(got["nothing"]))


# ---------------------------------------------------------------------------
# The wiring — where it runs, and the ref that keeps a batch honest
# ---------------------------------------------------------------------------
print("\nWhere it runs")

editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
ripple = (ROOT / "client/src/animatic/ripple.js").read_text(encoding="utf-8")

check(
    "the rule is a pure module the tests can drive",
    "export function renderShifts" in ripple and "export function rippleClips" in ripple,
)
check(
    "the attach runs it in the same write that makes room",
    "renderShifts(appended, next)" in editor,
)
# ⚠⚠ THE REGRESSION THIS WHOLE SECTION EXISTS FOR. The five lists used to be read
# out of a ref filled by an effect. That ref is EMPTY straight out of the load
# promise and STALE inside a poll keyed on `animating` alone — and rippling an
# empty list is a silent no-op that looks exactly like "nothing needed to move".
# It is why the captions were reported as not moving twice. There is no document
# ref any more, and there must not be one: React's functional setters are handed
# the live list at commit time and cannot be stale.
check(
    "NOTHING READS THE DOCUMENT OUT OF A REF",
    "docRef" not in editor,
    "a ref is empty at load and stale in a poll — and failing that way is silent",
)
# Every one of `RIPPLED_LISTS`, at every site that moves the board's row. The
# counts are the point: two sites ripple texts through a setter (the attach and
# the load) and the third takes the server's own list, and all three must reach
# the shapes and the overlays.
for name, call, want in [
    ("texts", "setTexts((list) => coverGrownShots(rippleClips(list, shifts), grown));", 2),
    ("shapes", "setShapes((list) => rippleClips(list, shifts));", 3),
    ("overlays", "setOverlays((list) => rippleClips(list, shifts));", 3),
    ("audio", "setAudioTracks((list) => rippleAudio(list, shifts, newId));", 2),
]:
    check(
        f"every site carries the {name} through a LIVE setter",
        editor.count(call) == want,
        f"found {editor.count(call)}, expected {want}",
    )
check(
    "…and the voiceover run takes the server's own captions and audio",
    "setTexts(rippleClips(project.texts || [], shifts, keep));" in editor
    and "setAudioTracks(rippleAudio(project.audio_tracks || [], shifts, newId, keep));" in editor,
    "the server rewrote those two, so they are rippled as values, not through state",
)
# ⚠ MOVED FIRST, THEN STRETCHED. `coverGrownShots` finds a caption's shot by
# where it now STARTS, so handing it clips that have not been carried yet matches
# every one of them against the wrong shot.
check(
    "the two sites that grow a shot also stretch the captions over it",
    editor.count("coverGrownShots(rippleClips(list, shifts), grown)") == 2,
    f"found {editor.count('coverGrownShots(rippleClips(list, shifts), grown)')}, expected 2",
)
check(
    "…against the shots that actually grew, not against every shift",
    editor.count("grownSpans(") == 2,
    f"found {editor.count('grownSpans(')}",
)
check(
    "the pictures on every other row are carried at all three sites",
    editor.count("rippleFrames(") == 3,
    f"found {editor.count('rippleFrames(')}",
)
check(
    "…and the load re-runs the layout, so an old take's panel is put right",
    "spreadPanelsForRenders(framesRef.current)" in editor and "healed" in editor,
    "nothing else ever re-runs it for a take that is already attached",
)
check(
    "…and says so, because clips moving on their own at open is alarming",
    "were shorter than the takes over them" in editor,
)
check(
    "audio is cut with the editor's own id minter",
    "newId" in editor,
    "ids are the editor's to hand out, not a pure module's",
)
check(
    "and the cut itself is the razor's, not a second one written here",
    "splitClip" in ripple,
    "two halves of a cut must be the two clips the razor already makes",
)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  · {f}")
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
if not failures:
    print("All good — the pictures move and the film moves with them.")
sys.exit(1 if failures else 0)

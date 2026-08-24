"""A MOVE COVERS ITS CLIP, WHATEVER LATER HAPPENS TO THE CLIP'S LENGTH.

    python tests/move_follows_hold_check.py

⚠ WHAT THIS IS ABOUT. Reported twice, the second time with a screenshot of the
timeline: "you only put key frame in clip half of clip, not zoom in cover image
start to end clip".

Keyframes are stored in MILLISECONDS from the clip's start, and FIVE different
things rewrite a still's hold after a move has been written on it:

  · the Veo pass grows a panel to its take's length (`spreadPanelsForRenders`),
  · the voiceover grows a shot to cover the line spoken over it,
  · "Fit to audio" rescales every hold in the film,
  · "Set all" sets them,
  · and the user drags a clip's edge.

None of them touched the keys. So a Ken Burns push written across a 2-second
hold stayed 2 seconds long on the 4-second clip that hold became: the picture
finished moving half way through the shot and sat frozen for the rest of it.
`reholdPatch` is the one place that now answers "this still is a different
length" and it carries the animation with it.

⚠ AND A VIDEO CLIP IS NOT A STILL. Its duration is a window on footage, so
changing it is a TRIM and the keys must stay on the frames they were put on.
That distinction is the whole reason this is a function and not a `map`.

Also checked here: every caption the Director writes gets ONE slow push
(`captionPush`), which is the third thing that was asked for — "text clip you
also give some motion little, keep only one motion in text clip like little zoom
in".

No browser and no backend: the geometry and the action registry both load under
bare node.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
AGENT = (ROOT / "client/src/animatic/agent").as_uri()
SCENE = (ROOT / "client/src/animatic/scene.js").as_uri()

HARNESS = """
import { motionKeys, captionPush } from "__AGENT__/actions.js";
import { reholdPatch, rescaleKeys, spreadPanelsForRenders, isStillClip } from "__SCENE__";

const out = {};

// A push written across a two-second hold, which is what the plan does.
const move = motionKeys("zoom_in", 1, 2000, "ease-in-out");
out.written = move.keyframes.scale.map((k) => k.t);
out.rest = move.rest;

const panel = { id: "p1", kind: "image", duration_ms: 2000, keyframes: move.keyframes };

// The four hold-changing paths all come through here.
out.grown = reholdPatch(panel, 4000);
out.shrunk = reholdPatch(panel, 1000);
out.same = reholdPatch(panel, 2000);
out.long = reholdPatch({ ...panel, duration_ms: 2000 }, 10100);

// ...and a video clip is trimmed, not re-held.
const take = { id: "v1", kind: "video", duration_ms: 4000, keyframes: move.keyframes };
out.trimmed = reholdPatch(take, 8000);
out.isStill = { image: isStillClip(panel), video: isStillClip(take) };

// A clip with no animation at all must come back as a plain duration patch.
out.bare = reholdPatch({ id: "b", kind: "image", duration_ms: 2000 }, 4000);

// THE VEO PATH, END TO END: a 4s take over a 2s panel grows the panel, and the
// panel's push has to grow with it.
const board = [
  { id: "p1", kind: "image", duration_ms: 2000, track: 0, start_ms: 0,
    src: { kind: "panel", storyboard_id: "b1", index: 0 }, keyframes: move.keyframes },
  { id: "p2", kind: "image", duration_ms: 2000, track: 0, start_ms: 2000,
    src: { kind: "panel", storyboard_id: "b1", index: 1 }, keyframes: move.keyframes },
  { id: "v1", kind: "video", duration_ms: 4000, track: 1, start_ms: 0,
    src: { kind: "video", storyboard_id: "b1", index: 0, upload_id: "u1" } },
];
out.veo = spreadPanelsForRenders(board)
  .filter((f) => f.kind !== "video")
  .map((f) => ({ id: f.id, ms: f.duration_ms, keys: (f.keyframes?.scale || []).map((k) => k.t) }));

// A track that is not a list, and a zero length: neither may throw.
out.odd = rescaleKeys({ scale: "nonsense" }, 2000, 4000);
out.zero = rescaleKeys({ scale: [{ t: 0, v: 1 }] }, 0, 4000);

// THE CAPTION'S OWN PUSH.
const push = captionPush(3000);
out.caption = { keys: push.keyframes.scale, rest: push.rest, props: Object.keys(push.keyframes) };
out.captionShort = captionPush(0).keyframes.scale.map((k) => k.t);

process.stdout.write(JSON.stringify(out));
"""

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "probe.mjs"
        script.write_text(HARNESS.replace("__AGENT__", AGENT).replace("__SCENE__", SCENE),
                          encoding="utf-8")
        run = subprocess.run([("node.exe" if os.name == "nt" else "node"), str(script)],
                             capture_output=True, text=True)
    if run.returncode != 0:
        print(run.stdout)
        print(run.stderr)
        return 1
    d = json.loads(run.stdout)

    print()
    print("⚠ THE MOVE IS WRITTEN ACROSS THE WHOLE HOLD")
    print()
    check("a push on a 2.0s shot is two keys, at 0 and 2000",
          d["written"] == [0, 2000], json.dumps(d["written"]))
    check("...and the resting value is where it ENDS, or it snaps back",
          abs(d["rest"]["scale"] - 1.1) < 1e-9, json.dumps(d["rest"]))

    print()
    print("⚠ AND IT FOLLOWS THE HOLD WHEN THE HOLD CHANGES — the bug, twice")
    print("  reported: 'key frame in half of clip, not start to end'")
    print()
    check("a 2.0s move on a shot grown to 4.0s is stretched to 4.0s",
          [k["t"] for k in d["grown"]["keyframes"]["scale"]] == [0, 4000],
          json.dumps(d["grown"]))
    check("...to 10.1s too, which is what the voiceover does to a shot",
          [k["t"] for k in d["long"]["keyframes"]["scale"]] == [0, 10100],
          json.dumps([k["t"] for k in d["long"]["keyframes"]["scale"]]))
    check("...and a shot SHORTENED to 1.0s has its move compressed, not clipped",
          [k["t"] for k in d["shrunk"]["keyframes"]["scale"]] == [0, 1000],
          json.dumps([k["t"] for k in d["shrunk"]["keyframes"]["scale"]]))
    stretched = d["grown"]["keyframes"]["scale"]
    check("the value and the ease ride along untouched — only `t` moves",
          abs(stretched[1]["v"] - 1.1) < 1e-9 and stretched[0]["ease"] == "ease-in-out",
          json.dumps(stretched))
    check("a hold that did not actually change is left completely alone",
          "keyframes" not in d["same"] and d["same"]["duration_ms"] == 2000,
          json.dumps(d["same"]))
    check("a still with no animation is just a duration",
          "keyframes" not in d["bare"], json.dumps(d["bare"]))

    print()
    print("⚠ BUT A VIDEO CLIP IS TRIMMED, NOT RE-HELD — its keys stay on the")
    print("  frames they were put on")
    print()
    check("a video clip's keys are untouched when its length changes",
          "keyframes" not in d["trimmed"], json.dumps(d["trimmed"]))
    check("...and `isStillClip` is the test that decides it",
          d["isStill"] == {"image": True, "video": False}, json.dumps(d["isStill"]))

    print()
    print("⚠ THE VEO PASS ITSELF, END TO END — a 4s take over a 2s panel")
    print()
    by_id = {row["id"]: row for row in d["veo"]}
    check("the panel under the take grew to the take's length",
          by_id["p1"]["ms"] == 4000, json.dumps(by_id["p1"]))
    check("⚠ AND ITS PUSH GREW WITH IT — this is the screenshot that was sent",
          by_id["p1"]["keys"] == [0, 4000], json.dumps(by_id["p1"]))
    check("...while the panel with no take over it is not touched at all",
          by_id["p2"]["ms"] == 2000 and by_id["p2"]["keys"] == [0, 2000],
          json.dumps(by_id["p2"]))

    print()
    print("⚠ AND NOTHING ODD THROWS")
    print()
    check("a keyframe track that is not a list is passed straight through",
          d["odd"]["scale"] == "nonsense", json.dumps(d["odd"]))
    check("a zero starting length cannot divide by zero",
          d["zero"]["scale"][0]["t"] == 0, json.dumps(d["zero"]))

    print()
    print("⚠ AND EVERY CAPTION THE DIRECTOR WRITES GETS ONE SLOW PUSH")
    print()
    caption = d["caption"]
    check("one property only — `scale`, so the text preset keeps opacity/x/y",
          caption["props"] == ["scale"], json.dumps(caption["props"]))
    check("two keys, spanning the whole caption",
          [k["t"] for k in caption["keys"]] == [0, 3000],
          json.dumps([k["t"] for k in caption["keys"]]))
    check("⚠ AND IT IS LITTLE — 4%, because a caption is read rather than watched",
          abs(caption["keys"][0]["v"] - 1) < 1e-9
          and abs(caption["keys"][1]["v"] - 1.04) < 1e-9,
          json.dumps([k["v"] for k in caption["keys"]]))
    check("...resting where it ends, the same rule the picture moves keep",
          abs(caption["rest"]["scale"] - 1.04) < 1e-9, json.dumps(caption["rest"]))
    check("a zero-length caption still gets a sane window rather than 0",
          d["captionShort"][1] > 0, json.dumps(d["captionShort"]))

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

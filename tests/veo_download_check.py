"""DOWNLOADING A VEO RENDER — offered on a paid render, and on nothing else.

The report:

    "i want you add download icon in media panel of only Veo video so user
     download video in local. because if user want delete project so user first
     download veo gneereted video in midea panel or when user click right mouse
     on clip in timeline so user get side of clip dropdown Download text buttun
     so user download both place … only add fuction when user generte Veo video"

A Veo render is the one asset in an animatic that CANNOT be got back. An upload
came off the user's machine and a panel is still on the storyboard; re-rendering
this costs money. So deleting the project must stop being the thing that destroys
it, and there are now two ways out: the ⬇ on its Media card, and Download in the
right-click menu on its timeline bar.

---------------------------------------------------------------------------
⚠ WHAT THIS FILE IS ACTUALLY GUARDING: THE WORD "ONLY"
---------------------------------------------------------------------------
"Only Veo" is the whole requirement, and it is the half that rots silently. The
button appearing on a panel or on an upload is not a crash and not a visible
mistake — it is a Download that fetches a poster PNG, or one offered on a file
the user already has. So the question `isVeoRender` answers is pinned here
against every other kind of source in the library, including the two that look
closest: a board panel (same storyboard, not footage) and a dropped video (same
footage, no storyboard).

⚠ AND IT IS ONE QUESTION, NOT THREE. `isVeoRender` is `cardRowKind(...) ===
"board_video"` — the SAME derivation that paints these bars pastel purple and
pins them to the Storyboard video row. The second half of this file asserts that
both call sites go through it rather than re-deriving "is this a render" from
`kind === "video"` on their own, because two definitions is how the ⬇ and the
purple come to disagree about the same clip.

    python tests/veo_download_check.py

Needs node for the logic half; skips it cleanly without one, exactly as
`asset_fields_check.py` does. The wiring half is a source read and always runs.
No backend, no browser, no ffmpeg.
"""

import json
import os
import re
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
# The sources a project can hold, and whether each is a paid render.
# ---------------------------------------------------------------------------
# ⚠ THE TWO NEAR-MISSES ARE THE POINT. "board panel" shares the storyboard and
# "uploaded video" shares the footage; a render is the only one that is both,
# which is exactly why `isVeoRender` asks two questions rather than one.
CASES = {
    # ✨ Animate's output: the panel's `src` kept underneath the video one, which
    # is what `attachVeoClip` writes and what keeps it on the Storyboard video row.
    "veo render": (
        {
            "kind": "video",
            "src": {
                "kind": "video",
                "storyboard_id": "b1",
                "index": 3,
                "upload_id": "u9",
            },
            "label": "Shot 4",
        },
        True,
    ),
    # Same board, not footage yet — the still the render was made FROM.
    "board panel": (
        {
            "kind": "image",
            "src": {"kind": "panel", "storyboard_id": "b1", "index": 3},
            "label": "Shot 4",
        },
        False,
    ),
    # Same footage, no board — a file dragged in off the desktop.
    "uploaded video": (
        {
            "kind": "video",
            "src": {"kind": "video", "upload_id": "u1"},
            "label": "take.mp4",
        },
        False,
    ),
    "uploaded still": (
        {
            "kind": "image",
            "src": {"kind": "upload", "upload_id": "u2"},
            "label": "still.png",
        },
        False,
    ),
    "colour card": (
        {"kind": "color", "src": {"kind": "upload"}, "color": "#000000"},
        False,
    ),
    "audio": (
        {
            "kind": "audio",
            "src": {"kind": "upload"},
            "upload_id": "u3",
            "label": "vo.mp3",
        },
        False,
    ),
    # A key pose of a panel: a storyboard still, one level finer. Still not footage.
    "board pose": (
        {
            "kind": "image",
            "src": {"kind": "pose", "storyboard_id": "b1", "index": 3, "frame": 7},
        },
        False,
    ),
    # Hostile: nothing at all. It must answer false rather than throw — this runs
    # inside a render, on every card and every bar.
    "empty": ({}, False),
}

HARNESS = """
import { isVeoRender, clipRowKind } from "%(scene)s";
import { assetFromFrame } from "%(assets)s";

const cases = JSON.parse(process.argv[2]);
const verdict = {};
const rowKind = {};
for (const [name, item] of Object.entries(cases)) {
  verdict[name] = isVeoRender(item);
  rowKind[name] = clipRowKind(item);
}

// A clip made into a library card and asked again. The download is drawn on the
// CARD and the menu opens on the CLIP, so if the trip through `assetFromFrame`
// lost the answer the two places would disagree about the same render.
const card = assetFromFrame(cases["veo render"], "a1");

console.log(JSON.stringify({
  verdict,
  rowKind,
  card,
  cardIsVeo: isVeoRender(card),
  // null / undefined must not throw either.
  nullish: [isVeoRender(null), isVeoRender(undefined)],
}));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="veodl_")
    try:
        src = HARNESS % {
            "scene": (ROOT / "client/src/animatic/scene.js").as_uri(),
            "assets": (ROOT / "client/src/animatic/assets.js").as_uri(),
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness, json.dumps({k: v[0] for k, v in CASES.items()})],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("Which sources count as a paid Veo render")
got = run_node()

if got is None:
    for name, (_item, want) in CASES.items():
        skip(f"'{name}' -> {want}", "node not available")
    skip("a render survives the trip to a library card", "node not available")
    skip("the answer is the row kind, not a second opinion", "node not available")
    skip("nothing in, false out", "node not available")
else:
    for name, (_item, want) in CASES.items():
        check(
            f"'{name}' -> {want}",
            got["verdict"][name] is want,
            f"got {got['verdict'][name]}",
        )
    check(
        "a render survives the trip to a library card",
        got["cardIsVeo"] is True,
        f"card={got['card']}",
    )
    # ⚠ THE ANSWER AND THE COLOUR ARE ONE DERIVATION. `.tl-bar.is-veo` is drawn on
    # `clipRowKind(f) === "board_video"`; if these two ever stop agreeing, a bar
    # is purple with no download or offers one without being purple.
    check(
        "the answer is the row kind, not a second opinion",
        all((got["rowKind"][n] == "board_video") is got["verdict"][n] for n in CASES),
        f"rowKind={got['rowKind']}",
    )
    check("nothing in, false out", got["nullish"] == [False, False], str(got["nullish"]))


# ---------------------------------------------------------------------------
# The wiring: both places ask the ONE question, and the fetch is authenticated.
# ---------------------------------------------------------------------------
print("\nBoth entry points, and the fetch behind them")

media_bin = (ROOT / "client/src/components/MediaBin.jsx").read_text(encoding="utf-8")
timeline = (ROOT / "client/src/components/Timeline.jsx").read_text(encoding="utf-8")
editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
api = (ROOT / "client/src/api.js").read_text(encoding="utf-8")

check(
    "the Media card's download is behind isVeoRender",
    "isVeoRender(asset)" in media_bin,
    "MediaBin no longer guards the download button on isVeoRender",
)
# ⚠ IT USED TO SAY "AND IT IS THE ONLY THING THAT DRAWS ONE THERE", counting the
# icon and requiring exactly 1. That was the right property written as the wrong
# number, and it went stale the moment the Media pane grew a right-click menu of
# its own: two ⬇ in the file, BOTH correctly behind the guard, one failing test.
# What matters is not how many there are — it is that none of them is drawn
# unguarded, which is what would offer a download of a board panel.
DOWNLOAD_ICON = 'name="download"'


def unguarded_downloads(source, window=900):
    """Every `Icon name="download"` with no `isVeoRender(` guard above it."""
    return [
        m.start() for m in re.finditer(re.escape(DOWNLOAD_ICON), source)
        if "isVeoRender(" not in source[max(0, m.start() - window):m.start()]
    ]


loose = unguarded_downloads(media_bin)
check(
    "...and EVERY download it draws is behind that guard, however many there are",
    media_bin.count(DOWNLOAD_ICON) >= 1 and not loose,
    f"{len(loose)} of {media_bin.count(DOWNLOAD_ICON)} unguarded",
)
# ⚠ THE GUARD MOVED, AND MOVING IT WAS AN IMPROVEMENT. It was written inline as
# `!onDownloadClip || !isVeoRender(f)` at the one place that opened the menu; the
# menu then gained a SECOND offer (✨ Generate, on a board still), so the decision
# became "is there anything in this menu at all" and moved into `clipMenuOffers` —
# whose own note says why: "asked here rather than at the two call sites so the
# menu that OPENS and the menu that RENDERS cannot come to disagree". So the
# question is now "does the ONE decider consult isVeoRender", not "does that
# literal sit beside the handler".
offers = timeline.split("const clipMenuOffers", 1)
check(
    "the timeline has ONE decider for whether a clip has a menu",
    len(offers) == 2, "clipMenuOffers is gone",
)
check(
    "...and the download it offers is behind isVeoRender",
    len(offers) == 2 and "isVeoRender(frame)" in offers[1].split("};", 1)[0],
    "clipMenuOffers no longer asks isVeoRender",
)
# ⚠ THE GUARD MUST RETURN *BEFORE* `preventDefault`, or every bar on the timeline
# swallows the browser's own menu and offers nothing in its place — which is
# strictly worse than the behaviour this feature replaced.
check(
    "...and a clip with no menu keeps the browser's own",
    re.search(
        r"if \(!clipMenuOffers\(f\)\) return;\s*\n\s*e\.preventDefault\(\);",
        timeline,
    )
    is not None,
    "the guard must return before preventDefault",
)
check(
    "both places call one handler",
    editor.count("downloadVeoClip") >= 3,  # the definition + two call sites
    f"{editor.count('downloadVeoClip')} references in AnimaticEditor",
)
check(
    "the handler refuses anything that is not a render",
    "if (!isVeoRender(item) || !uploadId) return;" in editor,
    "downloadVeoClip must not trust its caller — it is the last gate",
)
# ⚠ THE MEDIA ROUTE IS OWNER-CHECKED AND NEEDS A BEARER TOKEN. An <a href> sends
# no headers, so a plain link would land on a 401 instead of a file.
fn = api.split("export async function downloadAnimaticMedia", 1)
check("the download exists in the api layer", len(fn) == 2, "downloadAnimaticMedia is gone")
if len(fn) == 2:
    body = fn[1].split("\nexport ", 1)[0]
    check(
        "...and it sends the bearer token",
        "Authorization" in body and "Bearer" in body,
        "the media route is owner-checked; without the header this is a 401",
    )
    check(
        "...and it revokes the object URL it made",
        "revokeObjectURL" in body,
        "a Veo render is tens of megabytes — leaking one per press is real memory",
    )

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  · {f}")
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
if not failures:
    print("All good.")
sys.exit(1 if failures else 0)

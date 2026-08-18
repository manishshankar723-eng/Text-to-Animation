"""Auto-reframe: the geometry, the keyframes it writes, and the URL a redraw changes.

Phase 7's one AI path is `autoframe.detect_subject`, and it is the ONLY part of
this that is stubbed. Everything below it is pure arithmetic, and pure
arithmetic about where a picture sits is exactly the kind of thing that is
plausible, self-consistent and wrong — so nothing here checks autoframe against
itself. Four things are checked, each guarding a different way a reframe can
lie:

  1. **The crop is the target shape, inside the picture, and around the
     subject.** Over a table of real framings — a wide with a small figure, a
     close-up, a two-shot at the edge, a subject too big to fit — and both ways
     round (16:9 → 9:16 and 9:16 → 16:9), because widening has no room to pad
     and is where an off-by-one lands.

  2. **THE SUBJECT SURVIVES THE REAL EXPORTER.** The `scale`/`x`/`y` autoframe
     produces are pushed through `animatic_render.place_picture` — the actual
     function that pastes a picture into the actual MP4 — and the subject's
     corners are measured in canvas pixels. This is the check that matters:
     autoframe's arithmetic is the INVERSE of `place_picture`, and the only
     honest way to test an inverse is against the thing it inverts. A sign flip
     in either file fails here.

  3. **What lands on the clip is ordinary keyframes.** The patch is written onto
     a real `AnimaticFrame`, resolved through `animatic_render.scene_at`, and
     compared against the values that went in — so "no new render path" is a
     measured fact rather than a claim. Including a clip that was ALREADY
     keyframed (a Ken Burns push), which must keep its move.

  4. **A regenerated panel changes the served URL.** `_frame_version` off a real
     job record and a real file on disk: touch the panel, and the url the editor
     caches its blob under has to be a different string. This is the trap the
     whole feature dies on — see the 2026-08-09 three-rule entry.

Nothing here spends AI quota, needs a key, or needs ffmpeg.

    python tests/autoframe_check.py
"""

import os
import sys

import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("API_JOB_STORE", "memory")

from PIL import Image

import animatic_render
import autoframe

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
# The fixtures: real framings, in the coordinates the model answers in.
#
# x/y are the subject box's TOP-LEFT as a fraction of the picture, w/h its size.
# Named for what they are, because the failure message is what someone reads at
# 2am — "two-shot at the right edge" says more than "case 3".
# ---------------------------------------------------------------------------
LANDSCAPE = (1600, 900)   # a 16:9 board panel
PORTRAIT = (1080, 1920)   # the 9:16 it is being cut for
SQUARE = (1080, 1080)

SUBJECTS = {
    "a lone figure, small, centre of a wide": {"x": 0.44, "y": 0.38, "w": 0.11, "h": 0.34},
    "a close-up, head filling the frame": {"x": 0.28, "y": 0.10, "w": 0.44, "h": 0.78},
    "a two-shot pushed to the right edge": {"x": 0.55, "y": 0.30, "w": 0.44, "h": 0.55},
    "a figure hard against the left edge": {"x": 0.00, "y": 0.22, "w": 0.18, "h": 0.66},
    "a low subject, feet near the bottom": {"x": 0.30, "y": 0.55, "w": 0.30, "h": 0.44},
    "the whole frame — nothing to crop away": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
}


def contains(crop, subject, tol=1e-9):
    """Is the subject box wholly inside the crop box? Both in source fractions."""
    return (
        crop["x"] <= subject["x"] + tol
        and crop["y"] <= subject["y"] + tol
        and crop["x"] + crop["w"] >= subject["x"] + subject["w"] - tol
        and crop["y"] + crop["h"] >= subject["y"] + subject["h"] - tol
    )


# ---------------------------------------------------------------------------
# 1. The crop box
# ---------------------------------------------------------------------------
print("1. The crop box — target shape, inside the picture, around the subject\n")

for source, target, name in (
    (LANDSCAPE, PORTRAIT, "16:9 → 9:16"),
    (PORTRAIT, LANDSCAPE, "9:16 → 16:9"),
    (LANDSCAPE, SQUARE, "16:9 → 1:1"),
):
    a_s = source[0] / source[1]
    a_t = target[0] / target[1]
    print(f"  --- {name} ---")
    for label, subject in SUBJECTS.items():
        crop = autoframe.crop_box(subject, a_s, a_t)

        # (a) EXACTLY the target aspect. The reason the model is asked for a
        #     subject and not for a crop: "roughly 9:16" is wrong on every shot.
        got = (crop["w"] * source[0]) / (crop["h"] * source[1])
        check(
            f"[{name}] {label}: the crop IS the target aspect",
            close(got, a_t, 1e-6),
            f"(wanted {a_t:.6f}, got {got:.6f})",
        )

        # (b) Inside the picture. A crop off the edge is transparent pixels.
        inside = (
            crop["x"] >= -1e-9
            and crop["y"] >= -1e-9
            and crop["x"] + crop["w"] <= 1 + 1e-9
            and crop["y"] + crop["h"] <= 1 + 1e-9
        )
        check(f"[{name}] {label}: the crop stays inside the picture", inside, f"({crop})")

        # (c) Around the subject — unless it says it couldn't, which is the one
        #     honest exception and only ever happens on a subject that no box of
        #     the target shape can hold.
        if crop["fits"]:
            check(
                f"[{name}] {label}: THE SUBJECT IS INSIDE THE CROP",
                contains(crop, subject),
                f"\n    crop={crop}\n    subject={subject}",
            )
        else:
            impossible = autoframe.crop_box(subject, a_s, a_t, pad=0.0)
            check(
                f"[{name}] {label}: reported as unframable only when it really is",
                not contains(impossible, subject),
                "(said it did not fit, but it fits with no padding)",
            )

# The padding is a nicety and keeping the subject is not: a shot too tight to
# pad must drop the padding rather than crop into the subject.
tight = {"x": 0.35, "y": 0.15, "w": 0.30, "h": 0.70}
padded = autoframe.crop_box(tight, 16 / 9, 9 / 16)
check(
    "a shot too tight to pad drops the padding rather than the subject",
    padded["fits"] and contains(padded, tight),
    f"({padded})",
)

# The punch-in ceiling. A model that reports a distant figure as a tiny box must
# not produce a six-times blow-up of a storyboard panel.
tiny = {"x": 0.48, "y": 0.47, "w": 0.02, "h": 0.04}
zoomed = autoframe.crop_box(tiny, 16 / 9, 9 / 16)
check(
    "a tiny subject is not blown up past the punch-in ceiling",
    zoomed["h"] >= autoframe.MIN_CROP_HEIGHT - 1e-9,
    f"(crop height {zoomed['h']:.3f}, floor {autoframe.MIN_CROP_HEIGHT})",
)
check("…and it still contains the subject", contains(zoomed, tiny), f"({zoomed})")

# Widening: there is no room for a floor, and the ceiling has to win.
wide = autoframe.crop_box(tiny, 9 / 16, 16 / 9)
check(
    "widening ignores the punch-in floor — there is no room for it",
    close(wide["h"], (9 / 16) / (16 / 9), 1e-9),
    f"(got {wide['h']:.4f})",
)


# ---------------------------------------------------------------------------
# 2. THE REAL EXPORTER. autoframe inverts `place_picture`; test it against
#    `place_picture`.
# ---------------------------------------------------------------------------
print("\n2. Through animatic_render.place_picture — the function that makes the MP4\n")


def subject_on_canvas(subject, source, target, fit="contain"):
    """Where the subject's box lands on the exported canvas, in pixels.

    Runs the REAL `place_picture` — not a copy of its arithmetic — so this
    measures autoframe against the exporter rather than against itself.
    """
    values = autoframe.reframe_values(subject, source, target, fit=fit)
    im = Image.new("RGB", source)
    placed, left, top = animatic_render.place_picture(
        im, target, fit=fit, scale=values["scale"], x=values["x"], y=values["y"]
    )
    return (
        left + subject["x"] * placed.width,
        top + subject["y"] * placed.height,
        left + (subject["x"] + subject["w"]) * placed.width,
        top + (subject["y"] + subject["h"]) * placed.height,
    ), values


for source, target, name in (
    (LANDSCAPE, PORTRAIT, "16:9 → 9:16"),
    (PORTRAIT, LANDSCAPE, "9:16 → 16:9"),
    (LANDSCAPE, SQUARE, "16:9 → 1:1"),
):
    for label, subject in SUBJECTS.items():
        (l, t, r, b), values = subject_on_canvas(subject, source, target)
        if not autoframe.crop_box(subject, source[0] / source[1], target[0] / target[1])["fits"]:
            continue
        # One pixel of slack: `place_picture` rounds to whole pixels, and a
        # tolerance smaller than its own quantisation would be testing rounding.
        on_screen = l >= -1.0 and t >= -1.0 and r <= target[0] + 1.0 and b <= target[1] + 1.0
        check(
            f"[{name}] {label}: THE SUBJECT IS ON SCREEN IN THE EXPORT",
            on_screen,
            f"\n    canvas 0,0 → {target[0]},{target[1]}"
            f"\n    subject {l:.1f},{t:.1f} → {r:.1f},{b:.1f}"
            f"\n    values  {values}",
        )

        # And the frame is FILLED — a reframe that letterboxes has not reframed
        # anything. Measured on the picture itself, not on the subject.
        im = Image.new("RGB", source)
        placed, left, top = animatic_render.place_picture(
            im, target, fit="contain",
            scale=values["scale"], x=values["x"], y=values["y"],
        )
        fills = (
            left <= 1
            and top <= 1
            and left + placed.width >= target[0] - 1
            and top + placed.height >= target[1] - 1
        )
        check(f"[{name}] {label}: no letterbox left over", fills,
              f"(picture {left},{top} → {left + placed.width},{top + placed.height})")

# "cover" is the other fit, and it changes `base` — so it changes `scale` for
# the same crop and must still land the subject in the same place on screen.
# (A subject that FITS, deliberately: `fits: False` means no box of this shape
# holds it, and where a picture that cannot be framed ends up is not a fact
# about the fit mode.)
for label in (
    "a lone figure, small, centre of a wide",
    "a figure hard against the left edge",
    "a low subject, feet near the bottom",
):
    (l, t, r, b), values = subject_on_canvas(
        SUBJECTS[label], LANDSCAPE, PORTRAIT, fit="cover"
    )
    check(
        f"fit='cover' frames {label} too, not just the default 'contain'",
        l >= -1 and t >= -1 and r <= PORTRAIT[0] + 1 and b <= PORTRAIT[1] + 1,
        f"({l:.1f},{t:.1f} → {r:.1f},{b:.1f}; values {values})",
    )
    # The two fits must AGREE about where the picture goes — they differ only in
    # what `scale=1` means, and autoframe divides that back out. A reframe that
    # moved when someone flipped Fit would be a reframe nobody could trust.
    (cl, ct, cr, cb), _ = subject_on_canvas(SUBJECTS[label], LANDSCAPE, PORTRAIT)
    check(
        f"…and puts {label} in the SAME place 'contain' does",
        all(close(a, b, 1.5) for a, b in ((l, cl), (t, ct), (r, cr), (b, cb))),
        f"(cover {l:.1f},{t:.1f} → {r:.1f},{b:.1f} vs "
        f"contain {cl:.1f},{ct:.1f} → {cr:.1f},{cb:.1f})",
    )

# A subject no box of the target shape can hold is reported, not silently
# mis-framed — the caller may reasonably decide not to write a reframe it has
# been told is impossible.
huge = autoframe.reframe_values(
    SUBJECTS["a two-shot pushed to the right edge"], LANDSCAPE, PORTRAIT
)
check(
    "a subject too wide for the new shape is REPORTED rather than mis-framed",
    huge["fits"] is False,
    f"({huge})",
)

# Reframing for the shape it is ALREADY in must be a no-op, or "Reframe" on a
# 16:9 project would silently push into every shot.
same = autoframe.reframe_values(
    {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, LANDSCAPE, LANDSCAPE
)
check(
    "framing a picture for its own shape leaves it exactly where it was",
    close(same["scale"], 1.0, 1e-3) and close(same["x"], 0.5, 1e-3) and close(same["y"], 0.5, 1e-3),
    f"({same})",
)


# ---------------------------------------------------------------------------
# 3. What lands on the clip is ORDINARY KEYFRAMABLE PROPERTIES
# ---------------------------------------------------------------------------
print("\n3. What is written — ordinary scale / x / y, resolved by the ordinary path\n")

from server.schemas import AnimaticFrame  # noqa: E402 — after sys.path is set

values = autoframe.reframe_values(
    SUBJECTS["a lone figure, small, centre of a wide"], LANDSCAPE, PORTRAIT
)
patch = autoframe.apply_to_frame({"id": "f1"}, values)

check(
    "the patch is scale / x / y and NOTHING else — no crop field is invented",
    set(patch) == {"scale", "x", "y"},
    f"({sorted(patch)})",
)

still = AnimaticFrame(
    id="f1",
    src={"kind": "panel", "storyboard_id": "b" * 12, "index": 0},
    duration_ms=2000,
    **patch,
)
check("a re-framed clip is still a valid AnimaticFrame", still.scale == values["scale"])

# Resolved through the REAL scene function the monitor and the exporter share.
scene = animatic_render.scene_at({"frames": [still.model_dump()]}, 500)
check(
    "the exporter resolves those values unchanged (no new render path)",
    close(scene["frame"]["scale"], values["scale"])
    and close(scene["frame"]["x"], values["x"])
    and close(scene["frame"]["y"], values["y"]),
    f"\n    wrote   {values}\n    resolved {scene['frame']['scale']}, "
    f"{scene['frame']['x']}, {scene['frame']['y']}",
)
check(
    "a re-framed still is still a STILL — nothing was turned into an animation",
    not animatic_render.is_animated({"frames": [still.model_dump()]}),
)

# A clip that was ALREADY animated. This is the common case, not the exception:
# a Ken Burns push is the first thing anyone does to a held panel.
push = {
    "id": "f2",
    "scale": 1.0,
    "x": 0.5,
    "y": 0.5,
    # ⚠ `t` / `v` — `AnimaticKeyframe`'s real field names. Spelled out here on
    # purpose: writing `t_ms` / `value` produces a track that resolves to the
    # clip's base value with no error anywhere, which is how the first version
    # of `apply_to_frame` looked exactly right and animated nothing.
    "keyframes": {
        "scale": [{"t": 0, "v": 1.0}, {"t": 2000, "v": 1.2}],
        "x": [{"t": 0, "v": 0.5}, {"t": 2000, "v": 0.6}],
    },
}
moved = autoframe.apply_to_frame(push, values)
check("an animated clip keeps its keyframes rather than losing them", "keyframes" in moved)
check(
    "…and keeps the SAME NUMBER of them on each property",
    len(moved["keyframes"]["scale"]) == 2 and len(moved["keyframes"]["x"]) == 2,
)
check(
    "the first key lands on the new framing, not the old one",
    close(moved["keyframes"]["scale"][0]["v"], values["scale"], 1e-3)
    and close(moved["keyframes"]["x"][0]["v"], values["x"], 1e-3),
    f"({moved['keyframes']['scale'][0]}, {moved['keyframes']['x'][0]})",
)
check(
    "THE MOVE SURVIVES — a 20% push is still a 20% push after the reframe",
    close(
        moved["keyframes"]["scale"][1]["v"] / moved["keyframes"]["scale"][0]["v"],
        1.2,
        1e-3,
    ),
    f"(ratio {moved['keyframes']['scale'][1]['v'] / moved['keyframes']['scale'][0]['v']:.4f})",
)
# The pan is measured in fractions of the CANVAS, so the same gesture across the
# same part of a picture drawn 3× larger is 3× the number.
ratio = values["scale"] / 1.0
check(
    "…and so does the pan, scaled to the picture's new size",
    close(
        moved["keyframes"]["x"][1]["v"] - moved["keyframes"]["x"][0]["v"],
        0.1 * ratio,
        1e-3,
    ),
    f"(moved {moved['keyframes']['x'][1]['v'] - moved['keyframes']['x'][0]['v']:.4f}, "
    f"wanted {0.1 * ratio:.4f})",
)
animated = AnimaticFrame(id="f2", src={"kind": "upload", "upload_id": "u" * 12},
                         duration_ms=2000, **moved)
check(
    "a re-framed animated clip is still recognised as animated",
    animatic_render.is_animated({"frames": [animated.model_dump()]}),
)

# The property this pass never touches. An `opacity` key is nothing to do with
# framing and must come through untouched.
# The properties this pass never touches. An `opacity` key is nothing to do with
# framing, and must survive whether or not the clip is also being panned.
fade = {"id": "f3", "keyframes": {"opacity": [{"t": 0, "v": 0.0}]}}
check(
    "a clip keyframed only on something else is not given a keyframe map at all",
    "keyframes" not in autoframe.apply_to_frame(fade, values),
)
both = {
    "id": "f4",
    "keyframes": {
        "x": [{"t": 0, "v": 0.5}, {"t": 1000, "v": 0.7}],
        "opacity": [{"t": 0, "v": 0.0}, {"t": 500, "v": 1.0}],
    },
}
rewritten = autoframe.apply_to_frame(both, values)["keyframes"]
check(
    "a fade rides through a reframe untouched",
    [k["v"] for k in rewritten["opacity"]] == [0.0, 1.0],
    f"({rewritten['opacity']})",
)
check(
    "…while the pan beside it IS re-framed",
    rewritten["x"][0]["v"] != 0.5,
    f"({rewritten['x']})",
)
check(
    "…and every key keeps its TIME — a reframe is not a retime",
    [k["t"] for k in rewritten["x"]] == [0, 1000]
    and [k["t"] for k in rewritten["opacity"]] == [0, 500],
)


# ---------------------------------------------------------------------------
# 4. A REGENERATED PANEL CHANGES THE SERVED URL
#
# The trap the whole feature dies on. Every picture in this app is fetched as an
# authed blob and cached BY URL, so a path that survives a redraw is a picture
# that never updates — which is exactly what "I press Regenerate and nothing
# happens" was. See the 2026-08-09 three-rule entry in AGENTS.md.
# ---------------------------------------------------------------------------
print("\n4. A redrawn panel is a DIFFERENT URL\n")

from server import animatics as animatics_mod  # noqa: E402
from server import jobs as jobs_mod  # noqa: E402
from server.common import board_dir, panel_path  # noqa: E402
from server.jobs import MemoryJobStore  # noqa: E402
from server.schemas import JobKind, JobStatus  # noqa: E402

# A store of our own, with no persist path: this test writes job records and a
# PNG, and neither belongs in whatever the dev environment is using.
store = MemoryJobStore(persist_path=None)
jobs_mod._store = store
OWNER = "reframe@test.local"

board = store.create("Board", kind=JobKind.STORYBOARD, owner=OWNER,
                     params={"aspect_ratio": "16:9"})
BOARD_ID = board.job_id
store.update(BOARD_ID, status=JobStatus.SUCCEEDED,
             result={"panels": [{"index": 0, "url": "x"}], "active_variant": 0})

frame = {
    "id": "shot1",
    "src": {"kind": "panel", "storyboard_id": BOARD_ID, "index": 0},
    "duration_ms": 2000,
}
animatic = store.create("Animatic", kind=JobKind.ANIMATIC, owner=OWNER,
                        params={"frames": [frame]})
ANIMATIC_ID = animatic.job_id

panel_file = panel_path(BOARD_ID, 0, 0)
os.makedirs(os.path.dirname(panel_file), exist_ok=True)
Image.new("RGB", LANDSCAPE, (30, 30, 30)).save(panel_file, "PNG")

before = animatics_mod._project_of(store.get(ANIMATIC_ID)).frames[0].url
check("a frame's url carries a version at all", "?v=" in (before or ""), f"({before})")

# THE REDRAW. Same filename, new bytes — precisely the case a plain path cannot
# express and the client therefore cannot see.
time.sleep(0.02)
Image.new("RGB", LANDSCAPE, (200, 30, 30)).save(panel_file, "PNG")
after = animatics_mod._project_of(store.get(ANIMATIC_ID)).frames[0].url

check(
    "REDRAWING THE PANEL CHANGES THE FRAME'S URL",
    before != after,
    f"\n    before {before}\n    after  {after}",
)
check(
    "…and it is the same route, only the version moved",
    (before or "").split("?")[0] == (after or "").split("?")[0],
    f"({before} vs {after})",
)
check(
    "reading it again without a redraw does NOT change the url",
    animatics_mod._project_of(store.get(ANIMATIC_ID)).frames[0].url == after,
)

# The library card is the other place the picture shows, and it was the one that
# used to be left behind.
check(
    "the library cover url is versioned too",
    "?v=" in (animatics_mod._summarise(store.get(ANIMATIC_ID)).cover_url or ""),
)

# A frame pointing at somebody else's board must not leak a version — the same
# owner check `_resolve_frame_path` makes, and for the same reason.
theirs = store.create("Theirs", kind=JobKind.STORYBOARD, owner="someone.else@test.local")
stolen = AnimaticFrame(
    id="s1", src={"kind": "panel", "storyboard_id": theirs.job_id, "index": 0},
    duration_ms=1000,
)
check(
    "a frame pointing at another account's board gets no version",
    animatics_mod._frame_version(store.get(ANIMATIC_ID), stolen) == "0",
)

# An UPLOAD that has never been touched must not churn either, or every read
# would look like a redraw and the editor would re-fetch the lot.
uploaded = AnimaticFrame(id="u1", src={"kind": "upload", "upload_id": "u" * 12},
                         duration_ms=1000)
check(
    "a frame's version is stable when nothing has changed",
    animatics_mod._frame_version(store.get(ANIMATIC_ID), uploaded)
    == animatics_mod._frame_version(store.get(ANIMATIC_ID), uploaded),
)

# A switched STYLE VARIANT points the frame at a different file. Two panels
# drawn in the same millisecond is not something to rely on not happening.
v0 = animatics_mod._frame_version(store.get(ANIMATIC_ID), AnimaticFrame(**frame))
store.update(BOARD_ID, result={**(store.get(BOARD_ID).result or {}), "active_variant": 1,
                               "variants": [{"panels": [{"index": 0}]},
                                            {"panels": [{"index": 0}]}]})
v1 = animatics_mod._frame_version(store.get(ANIMATIC_ID), AnimaticFrame(**frame))
check("switching the board's style variant changes the version", v0 != v1, f"({v0} vs {v1})")

try:
    os.remove(panel_file)
    os.rmdir(os.path.dirname(panel_file))
    os.rmdir(board_dir(BOARD_ID))
except OSError:
    pass


# ---------------------------------------------------------------------------
# 5. The stubbed model call — the shape of what comes back, not the answer
# ---------------------------------------------------------------------------
print("\n5. The model's answer, coerced\n")

check(
    "a box running off the edge is pulled back rather than refused",
    autoframe.coerce_subject({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5})["w"] <= 0.100001,
)
for bad, why in (
    ({"x": 0.1, "y": 0.1, "w": 0, "h": 0.5}, "no area"),
    ({"x": "left", "y": 0.1, "w": 0.5, "h": 0.5}, "not numbers"),
    ("not a box", "not an object"),
):
    try:
        autoframe.coerce_subject(bad)
        check(f"a model answer with {why} is refused", False, f"({bad!r} was accepted)")
    except autoframe.AutoframeError:
        check(f"a model answer with {why} is refused", True)

check("an aspect ratio parses from the string the project stores",
      close(autoframe.aspect_value("9:16"), 0.5625))
check("…and from a pair of pixel sizes", close(autoframe.aspect_value((1600, 900)), 16 / 9))
for bad in ("", "wide", "0:16", None):
    try:
        autoframe.aspect_value(bad)
        check(f"'{bad}' is refused as an aspect ratio", False)
    except autoframe.AutoframeError:
        check(f"'{bad}' is refused as an aspect ratio", True)

quote = autoframe.estimate(12)
check("the estimate counts the shots it will look at",
      quote["frames"] == 12 and quote["usd"] > 0)
check("an empty run is priced at nothing", autoframe.estimate(0)["usd"] == 0)
check("too many shots is flagged rather than quietly accepted",
      autoframe.estimate(autoframe.MAX_FRAMES + 1)["over_limit"])


# ---------------------------------------------------------------------------
# 6. A LONGER SHOT KEEPS THE DRAWINGS IT ALREADY HAS
#
# The other half of Phase 7. `plan_beats` with `existing_poses` must preserve
# them word for word — a re-plan would leave drawing 17 continuing a motion
# drawings 1–16 never made, and nothing in the pictures reveals that until you
# play it.
# ---------------------------------------------------------------------------
print("\n6. Making a shot longer\n")

import panel_sequence  # noqa: E402

DRAWN = [panel_sequence.OPENING_POSE] + [f"pose {i}" for i in range(2, 9)]  # a 2s shot

spaced = panel_sequence.respace(DRAWN, 4 * panel_sequence.FPS)
check("re-spacing keeps every pose, in order",
      [b["pose"] for b in spaced] == DRAWN)
check("…opens on frame 0 and closes on the last frame",
      spaced[0]["frame"] == 0 and spaced[-1]["frame"] == 4 * panel_sequence.FPS - 1,
      f"({spaced[0]['frame']} … {spaced[-1]['frame']})")
check("…and the frame numbers only ever go forwards",
      all(a["frame"] <= b["frame"] for a, b in zip(spaced, spaced[1:])))

# The model is unreachable here (no key, no network in a test), which exercises
# the fallback — and the fallback is the path that must not lose a drawing.
longer, _hold = panel_sequence.plan_beats(
    "a man asleep", 4, panel_sequence.frame_count_for(4), existing_poses=DRAWN,
)
check("a lengthened shot plans up to the new pose count",
      len(longer) == panel_sequence.frame_count_for(4),
      f"(got {len(longer)}, wanted {panel_sequence.frame_count_for(4)})")
check("THE DRAWINGS ALREADY ON DISK ARE KEPT, WORD FOR WORD",
      [b["pose"] for b in longer[:len(DRAWN)]] == DRAWN,
      f"\n    was  {DRAWN}\n    now  {[b['pose'] for b in longer[:len(DRAWN)]]}")
check("pose 1 is still the panel itself",
      longer[0]["pose"] == panel_sequence.OPENING_POSE and longer[0]["frame"] == 0)
check("the new poses are spread over the LONGER span",
      longer[-1]["frame"] == 4 * panel_sequence.FPS - 1,
      f"(last frame {longer[-1]['frame']})")

# Shortening. Nothing is asked for and nothing is paid for; the drawings past
# the new end stay on disk, so lengthening it again later is free.
shorter, _ = panel_sequence.plan_beats(
    "a man asleep", 2, panel_sequence.frame_count_for(2), existing_poses=DRAWN,
)
check("a SHORTENED shot asks the model for nothing",
      [b["pose"] for b in shorter] == DRAWN[:panel_sequence.frame_count_for(2)])
check("…and re-spaces what is left over the shorter span",
      shorter[-1]["frame"] == 2 * panel_sequence.FPS - 1)

fresh, hold = panel_sequence.plan_beats("a man asleep", 2, panel_sequence.frame_count_for(2))
check("a shot with no existing drawings still gets a full plan",
      len(fresh) == panel_sequence.frame_count_for(2))
check("…and an invariant, even when the planner could not be reached", bool(hold))
check("…opening on the panel itself", fresh[0]["pose"] == panel_sequence.OPENING_POSE)



# ---------------------------------------------------------------------------
# 7. CUT TO BEAT — run under node, against a click track at a known BPM
#
# The arithmetic lives in `client/src/animatic/beat_cut.js` for the same reason
# the razor's and the selection's do: it is pure, it has three rules that are
# easy to get subtly wrong, and none of them are testable from inside a click
# handler. Read that file's header — every check below is one of its rules.
# ---------------------------------------------------------------------------
print("\n7. Cut to beat — the arithmetic, under node\n")

import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A click track at 120 BPM — a beat every 500ms, which is the tempo anyone
# testing this by hand would reach for.
BPM_MS = 500
BEATS = [i * BPM_MS for i in range(40)]

HARNESS = """
import {
  beatMarks,
  cutsToDurations,
  nearestMark,
  planBeatCuts,
} from "%(mod)s";

const marks = beatMarks(
  [
    { upload_id: "music", start_ms: 0, offset_ms: 0 },
    { upload_id: "muted", start_ms: 0, offset_ms: 0, muted: true },
  ],
  { music: { beats: %(beats)s }, muted: { beats: [10, 20, 30] } }
);
const out = { marks };

// A clip's beats are in FILE time. This one starts 2s into the timeline and
// reads from 1s into the file, so a beat at 1500 in the file is heard at 2500.
out.shifted = beatMarks(
  [{ upload_id: "m", start_ms: 2000, offset_ms: 1000, trim_ms: 3000 }],
  { m: { beats: [0, 500, 1000, 1500, 2000, 3000, 4000] } }
);

out.nearestBelow = nearestMark(marks, 480);
out.nearestAbove = nearestMark(marks, 520);
out.nearestExact = nearestMark(marks, 500);
out.nearestTie = nearestMark(marks, 250);
out.nearestPastEnd = nearestMark(marks, 999999);

// Four clips, every cut a little off the beat. All four should be pulled on.
out.tidy = planBeatCuts([520, 480, 505, 495], marks, { minMs: 100 });
out.tidyDurations = cutsToDurations([520, 480, 505, 495], out.tidy.cuts, { minMs: 100 });

// Already on the beat: nothing to do, and it must SAY nothing to do rather than
// writing an identical sequence and marking the project dirty.
out.already = planBeatCuts([500, 500, 500, 500], marks, { minMs: 100 });

// THE CLUSTER CASE. Three very short clips whose nearest beat is the same one.
// Without the running floor these collapse to zero-length — pictures that never
// appear, in an edit that still claims to have them.
out.cluster = planBeatCuts([510, 30, 30, 500], marks, { minMs: 100 });
out.clusterDurations = cutsToDurations([510, 30, 30, 500], out.cluster.cuts, { minMs: 100 });

// A cut nowhere near a beat is left where it is — this feature tightens an
// edit, it does not rewrite one.
out.faraway = planBeatCuts([2000, 2000], [0, 40000], { minMs: 100 });

// No beats at all: nothing moves, and nothing crashes.
out.noMarks = planBeatCuts([300, 400], [], { minMs: 100 });
// One clip has no cuts in it at all.
out.single = planBeatCuts([1234], marks, { minMs: 100 });
out.singleDurations = cutsToDurations([1234], out.single.cuts, { minMs: 100 });

console.log(JSON.stringify(out));
"""


def run_beat_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="beatcut_")
    try:
        src = HARNESS % {
            "mod": (ROOT / "client/src/animatic/beat_cut.js").as_uri(),
            "beats": json.dumps(BEATS),
        }
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(src)
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:600])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


beat = run_beat_node()
if beat is None:
    print("  SKIP  node is not on PATH — the cut-to-beat checks did not run.")
    print("        `npm run build` needs the same node, so this is worth fixing.")
    failures.append("cut to beat (node unavailable)")
else:
    check("a MUTED track contributes no beats — you cannot cut to what you can't hear",
          beat["marks"] == BEATS, f"({beat['marks'][:6]}…)")
    check("beats are walked from FILE time onto the timeline through their clip",
          beat["shifted"] == [2000, 2500, 3000, 4000],
          f"({beat['shifted']})")
    check("…and a beat trimmed off the clip is not a cut point",
          4000 not in [b for b in beat["shifted"] if b > 4000] and len(beat["shifted"]) == 4)

    check("the nearest mark below is found", beat["nearestBelow"]["at"] == 500)
    check("the nearest mark above is found", beat["nearestAbove"]["at"] == 500)
    check("a mark exactly on the cut has a gap of zero",
          beat["nearestExact"]["at"] == 500 and beat["nearestExact"]["gap"] == 0)
    check("a tie picks a mark rather than nothing", beat["nearestTie"]["at"] in (0, 500))
    check("a cut past the last beat still finds the last beat",
          beat["nearestPastEnd"]["at"] == BEATS[-1])

    check("every cut off the beat is pulled onto it",
          beat["tidy"]["cuts"] == [500, 1000, 1500],
          f"({beat['tidy']})")
    # `moved` is 2, not 3, and that is the honest number: the second cut of
    # [520, 480, …] already lands on 1000, so it is on a beat and nothing about
    # it changes. Counting it would make the message overstate the edit.
    check("…and only the cuts that ACTUALLY moved are counted",
          beat["tidy"]["moved"] == 2, f"({beat['tidy']})")
    check("…and the clip durations that come back add up to those cuts",
          beat["tidyDurations"][:3] == [500, 500, 500],
          f"({beat['tidyDurations']})")
    check("THE LAST CUT IS NOT MOVED — it is the end of the video, not an edit",
          len(beat["tidy"]["cuts"]) == 3 and beat["tidyDurations"][3] == 495,
          f"({beat['tidyDurations']})")

    check("a sequence already on the beat reports nothing to do",
          beat["already"]["moved"] == 0, f"({beat['already']})")

    check("TWO CUTS NEVER COLLAPSE ONTO ONE BEAT",
          all(a < b for a, b in zip(beat["cluster"]["cuts"], beat["cluster"]["cuts"][1:])),
          f"({beat['cluster']['cuts']})")
    check("…and no clip is left shorter than the minimum hold",
          all(d >= 100 for d in beat["clusterDurations"]),
          f"({beat['clusterDurations']})")

    check("a cut nowhere near a beat is left where it is",
          beat["faraway"]["moved"] == 0, f"({beat['faraway']})")
    # The cuts still come back — they are just all where they already were.
    # Returning them unchanged rather than an empty list is what keeps
    # `cutsToDurations` total: it is fed every cut, always.
    check("no beats at all moves nothing rather than crashing",
          beat["noMarks"]["moved"] == 0 and beat["noMarks"]["cuts"] == [300])
    check("a one-clip sequence has no cuts and keeps its hold",
          beat["single"]["cuts"] == [] and beat["singleDurations"] == [1234])


print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(
    "The reframe geometry survives the real exporter, writes ordinary keyframes, "
    "a redraw changes the url, a longer shot keeps its drawings, and the cuts "
    "land on the beat without ever landing on each other."
)

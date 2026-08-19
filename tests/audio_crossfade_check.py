"""AUDIO TRANSITIONS: the three crossfade curves, and the cut they land on.

Premiere files three things under Audio Transitions → Crossfade — Constant Gain,
Constant Power, Exponential Fade — and they are three CURVES, not three
mechanisms. So the feature is a curve on each end of an audio clip, plus the
gesture that sets both ends of a cut at once. Which leaves three ways for it to
go quietly wrong, and this checks all three:

1. **The curve is not the curve.** `afade` shapes the exported audio and the
   editor's `curveGain` only PREDICTS it, so the two can drift and nothing fails
   — you would just be auditioning a different fade from the one you encode.
   Both halves are run over the same grid and compared, and the mapping from a
   curve's name to an `afade` curve is checked against the one the browser
   advertises rather than against the comment next to it.

2. **Constant Power does not hold its level.** This is the whole reason the
   curves exist: two constant-GAIN fades crossing scoop about 3 dB out of the
   middle, because amplitudes sum where powers should. Section 5 encodes both,
   decodes the MP4 back and measures the scoop — through two UNCORRELATED tones,
   which is the only material that can tell the two laws apart at all.

3. **The crossfade eats the wrong media.** Laying one grows a clip into its
   handles, and `trim_ms`/`offset_ms`/`start_ms` have to move together or the
   audio slides under the cut. Section 4 states every case as the patch it must
   produce, including the two that cannot overlap at all.

    python tests/audio_crossfade_check.py

Sections 3–5 need `node`; section 5 also needs ffmpeg (`imageio-ffmpeg` provides
one). Missing either is reported as SKIPPED, which is a gap rather than a pass.
"""

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image

from animatic import (
    FADE_CURVES,
    FADE_FF_CURVE,
    audio_graph,
    build_animatic,
    curve_gain,
    fade_curve,
    fade_gain_at,
    ffmpeg_available,
    ffmpeg_exe,
    run_ffmpeg,
)
from server.schemas import AnimaticAudio

ROOT = Path(__file__).resolve().parent.parent
work = tempfile.mkdtemp(prefix="crossfade_")
failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  skip {label}  ({why})")
    skipped.append(label)


def track(**over):
    """One audio clip, at its recorded level with hard edges."""
    base = {
        "id": over.pop("id", "t1"),
        "upload_id": "u1",
        "filename": "sound.wav",
        "duration_ms": 6000,
        "start_ms": 0,
        "offset_ms": 0,
        "trim_ms": None,
        "volume": 1.0,
        "muted": False,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Every animatic saved before this still opens, and still fades as it did
# ---------------------------------------------------------------------------
# The two fields are new, so EVERY existing project is missing them — and the
# default has to be the behaviour that already shipped (`afade`'s own `curve=tri`)
# rather than merely a safe-looking value, or every fade in every saved animatic
# changes shape the day this lands.
print("An animatic saved before crossfades existed")
old = AnimaticAudio(upload_id="u1", fade_in_ms=500, fade_out_ms=800)
check(
    "parses with no curve on it",
    old.fade_in_curve == "linear" and old.fade_out_curve == "linear",
    f"{old.fade_in_curve} / {old.fade_out_curve}",
)
check(
    "and 'linear' is the curve afade already used",
    FADE_FF_CURVE["linear"] == "tri",
    FADE_FF_CURVE["linear"],
)
check(
    "a curve this build has never heard of folds to linear, it does not raise",
    AnimaticAudio(upload_id="u1", fade_in_curve="parabolic-ish").fade_in_curve
    == "parabolic-ish"
    and fade_curve({"fade_in_curve": "parabolic-ish"}, "in") == "linear",
)
check(
    "…and so does a blank one, which is what a bare dict has",
    fade_curve({}, "in") == "linear" and fade_curve({}, "out") == "linear",
)

# ---------------------------------------------------------------------------
# 2. The curve reaches the filter graph
# ---------------------------------------------------------------------------
# Stated on every fade rather than only on the unusual ones: a graph that says
# what it wants only when it wants something surprising is a graph you cannot
# read to find out what it does.
print("\nThe curve reaches the ffmpeg graph")
parts, _out = audio_graph(
    [
        track(
            fade_in_ms=1000,
            fade_out_ms=1000,
            fade_in_curve="power",
            fade_out_curve="exponential",
        )
    ],
    6000,
)
graph = ";".join(parts)
check("the fade in states its curve", "afade=t=in:st=0:d=1.000:curve=qsin" in graph, graph)
check(
    "the fade out states its own, separately",
    "afade=t=out:st=5.000:d=1.000:curve=exp" in graph,
    graph,
)
plain = ";".join(audio_graph([track(fade_in_ms=1000)], 6000)[0])
check("an unshaped fade still says tri out loud", "curve=tri" in plain, plain)
check(
    "every curve maps to a real afade curve",
    sorted(FADE_FF_CURVE) == sorted(FADE_CURVES)
    and set(FADE_FF_CURVE.values()) == {"tri", "qsin", "exp"},
    FADE_FF_CURVE,
)

# ---------------------------------------------------------------------------
# 3 & 4. The browser's half
# ---------------------------------------------------------------------------
# The grid is deliberately dense at both ends: `exponential` spends almost all of
# its travel in the last 6% of the ramp, so a coarse grid would compare three
# curves that all look like a straight line in the middle.
GRID = [i / 40 for i in range(41)]
GAIN_CASES = [
    # (track, ms) — one inside each ramp, one outside both, one on each edge.
    (track(trim_ms=4000, fade_in_ms=1000, fade_out_ms=1000, fade_in_curve="power"), 0),
    (track(trim_ms=4000, fade_in_ms=1000, fade_out_ms=1000, fade_in_curve="power"), 500),
    (track(trim_ms=4000, fade_in_ms=1000, fade_out_ms=1000, fade_in_curve="power"), 1000),
    (track(trim_ms=4000, fade_in_ms=1000, fade_out_ms=1000, fade_out_curve="power"), 3500),
    (
        track(
            trim_ms=4000,
            fade_in_ms=1000,
            fade_out_ms=1000,
            fade_in_curve="exponential",
            fade_out_curve="exponential",
        ),
        940,
    ),
    (track(trim_ms=4000, fade_in_ms=1000, fade_out_ms=1000, fade_out_curve="exponential"), 3060),
    # Two fades longer than the clip, so `fade_window` has scaled them — the
    # curve has to be read off the SCALED window, not the stored numbers.
    (
        track(trim_ms=1000, fade_in_ms=800, fade_out_ms=800, fade_in_curve="power",
              fade_out_curve="power"),
        400,
    ),
]

HARNESS = """
import {
  curveGain,
  fadeCurve,
  fadeGainAt,
  FADE_CURVES,
  FADE_CURVE_INFO,
} from "%(mix)s";
import {
  crossfadePatch,
  crossfadeTarget,
  DEFAULT_CROSSFADE_MS,
  fadeEndPatch,
} from "%(clips)s";

const [grid, gainCases] = JSON.parse(process.argv[2]);
const out = { curves: FADE_CURVES, ff: {} };
for (const c of FADE_CURVES) out.ff[c] = FADE_CURVE_INFO[c].ff;

// --- The curves themselves, over the shared grid ---------------------------
out.gains = {};
for (const c of FADE_CURVES) out.gains[c] = grid.map((x) => curveGain(c, x));
// Out of range on both sides: a clamp, not an extrapolation.
out.clamped = [curveGain("power", -3), curveGain("power", 9)];
// An unknown curve is the straight line, at the point it differs most.
out.unknown = curveGain("nonsense", 0.5);
out.folded = [fadeCurve({ fade_in_curve: "power" }, "in"), fadeCurve({}, "out")];

// --- The whole envelope, so the WINDOW and the curve are checked together ---
out.envelope = gainCases.map(([t, ms]) => fadeGainAt(t, ms));

// --- The crossfade arithmetic ----------------------------------------------
// Two 3s pieces razored out of one 10s file: both have handles, and they abut.
const a = { id: "a", upload_id: "f", duration_ms: 10000, offset_ms: 0, trim_ms: 3000, start_ms: 0 };
const b = { id: "b", upload_id: "f", duration_ms: 10000, offset_ms: 3000, trim_ms: 3000, start_ms: 3000 };
out.butt = crossfadePatch(a, b, DEFAULT_CROSSFADE_MS, "power");

// A whole file dropped after another whole file: no handle anywhere.
const w1 = { id: "w1", upload_id: "g", duration_ms: 3000, offset_ms: 0, trim_ms: 3000, start_ms: 0 };
const w2 = { id: "w2", upload_id: "h", duration_ms: 4000, offset_ms: 0, trim_ms: 4000, start_ms: 3000 };
out.noHandles = crossfadePatch(w1, w2, DEFAULT_CROSSFADE_MS, "power");

// Only the outgoing clip has a handle — the crossfade should still be real, and
// should NOT move the incoming clip to get there.
out.tailOnly = crossfadePatch(a, w2, DEFAULT_CROSSFADE_MS, "power");

// Already overlapping by 2s, which is longer than the preset asks for.
const over = { id: "o", upload_id: "f", duration_ms: 10000, offset_ms: 3000, trim_ms: 3000, start_ms: 1000 };
out.wideOverlap = crossfadePatch(a, over, DEFAULT_CROSSFADE_MS, "power");

// A gap: no junction to sit on.
const far = { id: "far", upload_id: "f", duration_ms: 10000, offset_ms: 3000, trim_ms: 3000, start_ms: 5000 };
out.gap = crossfadePatch(a, far, DEFAULT_CROSSFADE_MS, "power");

// An incoming clip shorter than the crossfade wants to be.
const tiny = { id: "tiny", upload_id: "f", duration_ms: 10000, offset_ms: 3000, trim_ms: 400, start_ms: 3000 };
out.tiny = crossfadePatch(a, tiny, DEFAULT_CROSSFADE_MS, "power");

// --- Where a dropped crossfade lands --------------------------------------
out.target = [200, 1400, 1600, 2900, 3100, 5800, 9000].map((ms) => {
  const t = crossfadeTarget([a, b], ms);
  return t ? [t.clip.id, t.side, t.neighbour ? t.neighbour.id : null] : null;
});
out.fadeEnd = [
  fadeEndPatch(a, "in", 500, "exponential"),
  // Longer than the clip: capped, so it cannot rescale the fade at the far end.
  fadeEndPatch(tiny, "in", 5000, "power"),
];

console.log(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    src = HARNESS % {
        "mix": (ROOT / "client/src/animatic/audio_mix.js").as_uri(),
        "clips": (ROOT / "client/src/animatic/audio_clips.js").as_uri(),
    }
    harness = os.path.join(work, "harness.mjs")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(src)
    proc = subprocess.run(
        ["node", harness, json.dumps([GRID, GAIN_CASES])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print("    node said:", (proc.stderr or "").strip()[:600])
        return None
    return json.loads(proc.stdout)


TWIN_LABELS = [
    "the editor's three curves are the exporter's three, in order",
    "every curve agrees with the exporter's to 1e-12, across the ramp",
    "the browser advertises the same afade curve the exporter builds",
    "x outside 0..1 is clamped, not extrapolated",
    "an unknown curve is the straight line on both sides",
    "the whole fade envelope agrees, window and curve together",
]
XFADE_LABELS = [
    "a butt cut grows into the outgoing clip's tail and moves nothing",
    "…and with only a tail handle it is still a real crossfade",
    "with no handle anywhere it dips through the cut, and says so",
    "an overlap wider than the preset is used as it stands",
    "a gap is refused rather than reached across",
    "a crossfade never outruns the shorter clip it joins",
    "the nearest edge of the clip under the pointer wins",
    "a fade on a free end is capped at the clip's own length",
]

browser = run_node()
print("\nThe editor's curves are the exporter's curves")
if browser is None:
    for label in TWIN_LABELS + XFADE_LABELS:
        skip(label, "node not available")
else:
    check(TWIN_LABELS[0], browser["curves"] == list(FADE_CURVES), browser["curves"])
    worst, worst_at = 0.0, ""
    for curve, theirs in browser["gains"].items():
        for x, got in zip(GRID, theirs):
            delta = abs(curve_gain(curve, x) - got)
            if delta > worst:
                worst, worst_at = delta, f"{curve} at x={x}"
    check(TWIN_LABELS[1], worst < 1e-12, f"worst {worst:.3e} ({worst_at})")
    check(
        TWIN_LABELS[2],
        browser["ff"] == dict(FADE_FF_CURVE),
        f"{browser['ff']} vs {dict(FADE_FF_CURVE)}",
    )
    check(
        TWIN_LABELS[3],
        browser["clamped"] == [curve_gain("power", -3), curve_gain("power", 9)]
        and browser["clamped"] == [0.0, 1.0],
        browser["clamped"],
    )
    check(
        TWIN_LABELS[4],
        abs(browser["unknown"] - 0.5) < 1e-12 and browser["folded"] == ["power", "linear"],
        f"{browser['unknown']} / {browser['folded']}",
    )
    env_worst, env_at = 0.0, ""
    for (case, ms), got in zip(GAIN_CASES, browser["envelope"]):
        delta = abs(fade_gain_at(case, ms) - got)
        if delta > env_worst:
            env_worst, env_at = delta, f"{ms}ms of {case['trim_ms']}ms"
    check(TWIN_LABELS[5], env_worst < 1e-12, f"worst {env_worst:.3e} ({env_at})")

    print("\nLaying a crossfade on a cut")
    butt = browser["butt"]
    check(
        XFADE_LABELS[0],
        butt["ok"]
        and butt["overlapped"]
        and butt["appliedMs"] == 1000
        # The outgoing clip lengthens; the incoming one is not touched beyond its
        # fade, because moving it would shift when its content is heard.
        and butt["patches"]["a"] == {
            "trim_ms": 4000,
            "fade_out_ms": 1000,
            "fade_out_curve": "power",
        }
        and butt["patches"]["b"] == {"fade_in_ms": 1000, "fade_in_curve": "power"},
        butt,
    )
    tail_only = browser["tailOnly"]
    check(
        XFADE_LABELS[1],
        tail_only["ok"]
        and tail_only["overlapped"]
        and tail_only["appliedMs"] == 1000
        and "start_ms" not in tail_only["patches"]["w2"],
        tail_only,
    )
    none = browser["noHandles"]
    check(
        XFADE_LABELS[2],
        none["ok"]
        and none["overlapped"] is False
        and none["appliedMs"] == 500
        and none["patches"]["w1"]["fade_out_ms"] == 500
        and none["patches"]["w2"]["fade_in_ms"] == 500
        # Nothing was moved or re-trimmed: there was nothing to move it into.
        and "trim_ms" not in none["patches"]["w1"]
        and "start_ms" not in none["patches"]["w2"],
        none,
    )
    wide = browser["wideOverlap"]
    check(
        XFADE_LABELS[3],
        wide["ok"]
        and wide["appliedMs"] == 2000
        and "trim_ms" not in wide["patches"]["a"]
        and "start_ms" not in wide["patches"]["o"],
        wide,
    )
    check(XFADE_LABELS[4], browser["gap"] == {"ok": False, "reason": "gap"}, browser["gap"])
    tiny = browser["tiny"]
    check(
        XFADE_LABELS[5],
        tiny["ok"]
        and tiny["appliedMs"] == 400
        # ⚠ AND THE OUTGOING CLIP GREW BY 400, NOT BY 1000. Growing the full
        # second would leave 600ms where both clips play at full level, which is
        # a doubled mix — the one thing a crossfade must never produce.
        and tiny["patches"]["a"]["trim_ms"] == 3400
        and tiny["patches"]["tiny"]["fade_in_ms"] == 400,
        tiny,
    )
    check(
        XFADE_LABELS[6],
        browser["target"]
        == [
            ["a", "in", None],       # first half of the first clip: its free head
            ["a", "in", None],       # still nearer the head
            ["a", "out", "b"],       # past the middle: the cut, with b beyond it
            ["a", "out", "b"],
            ["b", "in", "a"],        # the far side of the same cut
            ["b", "out", None],      # the free tail of the last clip
            None,                    # past everything: no clip, no crossfade
        ],
        browser["target"],
    )
    check(
        XFADE_LABELS[7],
        browser["fadeEnd"]
        == [
            {"fade_in_ms": 500, "fade_in_curve": "exponential"},
            {"fade_in_ms": 400, "fade_in_curve": "power"},
        ],
        browser["fadeEnd"],
    )

# ---------------------------------------------------------------------------
# 5. Constant Power really holds the level that Constant Gain drops
# ---------------------------------------------------------------------------
# ⚠ TWO DIFFERENT TONES, AND THAT IS THE WHOLE FIXTURE. Equal-power crossfading
# is a claim about UNCORRELATED signals: cross one sine with a copy of itself and
# the amplitudes add, constant gain holds perfectly and constant power comes out
# 3 dB LOUD — the exact opposite result, from a test that looks the same. Two
# tones an octave and a half apart add in power instead, which is what real
# material does and what the curves are designed for.
#
# The arithmetic being checked, at the midpoint of a 1s crossfade:
#   constant gain   0.5·A + 0.5·B      → power 0.25+0.25 = 0.5  → 0.707× (−3 dB)
#   constant power  0.707·A + 0.707·B  → power 0.5 +0.5  = 1.0  → 1.000× (flat)
POWER_LABELS = [
    "both exports came out the same length",
    "a constant-gain crossfade scoops the middle out, near enough −3 dB",
    "a constant-power crossfade holds its level right through",
    "…which is the audible difference the two curves exist to offer",
]
print("\nWhat the two laws actually sound like")
if not ffmpeg_available():
    for label in POWER_LABELS:
        skip(label, "ffmpeg not available")
else:
    PICTURE = os.path.join(work, "frame.png")
    Image.new("RGB", (640, 360), (40, 60, 120)).save(PICTURE, "PNG")
    LOW = os.path.join(work, "low.wav")
    HIGH = os.path.join(work, "high.wav")
    for path, hz in ((LOW, 300), (HIGH, 900)):
        run_ffmpeg(
            [
                ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"aevalsrc=0.5*sin(2*PI*{hz}*t):d=6:s=44100",
                path,
            ],
            0,
        )

    def pair(curve: str) -> list[dict]:
        """Two 4s clips overlapping 3.0s → 4.0s, crossfaded with `curve`."""
        return [
            track(
                id="out", upload_id="low", filename="low.wav", path=LOW,
                start_ms=0, trim_ms=4000, fade_out_ms=1000, fade_out_curve=curve,
            ),
            track(
                id="in", upload_id="high", filename="high.wav", path=HIGH,
                start_ms=3000, trim_ms=4000, fade_in_ms=1000, fade_in_curve=curve,
            ),
        ]

    def levels(path: str) -> list[float]:
        """The RMS of every 100ms of the finished mix, mono at 8kHz."""
        proc = subprocess.run(
            [
                ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
                "-i", path, "-vn", "-f", "f32le", "-ac", "1", "-ar", "8000", "-",
            ],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = proc.stdout
        samples = struct.unpack(f"<{len(raw) // 4}f", raw[: (len(raw) // 4) * 4])
        per = 800  # 100ms at 8kHz
        return [
            math.sqrt(sum(v * v for v in samples[s : s + per]) / per)
            for s in range(0, len(samples) - per, per)
        ]

    def band(rms: list[float], from_ms: int, to_ms: int) -> float:
        lo, hi = from_ms // 100, max(from_ms // 100 + 1, to_ms // 100)
        window = rms[lo:hi]
        return sum(window) / len(window) if window else 0.0

    def export(tracks, job) -> str:
        return build_animatic(
            job,
            [{"path": PICTURE, "duration_ms": 8000, "label": ""}],
            audio_tracks=tracks,
            aspect_ratio="16:9",
            resolution=360,
            fps=12,
            output_dir=os.path.join(work, "out"),
        )["video"]

    gain = levels(export(pair("linear"), "xf_gain"))
    power = levels(export(pair("power"), "xf_power"))
    check(POWER_LABELS[0], abs(len(gain) - len(power)) <= 1, f"{len(gain)} vs {len(power)}")

    # Measured against the outgoing tone on its own, well clear of the ramp.
    steady = band(gain, 1000, 2500)
    gain_mid = band(gain, 3300, 3700) / steady
    power_mid = band(power, 3300, 3700) / band(power, 1000, 2500)
    check(
        POWER_LABELS[1],
        abs(gain_mid - 0.707) < 0.06,
        f"{gain_mid:.3f}× through the middle (want ≈0.707)",
    )
    check(
        POWER_LABELS[2],
        abs(power_mid - 1.0) < 0.06,
        f"{power_mid:.3f}× through the middle (want ≈1.000)",
    )
    check(
        POWER_LABELS[3],
        power_mid - gain_mid > 0.18,
        f"constant power is {20 * math.log10(power_mid / gain_mid):.1f} dB louder in the middle",
    )

shutil.rmtree(work, ignore_errors=True)

print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — that is a gap, not a pass:")
    for s in skipped:
        print(f"  - {s}")
    sys.exit(2)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The three crossfades are three curves, and the MP4 has the one you picked.")

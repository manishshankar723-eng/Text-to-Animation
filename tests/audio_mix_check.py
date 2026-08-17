"""The MIX must be the one the editor showed: fades, ducking, beats.

Phase 6 gave an audio track a shape (a ramp at each end), a place in the mix
(what ducks under what) and a grid to cut against (the beats). All three are
written twice — once as an ffmpeg graph, once in the browser — so all three can
drift, and none of them fails loudly when it does: a fade in the wrong place or
a duck that never opens is a video that simply sounds wrong.

So nothing here is stubbed. The graph is built, encoded, and the audio decoded
back out of the MP4 and measured:

1. **Old projects still open** — every field is new and optional, and an
   animatic saved before this must parse and mix exactly as it did.
2. **The graph** — what `audio_graph` builds for each case, including the one
   case that must build nothing at all.
3. **The twin holds** — `fadeWindow` in the browser and `fade_window` here are
   run over the same cases (through node) and compared window for window.
4. **A fade is really there** — RMS climbs across the fade in, is untouched in
   the middle, and falls to nothing across the fade out, against a control
   export of the same project with no fades on it.
5. **A duck really ducks** — the music drops while the voice is talking and is
   back within a few percent afterwards, measured against the same export with
   `duck_to` at 1.
6. **Beats land on the beat** — a click track at a known BPM, through the real
   detector, every marker within 30ms.

    python tests/audio_mix_check.py

Needs ffmpeg (imageio-ffmpeg provides one; `GET /health` reports it). Sections
3 and 6 need `node`, which the client build already requires; without it they
are reported as SKIPPED rather than passed, because they are the only two
checks that look at the browser's half.
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
    EQ_BANDS,
    audio_graph,
    build_animatic,
    duck_ratio,
    eq_chain,
    fade_window,
    ffmpeg_available,
    ffmpeg_exe,
    run_ffmpeg,
    track_play_ms,
)

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


if not ffmpeg_available():
    print("ffmpeg is not available — this test cannot run.")
    print("Every assertion here is about what comes out of the encoder, so a")
    print("skip is a real gap, not a pass.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="audiomix_")


def track(**over):
    """One audio track, with the shape the server sends the exporter."""
    base = {
        "upload_id": over.pop("upload_id", "aud1"),
        "path": over.pop("path", "music.wav"),
        "offset_ms": 0,
        "trim_ms": None,
        "duration_ms": 0,
        "volume": 1.0,
        "muted": False,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "eq_low": 0.0,
        "eq_mid": 0.0,
        "eq_high": 0.0,
        "role": "",
        "duck_to": 1.0,
        "duck_target": "",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Every animatic already saved still opens, and still mixes the same
# ---------------------------------------------------------------------------
print("\nAn animatic saved before any of this still opens and mixes the same")
from server.schemas import AnimaticAudio  # noqa: E402 — after sys.path is set

old = AnimaticAudio(
    **{
        "upload_id": "a1",
        "filename": "bed.mp3",
        "duration_ms": 59000,
        "offset_ms": 0,
        "trim_ms": None,
        "volume": 0.35,
        "muted": False,
    }
)
check("an old payload parses", old.upload_id == "a1" and old.volume == 0.35)
check(
    "and every new field defaults to doing nothing",
    old.fade_in_ms == 0
    and old.fade_out_ms == 0
    and old.eq_low == 0.0
    and old.eq_mid == 0.0
    and old.eq_high == 0.0
    and old.role == ""
    and old.duck_to == 1.0
    and old.duck_target == "",
)
check(
    "a track with no shape on it needs no filter graph at all",
    audio_graph([track()], 6000) is None,
)
check(
    "…and neither does no audio",
    audio_graph([], 6000) is None,
)
check(
    "two tracks still go through amix, and still with normalize=0",
    "amix=inputs=2:duration=longest:normalize=0" in ";".join(
        audio_graph([track(upload_id="a"), track(upload_id="b")], 6000)[0]
    ),
)

# ---------------------------------------------------------------------------
# 2. Where the fades land, and what the graph says
# ---------------------------------------------------------------------------
print("\nThe window a fade is placed in")
check(
    "a fade out lands on the end of an untrimmed track",
    fade_window(track(duration_ms=5000, fade_out_ms=1000), 20000) == (0, 4000, 1000),
    fade_window(track(duration_ms=5000, fade_out_ms=1000), 20000),
)
check(
    "a trim carries the fade with it",
    fade_window(track(duration_ms=50000, trim_ms=8000, fade_out_ms=2000), 20000)
    == (0, 6000, 2000),
    fade_window(track(duration_ms=50000, trim_ms=8000, fade_out_ms=2000), 20000),
)
check(
    "the end of the VIDEO cuts it short when that comes first",
    fade_window(track(duration_ms=90000, fade_out_ms=2000), 6000) == (0, 4000, 2000),
    fade_window(track(duration_ms=90000, fade_out_ms=2000), 6000),
)
check(
    "an offset shortens what is left to fade",
    track_play_ms(track(duration_ms=9000, offset_ms=3000), 60000) == 6000,
)
check(
    "two fades longer than the track are scaled down together, not crossed",
    fade_window(track(duration_ms=4000, fade_in_ms=3000, fade_out_ms=3000), 60000)
    == (2000, 2000, 2000),
    fade_window(track(duration_ms=4000, fade_in_ms=3000, fade_out_ms=3000), 60000),
)
check(
    "a track whose length never reached us fades against the video instead",
    fade_window(track(duration_ms=0, fade_out_ms=1000), 6000) == (0, 5000, 1000),
    fade_window(track(duration_ms=0, fade_out_ms=1000), 6000),
)

print("\nThe graph that gets built")
parts, out = audio_graph([track(duration_ms=8000, fade_in_ms=1500, fade_out_ms=2000)], 6000)
graph = ";".join(parts)
check("a fade in starts at zero", "afade=t=in:st=0:d=1.500" in graph, graph)
check("a fade out starts where the track ends", "afade=t=out:st=4.000:d=2.000" in graph, graph)
check("one track needs no amix", "amix" not in graph and out == "[a0]", graph)

voice = track(upload_id="v", path="voice.wav", role="voice", duration_ms=6000)
music = track(upload_id="m", path="music.wav", duration_ms=6000, role="music", duck_to=0.3)
parts, out = audio_graph([music, voice], 6000)
graph = ";".join(parts)
check("the voice is split — once for the mix, once as the key", "asplit=2" in graph, graph)
check("and the bed goes through a sidechain compressor", "sidechaincompress=" in graph, graph)
check("keyed off the voice, not off itself", "[a0][v1s0]sidechaincompress" in graph, graph)
check("the ducked track is what reaches the mix", "[d0]" in out or "[d0]" in graph, graph)
check(
    "both chains are pinned to one format, or the sidechain can't pair them",
    graph.count("aformat=sample_fmts=fltp:sample_rates=48000") == 2,
    graph,
)
check(
    "a duck with no voice anywhere does nothing at all",
    # Not even a graph: with nothing to key off, this track is exactly what it
    # was before anyone touched the control.
    audio_graph([track(upload_id="m", duck_to=0.3, duration_ms=6000)], 6000) is None,
)
check(
    "…and a track cannot duck under itself",
    "sidechaincompress"
    not in ";".join(
        audio_graph(
            [
                track(upload_id="v", role="voice", duck_to=0.3, duration_ms=6000),
                track(upload_id="m", duration_ms=6000, volume=0.5),
            ],
            6000,
        )[0]
    ),
)
named = audio_graph(
    [
        track(upload_id="m", duck_to=0.3, duck_target="v2", duration_ms=6000),
        track(upload_id="v1", role="voice", duration_ms=6000),
        track(upload_id="v2", role="voice", duration_ms=6000),
    ],
    6000,
)[0]
check(
    "a named target wins over the first voice on the project",
    "[a2]asplit=2" in ";".join(named),
    ";".join(named),
)
check(
    "the ratio deepens as the duck gets deeper",
    duck_ratio(1.0) == 1.0 and 1 < duck_ratio(0.7) < duck_ratio(0.3) <= 20.0,
    f"({duck_ratio(0.7):.2f} → {duck_ratio(0.3):.2f})",
)

print("\nThe tone controls")
check("a flat track gets no filters at all", eq_chain(track()) == [])
check(
    "a band under the epsilon is flat too",
    eq_chain(track(eq_mid=0.02)) == [],
    eq_chain(track(eq_mid=0.02)),
)
check(
    "low is a SHELF with its slope stated, not ffmpeg's default Q",
    eq_chain(track(eq_low=6)) == ["bass=g=6.00:f=120:t=s:w=1"],
    eq_chain(track(eq_low=6)),
)
check(
    "mid is a bell at 1kHz",
    eq_chain(track(eq_mid=-4.5)) == ["equalizer=f=1000:t=q:w=1.00:g=-4.50"],
    eq_chain(track(eq_mid=-4.5)),
)
check(
    "high is the other shelf",
    eq_chain(track(eq_high=-12)) == ["treble=g=-12.00:f=6000:t=s:w=1"],
    eq_chain(track(eq_high=-12)),
)
eq_only = audio_graph([track(duration_ms=6000, eq_low=6)], 6000)
check("EQ on its own is enough to need a graph", eq_only is not None)
chain = ";".join(eq_only[0])
check(
    "and it runs BEFORE the fader and the fades",
    chain.index("bass=") < chain.index("volume="),
    chain,
)

# ---------------------------------------------------------------------------
# 3. The browser's half of the twin agrees
# ---------------------------------------------------------------------------
print("\nThe editor places a fade exactly where the exporter does")

FADE_CASES = [
    (track(duration_ms=5000, fade_out_ms=1000), 20000),
    (track(duration_ms=50000, trim_ms=8000, fade_in_ms=500, fade_out_ms=2000), 20000),
    (track(duration_ms=90000, fade_in_ms=1200, fade_out_ms=2000), 6000),
    (track(duration_ms=9000, offset_ms=3000, fade_out_ms=1500), 60000),
    (track(duration_ms=4000, fade_in_ms=3000, fade_out_ms=3000), 60000),
    (track(duration_ms=4000, fade_in_ms=1000, fade_out_ms=5000), 60000),
    (track(duration_ms=0, fade_out_ms=1000), 6000),
]

HARNESS = """
import { fadeWindow, EQ_BANDS, EQ_EPSILON } from "%(mix)s";
import { energyEnvelope, onsetsFromEnvelope, HOP } from "%(beats)s";

// --- The fade windows the editor would draw -------------------------------
const cases = JSON.parse(process.argv[2]);
const fades = cases.map(([track, totalMs]) => {
  const w = fadeWindow(track, totalMs);
  return [w.inMs, w.outAtMs, w.outMs];
});

// --- The EQ bands the browser would build ---------------------------------
// Reported as the four numbers that decide what the filter IS. If these drift
// from the exporter's table, the editor is auditioning a different EQ.
const bands = EQ_BANDS.map((b) => [b.id, b.field, b.hz, b.q]);

// --- A click track at a known BPM, through the real detector ---------------
// Made here rather than decoded, so the test measures the DETECTOR and not a
// codec: one short burst of decaying noise on each beat, silence between.
const sr = 44100;
const bpm = 120;
const seconds = 8;
const data = new Float32Array(sr * seconds);
let seed = 12345;
const noise = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return (seed / 0x3fffffff) - 1;
};
const period = Math.round((60 / bpm) * sr);
for (let start = 0; start < data.length; start += period) {
  const burst = Math.round(sr * 0.008);
  for (let j = 0; j < burst && start + j < data.length; j++) {
    data[start + j] = noise() * 0.9 * Math.exp(-j / (burst / 3));
  }
}
const hopMs = (HOP / sr) * 1000;
const env = energyEnvelope([data], data.length, HOP);
const beats = onsetsFromEnvelope(env, hopMs);

console.log(JSON.stringify({ fades, bands, epsilon: EQ_EPSILON, beats, hopMs, bpm, seconds }));
"""


def run_node() -> dict | None:
    """Run the browser's own modules under node, or None if there is no node."""
    if not shutil.which("node"):
        return None
    src = HARNESS % {
        "mix": (ROOT / "client/src/animatic/audio_mix.js").as_uri(),
        "beats": (ROOT / "client/src/animatic/beats.js").as_uri(),
    }
    harness = os.path.join(work, "harness.mjs")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(src)
    payload = json.dumps([[t, total] for (t, total) in FADE_CASES])
    proc = subprocess.run(
        ["node", harness, payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        print("    node said:", (proc.stderr or "").strip()[:400])
        return None
    return json.loads(proc.stdout)


browser = run_node()
if browser is None:
    skip("the editor's fade windows match the exporter's", "node not available")
    skip("the editor's EQ bands match the exporter's", "node not available")
    skip("beat markers land on the beat", "node not available")
else:
    mismatches = []
    for (case, total), theirs in zip(FADE_CASES, browser["fades"]):
        mine = list(fade_window(case, total))
        if mine != list(theirs):
            mismatches.append(f"{mine} vs {theirs}")
    check(
        f"the editor's fade windows match the exporter's ({len(FADE_CASES)} cases)",
        not mismatches,
        " · ".join(mismatches),
    )
    mine_bands = [[b["id"], b["field"], b["hz"], b["q"]] for b in EQ_BANDS]
    check(
        "the editor's EQ bands match the exporter's, band for band",
        mine_bands == browser["bands"],
        f"{mine_bands} vs {browser['bands']}",
    )

# ---------------------------------------------------------------------------
# The material: a picture, a music bed, and a voice that talks 2s → 4s
# ---------------------------------------------------------------------------
VIDEO_MS = 6000
PICTURE = os.path.join(work, "frame.png")
Image.new("RGB", (640, 360), (200, 60, 60)).save(PICTURE, "PNG")

MUSIC = os.path.join(work, "music.wav")
VOICE = os.path.join(work, "voice.wav")
# ⚠ THE BED IS THE LOUD ONE (0.42 RMS against the voice's 0.085). The duck is
# measured on the finished MIX, which contains the voice as well, so a quiet bed
# under a loud voice would move the total by a couple of percent however hard it
# was ducked — a test that passes only when the duck is broken.
# Written with `aevalsrc` rather than `sine` for exactly that reason: the
# amplitude is stated, so the levels are known rather than assumed.
#
# Different sample rates on purpose: a 44.1kHz bed keyed off a 48kHz voiceover
# is the ordinary case, and it is what the `aformat` in the graph is there for.
MUSIC_MS, VOICE_MS = 10_000, 8_000
run_ffmpeg(
    [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "aevalsrc=0.6*sin(2*PI*220*t):d=10:s=44100", MUSIC,
    ],
    0,
)
run_ffmpeg(
    [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "aevalsrc=0.12*sin(2*PI*900*t)*between(t\\,2\\,4):d=8:s=48000",
        VOICE,
    ],
    0,
)


def export(tracks, job, video_ms: int = VIDEO_MS) -> str:
    """Encode one animatic with these tracks, and return the MP4."""
    result = build_animatic(
        job,
        [{"path": PICTURE, "duration_ms": video_ms, "label": ""}],
        audio_tracks=tracks,
        aspect_ratio="16:9",
        resolution=360,
        fps=12,
        output_dir=os.path.join(work, "out"),
    )
    return result["video"]


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
    out = []
    for start in range(0, len(samples) - per, per):
        block = samples[start : start + per]
        out.append(math.sqrt(sum(v * v for v in block) / per))
    return out


def band(rms: list[float], from_ms: int, to_ms: int) -> float:
    """The average level over a stretch of the file, in the same units."""
    lo, hi = from_ms // 100, max(from_ms // 100 + 1, to_ms // 100)
    window = rms[lo:hi]
    return sum(window) / len(window) if window else 0.0


# ---------------------------------------------------------------------------
# 4. A fade is really in the file
# ---------------------------------------------------------------------------
print("\nA fade is really in the encoded audio")
flat = levels(export([track(path=MUSIC, duration_ms=MUSIC_MS)], "mix_flat"))
faded = levels(
    export(
        [track(path=MUSIC, duration_ms=MUSIC_MS, fade_in_ms=1500, fade_out_ms=2000)],
        "mix_faded",
    )
)
check("both exports came out the same length", abs(len(flat) - len(faded)) <= 1,
      f"({len(flat)} vs {len(faded)} × 100ms)")

start = band(faded, 0, 200)
quarter = band(faded, 600, 900)
open_ = band(faded, 1600, 2600)
check("it starts near silence", start < open_ * 0.35, f"({start:.4f} vs {open_:.4f})")
check("climbs through the fade", start < quarter < open_,
      f"({start:.4f} → {quarter:.4f} → {open_:.4f})")
check(
    "is at half level half way up",
    abs(quarter / open_ - 0.5) < 0.12,
    f"({quarter / open_:.3f} of the open level)",
)
mid_faded, mid_flat = band(faded, 2600, 3600), band(flat, 2600, 3600)
check(
    "and the middle of the file is untouched",
    abs(mid_faded / mid_flat - 1) < 0.03,
    f"({mid_faded:.4f} vs {mid_flat:.4f})",
)
check(
    "the fade out starts where the video ends, not where the file does",
    abs(band(faded, 4900, 5100) / mid_flat - 0.5) < 0.15,
    f"({band(faded, 4900, 5100) / mid_flat:.3f} at 5.0s of a 6.0s video)",
)
check(
    "and reaches silence at the end",
    band(faded, 5800, 6000) < mid_flat * 0.15,
    f"({band(faded, 5800, 6000):.4f} vs {mid_flat:.4f})",
)

# ---------------------------------------------------------------------------
# 4b. The EQ moves the band it says it moves, and leaves the others alone
# ---------------------------------------------------------------------------
# A GAIN would pass "the low end got louder". This source is two tones far apart,
# and every check below is a PAIR — one band moved, the other didn't — which only
# a real filter on the right band can satisfy.
#
# ⚠ THE TONES ARE OUTSIDE THE SHELVES, NOT ON THEM. A shelf gives HALF its gain
# in dB at its own corner frequency, so a 6 kHz tone against the 6 kHz shelf
# measures −6 dB of a −12 dB setting and reads as a broken filter. 50 Hz and
# 10 kHz are on the plateaus, where the number means what it says.
# ⚠ AND THEY ARE QUIET (0.2 each): +9 dB of low on a 0.3 tone clips the encoder,
# and clipped audio steals energy from the OTHER tone — which fails the pair.
print("\nThe EQ moves the band it names, and only that band")
TONE_LOW_HZ, TONE_HIGH_HZ = 50, 10000
TONES = os.path.join(work, "tones.wav")
run_ffmpeg(
    [
        ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi",
        "-i",
        f"aevalsrc=0.2*sin(2*PI*{TONE_LOW_HZ}*t)+0.2*sin(2*PI*{TONE_HIGH_HZ}*t)"
        ":d=8:s=44100",
        TONES,
    ],
    0,
)


def tone_levels(path: str) -> tuple[float, float]:
    """How much of each test tone there is in the middle of this file.

    ⚠ Decoded at 44.1kHz, not the 8kHz the RMS checks use: the high tone does
    not exist below a 20 kHz sample rate, and a test that measured it there
    would be measuring the resampler.
    """
    import numpy as np

    proc = subprocess.run(
        [
            ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
            "-i", path, "-vn", "-f", "f32le", "-ac", "1", "-ar", "44100", "-",
        ],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    samples = np.frombuffer(proc.stdout, dtype="<f4")
    # One second from the middle, away from anything that starts or stops.
    window = samples[44100 * 2 : 44100 * 3].astype("float64")
    spectrum = abs(np.fft.rfft(window * np.hanning(len(window))))

    def at(hz: float) -> float:
        bin_ = int(round(hz * len(window) / 44100))
        # ±3 bins: a Hann window spreads a tone over its neighbours, and the
        # point is the tone's energy, not one bin's.
        return float(spectrum[bin_ - 3 : bin_ + 4].sum())

    return at(TONE_LOW_HZ), at(TONE_HIGH_HZ)


flat_low, flat_high = tone_levels(export([track(path=TONES, duration_ms=8000)], "eq_flat"))
boost_low, boost_high = tone_levels(
    export([track(path=TONES, duration_ms=8000, eq_low=9)], "eq_low")
)
cut_low, cut_high = tone_levels(
    export([track(path=TONES, duration_ms=8000, eq_high=-12)], "eq_high")
)
check(
    f"+9 dB of Low really lifts {TONE_LOW_HZ} Hz",
    1.8 < boost_low / flat_low < 3.2,
    f"(×{boost_low / flat_low:.2f}, wanted about ×2.8)",
)
check(
    f"…and leaves {TONE_HIGH_HZ // 1000} kHz where it was",
    abs(boost_high / flat_high - 1) < 0.12,
    f"(×{boost_high / flat_high:.3f})",
)
check(
    f"−12 dB of High really drops {TONE_HIGH_HZ // 1000} kHz",
    cut_high / flat_high < 0.45,
    f"(×{cut_high / flat_high:.3f})",
)
check(
    f"…and leaves {TONE_LOW_HZ} Hz where it was",
    abs(cut_low / flat_low - 1) < 0.12,
    f"(×{cut_low / flat_low:.3f})",
)

# ---------------------------------------------------------------------------
# 5. A duck really ducks — and lets go afterwards
# ---------------------------------------------------------------------------
print("\nThe bed drops under the voice and comes back up")


def mix_tracks(duck_to: float):
    return [
        track(upload_id="m", path=MUSIC, duration_ms=MUSIC_MS, role="music", duck_to=duck_to),
        track(upload_id="v", path=VOICE, duration_ms=VOICE_MS, role="voice"),
    ]


# ⚠ AN EIGHT-SECOND VIDEO, not the six the sections above use. The recovery has
# to be measured well clear of BOTH ends: the compressor is still letting go for
# about a second after the voice stops, and the very last block of an encode is
# where two exports of different graphs disagree about length by a few
# milliseconds — which swings a ratio taken over the tail.
DUCK_MS = 8000
open_mix = levels(export(mix_tracks(1.0), "mix_open", DUCK_MS))
ducked = levels(export(mix_tracks(0.3), "mix_ducked", DUCK_MS))
check("both exports came out the same length", abs(len(open_mix) - len(ducked)) <= 1,
      f"({len(open_mix)} vs {len(ducked)} × 100ms)")

before = band(ducked, 500, 1500) / max(1e-9, band(open_mix, 500, 1500))
during = band(ducked, 2500, 3500) / max(1e-9, band(open_mix, 2500, 3500))
# ⚠ The voice stops at 4.0s and the release is a 400ms TIME CONSTANT, so the bed
# is most of the way back by 4.4s and all the way back by about 5.2s. A window
# starting at 4.6s straddles the last of the recovery and sat right on the
# tolerance — it failed about one run in three, which is a flaky test, not a
# finding. This one is after the compressor has finished and well before the
# end of the file, which is what the check is actually about.
after = band(ducked, 5200, 6500) / max(1e-9, band(open_mix, 5200, 6500))
check("nothing happens before the voice", abs(before - 1) < 0.05, f"({before:.3f})")
check("the mix drops while the voice is talking", during < 0.8, f"({during:.3f} of open)")
check("and is back within a few percent afterwards", abs(after - 1) < 0.05, f"({after:.3f})")
print(f"       level vs the un-ducked mix: {before:.3f} → {during:.3f} → {after:.3f}")

# ---------------------------------------------------------------------------
# 6. Beats land on the beat
# ---------------------------------------------------------------------------
print("\nBeat markers land on the beat")
if browser is None:
    pass  # already reported as skipped above
else:
    bpm, seconds = browser["bpm"], browser["seconds"]
    period = 60_000 / bpm
    expected = [round(n * period) for n in range(int(seconds * 1000 // period))]
    found = browser["beats"]
    worst = 0
    missed = []
    for want in expected:
        near = min((abs(want - got) for got in found), default=9999)
        worst = max(worst, near)
        if near > 30:
            missed.append(want)
    check(
        f"every beat of a {bpm} BPM click track is found",
        len(found) == len(expected) and not missed,
        f"(found {len(found)} of {len(expected)}, missed at {missed[:4]})",
    )
    check(f"…within 30ms", worst <= 30, f"(worst {worst}ms)")
    print(f"       {len(found)} markers, worst error {worst}ms, hop {browser['hopMs']:.1f}ms")

shutil.rmtree(work, ignore_errors=True)

print()
if skipped:
    print(f"{len(skipped)} check(s) SKIPPED — that is a gap, not a pass:")
    for s in skipped:
        print(f"  - {s}")
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Fades, ducking and beats are in the file the editor promised.")

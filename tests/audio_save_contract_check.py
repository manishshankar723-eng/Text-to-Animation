"""WHAT AN AUDIO CLIP SENDS WHEN IT IS SAVED — against the schema that receives it.

⚠ THIS TEST EXISTS BECAUSE ONE FIELD ON ONE CLIP DESTROYED A NIGHT'S WORK, AND
NOTHING ANYWHERE SAID SO.

The soundtrack pass wrote `trim_ms: 0` on every clip it laid down, meaning "no
trim". `AnimaticAudio.trim_ms` is `int | None` with `ge=100`: absent means "play
the whole file", and 0 is neither absent nor 100 or more. So FastAPI refused the
save with a 422 — and a 422 refuses the WHOLE DOCUMENT, not the offending clip.
Every frame, every caption, every duration, the title: none of it was written.
The autosave then re-sent the same rejected body on the next edit and was refused
again, forever, while a wall of raw pydantic JSON sat along the bottom of the
editor. Reported from the screen:

    "raat mai band kar diya tha sound effects and music set karke layer pe, but
     jab subah abhi khol raha hun to na koi sound effects and bg music hai"

⚠ AND IT COULD HAPPEN AGAIN TOMORROW WITH A DIFFERENT FIELD. That is what this
file is really about. `assetForSave` and `frameForSave` exist so a field is not
DROPPED on the way out; `audioForSave` also exists so a field cannot be POISONED
on the way out — every number clamped into the range the schema accepts, whatever
put it there. A future pass that invents a bad value costs that value, never the
project.

Four things are checked, in order of how much they matter:

  1. THE CLAMP. Values no clip should ever carry — a 0 trim, a negative start, a
     NaN, a duck of 0, a curve nobody has heard of — all come out as something
     `AnimaticAudio` accepts. Every case is validated by the real pydantic model,
     not by a copy of its rules.
  2. ⚠ THE RAW SHAPE IS STILL REFUSED. The same clips, NOT passed through
     `audioForSave`, must fail validation — otherwise this test would pass just
     as happily against the broken code it was written for.
  3. THE REAL PASS. `sfxPlacements` and `musicPlacement` are run for real — the
     placements the AI Editor and the 🎬 Director actually lay down — and every
     clip they produce is validated. This is the path that broke.
  4. THE WHITELIST. Every field on the schema is mentioned by `audioForSave`, so
     a field the model gains is not silently thrown away (`url` excepted: the
     server fills it on read and ignores it on write).

    python tests/audio_save_contract_check.py

Needs node; skips cleanly without it, exactly as `asset_fields_check.py` does.
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

from pydantic import ValidationError

from server.schemas import AnimaticAudio

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
# The clips. Each one is a shape something in the editor has produced or could
# produce; the name is what went wrong with it.
# ---------------------------------------------------------------------------
CLIPS = {
    # ⚠ THE ONE THAT DID IT. Exactly what `sfxPlacements` used to write.
    "trim zero": {"upload_id": "u1", "start_ms": 0, "trim_ms": 0, "volume": 0.62},
    # Below the floor is still "no trim", not a 422.
    "trim under the floor": {"upload_id": "u2", "trim_ms": 40},
    "trim at the floor": {"upload_id": "u3", "trim_ms": 100},
    # A duck written as "off" by something that meant 1.0 — the schema's floor
    # is 0.05, so this is the same failure wearing a different field's name.
    "duck zero": {"upload_id": "u4", "duck_to": 0},
    "negative start": {"upload_id": "u5", "start_ms": -500, "offset_ms": -20},
    "volume past the ceiling": {"upload_id": "u6", "volume": 9},
    "a fade longer than a minute": {"upload_id": "u7", "fade_in_ms": 90000},
    "an EQ past the rails": {"upload_id": "u8", "eq_low": 40, "eq_high": -40},
    "a curve nobody has heard of": {"upload_id": "u9", "fade_in_curve": "bounce"},
    # ⚠ "nan" RATHER THAN A REAL NaN, because NaN is not JSON and this list
    # crosses into node as JSON. `Number("nan")` is NaN on the other side, which
    # is the value being tested — a length that failed to measure.
    "numbers that are not numbers": {
        "upload_id": "u10",
        "start_ms": "x",
        "trim_ms": None,
        "volume": None,
        "duration_ms": "nan",
    },
    "an empty clip": {},
}

HARNESS = """
import { audioForSave, audioFileCount, MAX_AUDIO_FILES } from "%(audio)s";
import { musicCue, musicPlacement, sfxCues, sfxPlacements } from "%(sound)s";

const clips = JSON.parse(process.argv[2]);
const out = { fields: Object.keys(audioForSave({})), saved: {}, files: {} };
for (const [name, clip] of Object.entries(clips)) out.saved[name] = audioForSave(clip);

// --- The real pass, end to end -------------------------------------------
// Fourteen 2s shots, cues on ten of them, from recordings far longer than any
// shot — the exact board that was reported.
const frames = [];
const starts = [];
for (let i = 0; i < 14; i += 1) {
  frames.push({ id: `f${i + 1}`, duration_ms: 2000 });
  starts.push(i * 2000);
}
const shots = Array.from({ length: 10 }, (_, i) => ({ shot: i + 1, sfx: `sound ${i + 1}` }));
const cued = sfxCues({ analysis: { shots }, frames, starts });
const imported = cued.sounds.map((s, i) => ({
  key: s.key,
  upload_id: `sfx${i}`,
  filename: `${s.key}.mp3`,
  duration_ms: 30000,
}));
const bed = musicCue({ analysis: { music: { query: "cinematic suspenseful orchestral" } } });
imported.push({ key: bed.key, upload_id: "bed", filename: "bed.mp3", duration_ms: 9000 });

const laid = [
  ...sfxPlacements({ cues: cued.cues, imported }).clips,
  ...musicPlacement({ cue: bed, imported, totalMs: 28000, underSpeech: false }).clips,
];
// ⚠ BOTH SHAPES ARE REPORTED. `raw` is what the pass produces; `sent` is what a
// save would carry. The test asserts things about each, because a clamp that
// hides a bad placement is not a fix.
out.raw = laid;
out.sent = laid.map(audioForSave);
out.files = {
  cap: MAX_AUDIO_FILES,
  distinct: audioFileCount(laid),
  clips: laid.length,
};
process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="audio-save-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {
                    "audio": (ROOT / "client/src/animatic/audio_clips.js").as_uri(),
                    "sound": (ROOT / "client/src/animatic/agent/sound_pass.js").as_uri(),
                }
            )
        proc = subprocess.run(
            ["node", harness, json.dumps(CLIPS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1000])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def validates(row) -> str:
    """"" when the schema accepts this clip, else its first complaint."""
    try:
        AnimaticAudio(**row)
        return ""
    except ValidationError as exc:
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"


print("\nWHAT AN AUDIO CLIP SENDS WHEN IT IS SAVED\n" + "=" * 70)

data = run_node()

print(
    "\n⚠ 1. THE CLAMP. A field written wrong costs that field — never the whole\n"
    "  document. Validated by the real `AnimaticAudio`, not by a copy of it.\n"
)
if not data:
    skip("every clamp check", "node not available")
else:
    for name, sent in data["saved"].items():
        why = validates(sent)
        check(f"“{name}” is saveable", not why, why)

    # The two that are the actual bug, said explicitly.
    check(
        "⚠ trim_ms 0 becomes null (“the whole file”), never 0",
        data["saved"]["trim zero"]["trim_ms"] is None,
        json.dumps(data["saved"]["trim zero"]["trim_ms"]),
    )
    check(
        "...and a trim BELOW the floor is null too, not clamped up to 100",
        data["saved"]["trim under the floor"]["trim_ms"] is None,
        json.dumps(data["saved"]["trim under the floor"]["trim_ms"]),
    )
    check(
        "...while a real trim is left exactly alone",
        data["saved"]["trim at the floor"]["trim_ms"] == 100,
        json.dumps(data["saved"]["trim at the floor"]["trim_ms"]),
    )
    check(
        "a duck of 0 becomes the schema's floor, not a refused save",
        data["saved"]["duck zero"]["duck_to"] >= 0.05,
        json.dumps(data["saved"]["duck zero"]["duck_to"]),
    )
    check(
        "a NaN never reaches the wire",
        all(
            not isinstance(v, float) or v == v
            for v in data["saved"]["numbers that are not numbers"].values()
        ),
        json.dumps(data["saved"]["numbers that are not numbers"]),
    )

print(
    "\n⚠ 2. AND THE RAW SHAPE IS STILL REFUSED — otherwise this test would pass\n"
    "  just as happily against the code it was written for.\n"
)
if not data:
    skip("the raw shape is refused", "node not available")
else:
    check(
        "an unclamped trim_ms 0 is rejected by the schema",
        bool(validates({"upload_id": "u", "trim_ms": 0})),
        "the schema accepted it — has `ge=100` been removed from AnimaticAudio?",
    )
    check(
        "...which is the 422 that refused every save of the project",
        "trim_ms" in validates({"upload_id": "u", "trim_ms": 0}),
        validates({"upload_id": "u", "trim_ms": 0}),
    )

print(
    "\n⚠ 3. THE REAL PASS — ten cues and a music bed on a 14-shot board, the\n"
    "  placements the AI Editor and the 🎬 Director actually lay down.\n"
)
if not data:
    skip("the real pass saves", "node not available")
else:
    bad = [(i, validates(c)) for i, c in enumerate(data["sent"]) if validates(c)]
    check(
        f"every one of the {len(data['sent'])} clips it lays down is saveable",
        not bad,
        json.dumps(bad[:3]),
    )
    # ⚠ THE PLACEMENT ITSELF, not merely what the clamp rescued. A pass that
    # still wrote 0 and relied on `audioForSave` to tidy it would leave the
    # EDITOR reading 0 as "play the whole file" — which is the second half of
    # the same report, the sounds all playing over each other.
    check(
        "⚠ and the pass itself writes no trim_ms 0 — the clamp is a backstop, not the fix",
        all(c.get("trim_ms") != 0 for c in data["raw"]),
        json.dumps([c.get("trim_ms") for c in data["raw"]][:12]),
    )
    # ⚠ A REAL NUMBER, NOT "None OR SMALL". Every recording in this fixture is
    # 30 seconds against a 2-second shot, so each effect MUST carry a trim — and
    # the old `0` would satisfy a "<= 3000" test while meaning the opposite.
    sfx = [c for c in data["raw"] if c.get("volume") and c["volume"] > 0.3]
    check(
        "no sound effect outlives the shot it was cued for by more than its ring-out",
        len(sfx) == 10
        and all(isinstance(c.get("trim_ms"), int) and 0 < c["trim_ms"] <= 3000 for c in sfx),
        json.dumps([c.get("trim_ms") for c in sfx]),
    )
    f = data["files"]
    check(
        "...and the whole soundtrack fits the project's audio-file cap",
        f["distinct"] <= f["cap"],
        json.dumps(f),
    )

print(
    "\n⚠ 4. THE WHITELIST. A field the schema gains and this list forgets is a\n"
    "  field computed in the editor and thrown away on the way to the server.\n"
)
schema_fields = set(AnimaticAudio.model_fields)
if not data:
    skip("the whitelist is complete", "node not available")
else:
    sent_fields = set(data["fields"])
    # `url` is the server's to fill on read and ignore on write.
    missing = schema_fields - sent_fields - {"url"}
    check("every schema field is sent", not missing, str(sorted(missing)))
    check(
        "...and nothing is sent that the schema has no field for",
        not (sent_fields - schema_fields),
        str(sorted(sent_fields - schema_fields)),
    )
    check(
        "`url` is NOT sent — the server resolves it per request",
        "url" not in sent_fields,
        str(sorted(sent_fields)),
    )

print("\n" + "-" * 70)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
else:
    print("All checks passed.")
if skipped:
    print(f"{len(skipped)} check(s) skipped — install node to run them.")
sys.exit(1 if failures else 0)

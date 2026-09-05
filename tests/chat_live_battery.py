"""EVERY KIND OF ASK, AGAINST THE REAL MODEL, ON ONE BOARD.

    python tests/chat_live_battery.py              # all nine
    python tests/chat_live_battery.py TEXT VIDEO  # only these

⚠ **NAME THE CASES WHEN THE KEY IS A FREE ONE.** The free tier's cap is counted
in REQUESTS PER DAY, not tokens, so nine turns is nine of whatever the day has —
and the person who has to test the app in a browser afterwards needs some left.
Four well-chosen cases cover every distinct mechanism: TRANSITIONS (steps over a
whole board, and the house cap lifting), TEXT (a step whose `args` must arrive),
EFFECTS (`args.params`, an array INSIDE args — the E123 risk), and VIDEO (the
`passes` branch, which is not a plan at all). The rest repeat those shapes.

⚠ **THIS ONE SPENDS MONEY AND IS NOT A CHECK.** Every other `tests/*_check.py`
runs with no model and no network; this makes NINE real chat turns on the key in
`.env`, and it lives here because the thing it answers cannot be answered any
other way: *does the ✨ chat actually produce work for each kind of request?*

⚠ **IT EXISTS BECAUSE FOUR ROUNDS OF GUESSING PRECEDED ONE LIVE RUN.** On
2026-09-05 "add music and sound effects in this storyboard story wise" came back
with nothing on the timeline, three times over. Reading the code produced three
plausible wrong answers; asking the model produced the right one in twenty
seconds — the schema did not mark `sound.sfx` as `required`, so the model never
wrote the key at all. See **RULEBOOK E123**.

⚠ **A 429 ON EVERY LINE IS QUOTA, NOT A BUG** — the Developer API's free tier has
a daily cap, and this script alone can reach it.

⚠ **NOTHING IS APPLIED.** `editor_chat_agent.chat()` returns a proposal; no
project is opened, no timeline is touched, no browser is involved.

What one line means:

    SOUND  19.5s  kind=plan · steps=0 · sound=14 sfx + music
    TEXT   12.2s  kind=plan · steps=add_text x1
    VIDEO  14.8s  kind=answer · doors=veo

`steps=0` with no `sound=` and no `doors=` is the failure this script is for.
"""

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ `.env` IS READ BY HAND HERE. Nothing has loaded it — this is not the server,
# and the key is the whole point of the script. `setdefault`, so a variable
# already exported on the command line still wins.
ENV_PATH = os.path.join(ROOT, ".env")
if os.path.exists(ENV_PATH):
    for line in io.open(ENV_PATH, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            if value:
                os.environ.setdefault(name.strip(), value.strip())

logging.basicConfig(level=logging.ERROR, format="%(message)s")

import editor_chat_agent as agent  # noqa: E402


def vocabulary() -> dict:
    """The real capability manifest, out of the browser's own module.

    ⚠ **NOT A HAND-WRITTEN COPY.** The manifest is generated from the renderer's
    own tables (`capabilities.js`), and a stale copy here would test the chat
    against a vocabulary the editor does not have — which is the one bug this
    script could never find. Needs `node`; there is no fallback, on purpose.
    """
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is not on PATH — it is needed to read capabilities.js.")
    caps_js = Path(ROOT, "client", "src", "animatic", "agent", "capabilities.js").resolve()
    work = tempfile.mkdtemp(prefix="chat_battery_")
    harness = os.path.join(work, "caps.mjs")
    with io.open(harness, "w", encoding="utf-8") as fh:
        fh.write("import { capabilities } from %s;\n" % json.dumps(caps_js.as_uri()))
        fh.write("process.stdout.write(JSON.stringify(capabilities()));\n")
    done = subprocess.run([node, harness], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("node could not read capabilities.js:\n" + (done.stderr or "")[:800])
    return json.loads(done.stdout)


# A fourteen-shot board of the kind that produced the bug: one picture row, 2s a
# shot, nothing on the timeline yet. Descriptions are short on purpose — a model
# that needs the pictures will ask to LOOK, which is itself worth seeing.
DESCRIPTIONS = [
    "puja table with diyas", "woman lighting a lamp", "man entering the doorway",
    "woman praying", "rangoli powder poured", "family gathering",
    "child running in with sweets", "hands exchanging a gift",
    "lamps flickering in a row", "wide of the courtyard", "woman smiling",
    "fireworks through a window", "everyone eating together", "last diya burning down",
]

BOARD = {
    "title": "Untitled Project",
    "aspect_ratio": "9:16",
    "fps": 24,
    "total_ms": 28000,
    "existing": {"transitionCuts": [], "texts": 0, "shapes": 0, "audioTracks": 0},
    "layers": [{"layer": 4, "name": "Story..Image", "shots": len(DESCRIPTIONS)}],
    "shots": [
        {"ms": 2000, "layer": 4, "label": "Shot %d" % (i + 1), "description": text}
        for i, text in enumerate(DESCRIPTIONS)
    ],
}

# ⚠ ONE MESSAGE PER KIND OF WORK, AND EACH IS PHRASED THE WAY A PERSON TYPES IT —
# not the way the schema wants it. "story wise" is here because it is the exact
# sentence that failed, and because it asks for EVERY shot, which is what a house
# default refuses (E106).
CASES = [
    ("SOUND", "add music and sound effects in this storyboard story wise"),
    ("TRANSITIONS", "put a dissolve on every cut"),
    ("TEXT", "add a title on shot 1 that says Happy Diwali"),
    ("EFFECTS", "add a warm cinematic effect on every shot"),
    ("DURATION", "make shot 3 four seconds long"),
    ("MOTION", "add a slow push in on shot 10"),
    ("VIDEO", "make a real video from this storyboard"),
    ("IMAGES", "draw the animatic images for these shots"),
    ("CAPTIONS", "add captions to this"),
]


def summarise(turn: dict) -> str:
    """One line: what a person would be able to DO with this turn."""
    steps = (turn.get("plan") or {}).get("steps") or []
    verbs: dict = {}
    for step in steps:
        verbs[step.get("verb")] = verbs.get(step.get("verb"), 0) + 1
    sound = turn.get("sound") or {}
    cues = len(sound.get("sfx") or [])
    doors = [p.get("door") for p in (turn.get("passes") or [])]

    bits = ["kind=%s" % turn.get("kind")]
    bits.append("steps=" + (", ".join("%s x%d" % (v, n) for v, n in verbs.items()) or "0"))
    if cues or sound.get("music"):
        bits.append("sound=%d sfx%s" % (cues, " + music" if sound.get("music") else ""))
    if doors:
        bits.append("doors=" + ",".join(doors))
    if turn.get("look"):
        bits.append("LOOK at %d shot(s)" % len(turn["look"]["shots"]))
    if turn.get("ask"):
        bits.append("ask(%d options)" % len(turn["ask"]["options"]))
    if turn.get("dropped"):
        bits.append("DROPPED=%d" % len(turn["dropped"]))
    return " · ".join(bits)


def chosen() -> list:
    """The cases named on the command line, or all of them.

    ⚠ **AN UNKNOWN NAME IS AN ERROR, NOT AN EMPTY RUN.** A typo that quietly ran
    nothing would look exactly like a key with no quota left, which is the one
    thing this script exists to tell apart.
    """
    asked = [a.strip().upper() for a in sys.argv[1:] if a.strip()]
    if not asked:
        return CASES
    known = {name for name, _ in CASES}
    unknown = [a for a in asked if a not in known]
    if unknown:
        raise SystemExit("no such case(s): %s\nknown: %s"
                         % (", ".join(unknown), ", ".join(sorted(known))))
    return [(name, message) for name, message in CASES if name in asked]


def main() -> int:
    cases = chosen()
    caps = vocabulary()
    print()
    print("%d verbs in the manifest · %d shots on the board · %d live call(s) to make"
          % (len(caps.get("verbs") or []), len(DESCRIPTIONS), len(cases)))
    print("=" * 78)
    quota = 0
    for name, message in cases:
        started = time.monotonic()
        drops: list = []
        try:
            turn = agent.chat(messages=[{"role": "user", "text": message}], board=BOARD,
                              vocabulary=caps, settings=None, language="", pictures=())
            line = summarise(turn)
            drops = [d.get("why") for d in (turn.get("dropped") or [])][:3]
        except Exception as error:  # noqa: BLE001 — the reason IS the result here
            said = str(error)
            quota += 1 if ("429" in said or "rate limited" in said) else 0
            line = "FAILED: %s" % said[:110]
        print("%-12s %5.1fs  %s" % (name, time.monotonic() - started, line))
        for why in drops:
            print("%s drop: %s" % (" " * 19, why))
    print("=" * 78)
    if quota:
        print("⚠ %d of %d calls hit the quota wall (HTTP 429). That is the free tier's daily"
              % (quota, len(cases)))
        print("  cap, not a fault in the chat — try again tomorrow, or on a paid key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

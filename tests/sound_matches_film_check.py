"""THE SOUND HAS TO BELONG TO **THIS** FILM — and the words to say so must reach the model.

⚠ THE FAILURE THIS SUITE EXISTS FOR, REPORTED FROM THE SCREEN ON 2026-09-05. A
storyboard of a Hindu festival at home — marigold garlands, lit diyas, a rangoli,
a woman at a decorated puja table — was scored by the ✨ AI Editor with:

    "pop bubble" · "camera shutter" · "digital beep" · "mouse click" ·
    "glitch static" · and a bed of "upbeat energetic corporate pop vlog"

Every one of those is a UI noise from a tech vlog. It looks like a taste failure
and it is not one. It is the **only** answer available to something that was
handed this and nothing else:

    - Title: (untitled)
    - 14 shot(s), 0:28 total, 9:16 at 24fps
    1. [2.0s] Shot 1
    2. [2.0s] Shot 2      … fourteen times

⚠ AND THE WORDS EXISTED THE WHOLE TIME. `boardFrom` sends `description:
frame.description || frame.prompt`, and `AnimaticFrame` HAS NEITHER FIELD — so
that key has been "" on every turn since it was written. What a shot is OF lives
on the storyboard PANEL the frame references (`src.storyboard_id` + `src.index`),
along with its location, the board's genre and world, and the script it all came
from. None of it ever reached the model.

⚠ WHICH IS ALSO WHY THIS IS TWO KINDS OF TEST AT ONCE, AND THEY ARE MARKED. The
plumbing is PAKKA — either the description reaches the prompt or it does not, and
that is a fact a test can settle. What the model then DOES with it is GUZARISH:
a prompt rule, provable only by a live run. So sections 1-4 assert the words
arrive; section 5 asserts the rules that use them are still in the two prompts,
which is the most a test can honestly claim about a prompt.

    python tests/sound_matches_film_check.py

Spends nothing: no model call, no Freesound key, no network. Needs node only for
the last section (what the browser puts on the wire).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sandbox import pin  # ⚠ FIRST, before any server import

_TMP = pin("sound_match_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml  # noqa: E402

from server.common import fill_board_words  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.schemas import JobKind  # noqa: E402

import director  # noqa: E402
from editor_chat_agent import board_digest  # noqa: E402

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
# The fixture: the board that was actually mis-scored, and the project made
# from it. Two owners, because the owner check is one of the things under test.
# ---------------------------------------------------------------------------
MINE = "me@example.com"
THEIRS = "someone-else@example.com"

PANELS = [
    {
        "index": 0,
        "description": "Marigold garlands and lit diyas on a decorated puja table.",
        "location": "home shrine",
        "url": "panel_00.png",
    },
    {
        "index": 1,
        "description": "A woman folds her hands before the idol as the aarti begins.",
        "location": "home shrine",
        "url": "panel_01.png",
    },
    {
        "index": 2,
        "description": "The family carries the idol to the water at dusk.",
        "location": "riverbank",
        "url": "panel_02.png",
    },
]

store = get_store()

board = store.create(
    "Ganesh Chaturthi at home",
    kind=JobKind.STORYBOARD,
    owner=MINE,
    params={
        "genre": "festival short",
        "script": "A family prepares the puja, welcomes Ganpati home, and says "
                  "goodbye at the visarjan.",
        "world": {"setting": "a decorated home shrine", "period": "present day"},
        "market": {"label": "India", "language": "Hindi"},
    },
)
store.update(board.job_id, result={"panels": PANELS})
board = store.get(board.job_id)

# The same board, owned by somebody else. A crafted `storyboard_id` pointing here
# must contribute nothing at all.
other = store.create(
    "Somebody else's film",
    kind=JobKind.STORYBOARD,
    owner=THEIRS,
    params={"genre": "thriller", "script": "Not yours to read."},
)
store.update(other.job_id, result={"panels": PANELS})

project = store.create(
    "Untitled Project",
    kind=JobKind.ANIMATIC,
    owner=MINE,
    params={"source_storyboard_id": board.job_id},
)
project = store.get(project.job_id)


def a_board(**over):
    """The read-model the browser sends — every description blank, as it really is."""
    body = {
        "title": "Untitled Project",
        "aspect_ratio": "9:16",
        "fps": 24,
        "total_ms": 6000,
        "shots": [
            {
                "label": f"Shot {i + 1}",
                "ms": 2000,
                "description": "",
                "dialogue": "",
                "src": {"kind": "panel", "storyboard_id": board.job_id, "index": i},
            }
            for i in range(3)
        ],
    }
    body.update(over)
    return body


print("\nTHE SOUND HAS TO BELONG TO THIS FILM\n" + "=" * 70)

print(
    "\n⚠ 1. THE WORDS REACH THE SERVER'S COPY OF THE BOARD.  [PAKKA]\n"
    "  The browser cannot send them — a description lives on the PANEL, not on\n"
    "  the frame — so this is the only place they can be put back.\n"
)
filled = fill_board_words(a_board(), project)
shots = filled["shots"]
check(
    "every shot gets the description of the panel it came from",
    [s["description"] for s in shots] == [p["description"] for p in PANELS],
    json.dumps([s["description"] for s in shots]),
)
check(
    "…and its location, which is the single best word for what a shot SOUNDS like",
    [s.get("location") for s in shots] == ["home shrine", "home shrine", "riverbank"],
    json.dumps([s.get("location") for s in shots]),
)
check(
    "the film gets a header saying what KIND of film it is",
    filled.get("film", {}).get("genre") == "festival short",
    json.dumps(filled.get("film")),
)
# ⚠ `.get` THROUGHOUT, so a broken pipe REPORTS as failures instead of dying on
# a KeyError halfway down — a suite that crashes tells you far less than one that
# says which four things stopped arriving.
film = filled.get("film") or {}
check(
    "…the board's REAL title, not the project's placeholder",
    film.get("title") == "Ganesh Chaturthi at home" and filled["title"] == "Untitled Project",
    json.dumps([film.get("title"), filled.get("title")]),
)
check(
    "…its world, its market and its language",
    str(film.get("world") or "").startswith("a decorated home shrine")
    and film.get("market") == "India"
    and film.get("language") == "Hindi",
    json.dumps(film),
)
check(
    "…and what it is about, from the script",
    "visarjan" in str(film.get("logline") or ""),
    str(film.get("logline")),
)
# ⚠ A REAL MUTATION TEST, not a fresh fixture compared with itself. `director.py`
# hashes the board it was handed as part of its determinism claim, so a helper
# that wrote through to its argument would make that hash depend on call order.
untouched = a_board()
fill_board_words(untouched, project)
check(
    "⚠ the caller's board is NOT mutated — the Director hashes the one it was given",
    untouched["shots"][0]["description"] == "" and "film" not in untouched,
    json.dumps(untouched["shots"][0]),
)

print(
    "\n⚠ 2. AND ONLY WHERE THE USER LEFT A BLANK.  [PAKKA]\n"
    "  A description they typed is what they MEAN this shot to be; the panel's\n"
    "  is only what it started as.\n"
)
mine = a_board()
mine["shots"][1]["description"] = "Her hands, very close."
kept = fill_board_words(mine, project)
check(
    "a description the user wrote outranks the panel's",
    kept["shots"][1]["description"] == "Her hands, very close.",
    kept["shots"][1]["description"],
)
check(
    "…and the shots around it are still filled in",
    kept["shots"][0]["description"] == PANELS[0]["description"],
    kept["shots"][0]["description"],
)

print(
    "\n⚠ 3. IT CANNOT BE USED TO READ SOMEBODY ELSE'S FILM.  [PAKKA]\n"
    "  Frames are user-editable JSON, so a crafted `storyboard_id` is a request\n"
    "  the server must refuse — the same guard `_voice_lines_of` already makes.\n"
)
stolen = a_board()
for i, shot in enumerate(stolen["shots"]):
    shot["src"] = {"kind": "panel", "storyboard_id": other.job_id, "index": i}
stolen.pop("film", None)
blind_project = store.get(
    store.create("No source", kind=JobKind.ANIMATIC, owner=MINE, params={}).job_id
)
robbed = fill_board_words(stolen, blind_project)
check(
    "a src pointing at another account's board fills in NOTHING",
    all(not s["description"] for s in robbed["shots"]),
    json.dumps([s["description"] for s in robbed["shots"]]),
)
check(
    "…and leaks no header from it either",
    not robbed.get("film"),
    json.dumps(robbed.get("film")),
)
gone = a_board()
for shot in gone["shots"]:
    shot["src"] = {"kind": "panel", "storyboard_id": "deadbeefdead", "index": 0}
missing = fill_board_words(gone, blind_project)
check(
    "a board that has been deleted is 'no words', never an error",
    all(not s["description"] for s in missing["shots"]),
    json.dumps([s["description"] for s in missing["shots"]]),
)

print(
    "\n⚠ 4. THE MODEL ACTUALLY SEES THEM — and is TOLD when there is nothing.  [PAKKA]\n"
    "  A digest that carries the words and a prompt that never prints them is\n"
    "  the same bug one layer along.\n"
)
seeing = board_digest(filled)
check(
    "the chat digest opens with what the film IS",
    "WHAT THIS FILM IS" in seeing and "festival short" in seeing,
    seeing[:160],
)
check(
    "…and every shot line carries its place and its description",
    "(home shrine)" in seeing and "Marigold garlands" in seeing,
    seeing[-300:],
)
blind = board_digest(a_board(shots=[{"label": f"Shot {i + 1}", "ms": 2000} for i in range(14)]))
check(
    "⚠ a film with no words at all is told so IN AS MANY WORDS",
    "NOTHING HERE SAYS WHAT THIS FILM IS ABOUT" in blind,
    blind[:400],
)
check(
    "…and is pointed at the two honest moves, `look` and `ask`",
    "`look`" in blind and "`ask`" in blind,
    blind[-400:],
)
check(
    "…which a film that HAS words is never told",
    "NOTHING HERE SAYS" not in seeing,
    seeing[:200],
)

brief = director.build_brief(filled, brief_text="", language="Hindi")
check(
    "the Director's brief carries the same header",
    brief.get("film", {}).get("genre") == "festival short",
    json.dumps(brief.get("film")),
)
check(
    "…and every shot's location",
    brief["shots"][2]["location"] == "riverbank",
    json.dumps(brief["shots"][2]),
)
check(
    "a project with no source board gets `film: {}` rather than empty strings",
    director.build_brief(a_board(), "", "") .get("film") == {},
    json.dumps(director.build_brief(a_board(), "", "").get("film")),
)
# ⚠ THE DETERMINISM CLAIM STILL HOLDS. `analyse_request` is hashed by
# `director_determinism_check`, and a new key built from an unordered dict would
# make that hash depend on what the caller sent first.
check(
    "…and the header is built in a FIXED key order, for the determinism hash",
    list(brief["film"]) == list(director.build_brief(filled, "", "Hindi")["film"]),
    json.dumps(list(brief["film"])),
)

print(
    "\n⚠ 5. THE RULES THAT USE THE WORDS ARE IN BOTH PROMPTS.  [GUZARISH]\n"
    "  A prompt rule cannot be proven by a test — only a live run can say the\n"
    "  model obeyed it. What IS provable is that the rule is still there, in\n"
    "  both places, because there are two sound writers and they drift.\n"
)
chat_system = yaml.safe_load((ROOT / "prompts.yaml").read_text(encoding="utf-8"))
chat_system = chat_system["editor_chat"]["system"]
director_sound = director.sound_instruction()

for name, text in (("the chat prompt", chat_system), ("the Director's", director_sound)):
    check(
        f"{name}: name the film before scoring it",
        "KIND OF FILM THIS IS" in text,
        text[:80],
    )
    check(
        f"{name}: the cue must belong to that world",
        "WOULD ACTUALLY BE HEARD" in text,
        "",
    )
    check(
        f"{name}: ⚠ the tech-vlog default is named as the trap it is",
        "corporate pop vlog" in text and "glitch" in text,
        "",
    )
    check(
        f"{name}: culture and faith are the user's, never swapped",
        "NEVER YOURS TO SWAP" in text or "NEVER YOURS TO" in text,
        "",
    )
    check(
        f"{name}: a film you cannot describe is not scored at all",
        "DO NOT SCORE IT" in text,
        "",
    )
    check(
        f"{name}: the bed is named by instrument or tradition, not by a genre folder",
        "GENRE LABEL" in text and "sitar" in text,
        "",
    )
check(
    "the chat is told that SOUND is the content question a look exists for",
    "SOUND IS A CONTENT QUESTION" in chat_system,
    "",
)

print(
    "\n⚠ 6. AND THE BROWSER PUTS `src` ON THE WIRE.  [PAKKA]\n"
    "  Without it the server cannot tell which panel a shot came from, and\n"
    "  every section above is describing a pipe with nothing in it.\n"
)

HARNESS = """
import { boardFrom } from "%(dir)s";
const ctx = {
  title: "Untitled Project",
  frames: [
    { id: "f1", label: "Shot 1", duration_ms: 2000,
      src: { kind: "panel", storyboard_id: "abc123", index: 0 } },
    { id: "f2", label: "Shot 2", duration_ms: 2000 },
  ],
  starts: [0, 2000], totalMs: 4000,
};
const out = boardFrom(ctx);
process.stdout.write(JSON.stringify({
  first: out.shots[0].src || null,
  second: Object.prototype.hasOwnProperty.call(out.shots[1], "src"),
}));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="sound-match-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {"dir": (ROOT / "client/src/animatic/agent/useDirectorRun.js").as_uri()}
            )
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:800])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


wire = run_node()
if wire is None:
    skip("boardFrom sends `src`", "node not available (or the module needs a browser)")
else:
    check(
        "a shot that came from a board carries its panel reference",
        (wire["first"] or {}).get("storyboard_id") == "abc123"
        and (wire["first"] or {}).get("index") == 0,
        json.dumps(wire["first"]),
    )
    check(
        "…and a shot that came from nowhere carries no empty `src` key",
        wire["second"] is False,
        json.dumps(wire),
    )

print("\n" + "-" * 70)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
else:
    print("All checks passed.")
if skipped:
    print(f"{len(skipped)} check(s) skipped.")
sys.exit(1 if failures else 0)

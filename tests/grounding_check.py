"""Unit checks for breakdown determinism + the grounding (hallucination) report.

Pure logic — no network, no AI quota, no server. Exercises the two things that
decide whether a breakdown can be trusted:

  1. `_sampling_kwargs` — the determinism settings actually sent to the model.
  2. `_find_span` / `_attach_script_lines` / `build_grounding_report` — whether
     a shot is really backed by the script, or the model made it up.

    python tests/grounding_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import script_breakdown as sb

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}: got {got!r}, want {want!r}")
        failures.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# ---------------------------------------------------------------------------
# A tiny script with a known shape, so every assertion below is checkable by eye
# ---------------------------------------------------------------------------
SCRIPT = (
    "Lubdhaka the hunter climbed the bilva tree at dusk and waited for the deer.\n"
    "Through the long cold night he plucked the leaves and let them fall.\n"
    "By morning the forest was silent, and Shiva stood before him.\n"
)


# ---------------------------------------------------------------------------
# 1. Sampling / determinism
# ---------------------------------------------------------------------------
print("\n[1] sampling kwargs")

_SAMPLING_ENV = ("TEXT_TEMPERATURE", "TEXT_TOP_P", "TEXT_SEED")
_saved = {k: os.environ.get(k) for k in _SAMPLING_ENV}


def _set_env(**values) -> None:
    for key in _SAMPLING_ENV:
        os.environ.pop(key, None)
    for key, value in values.items():
        os.environ[key] = value


try:
    _set_env()
    kw = sb._sampling_kwargs()
    check("default temperature is greedy", kw.get("temperature"), 0.0)
    check("default top_p", kw.get("top_p"), 1.0)
    check("default seed is fixed", kw.get("seed"), 42)

    _set_env(TEXT_TEMPERATURE="0.7", TEXT_SEED="7")
    kw = sb._sampling_kwargs()
    check("env overrides temperature", kw.get("temperature"), 0.7)
    check("env overrides seed", kw.get("seed"), 7)

    _set_env(TEXT_SEED="none")
    check("seed=none is omitted", "seed" in sb._sampling_kwargs(), False)

    _set_env(TEXT_TEMPERATURE="hot", TEXT_SEED="abc")
    kw = sb._sampling_kwargs()
    check("junk temperature falls back", kw.get("temperature"), 0.0)
    check("junk seed is dropped", "seed" in kw, False)
finally:
    for key, value in _saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# 2. Quote matching — exact vs fuzzy vs invented
# ---------------------------------------------------------------------------
print("\n[2] quote matching")

flat, _ = sb._flatten_script(SCRIPT)

span = sb._find_span(flat, sb._norm("By morning the forest was silent"))
check_true("verbatim quote is found", span)
check("verbatim quote reports exact", span[2] if span else None, "exact")

# Same sentence with one word inserted mid-quote: head and tail still anchor, so
# it resolves — but it must NOT claim to be exact.
span = sb._find_span(
    flat,
    sb._norm("Through the long cold night he plucked the golden leaves and let them fall"),
)
check_true("paraphrased quote still resolves", span)
check("paraphrased quote reports fuzzy", span[2] if span else None, "fuzzy")

check(
    "invented quote is rejected",
    sb._find_span(flat, sb._norm("Ravi drew his sword and charged the dragon")),
    None,
)


# ---------------------------------------------------------------------------
# 3. _attach_script_lines writes the match kind onto each shot
# ---------------------------------------------------------------------------
print("\n[3] attach script lines")

shots = [
    {
        "scene_number": 1,
        "shot_number": 1,
        "description": "Lubdhaka crouches in the bilva tree through the cold night, plucking leaves.",
        "characters": ["Lubdhaka"],
        "dialogue": [],
        "assets": ["bilva tree"],
        "location": "forest",
        "camera": "wide establishing",
        "script_line": "",
        "script_line_start": None,
        "script_line_end": None,
        "script_line_match": "",
        "script_excerpt": "Lubdhaka the hunter climbed the bilva tree at dusk",
    },
    {
        "scene_number": 2,
        "shot_number": 1,
        "description": "A spaceship lands on a neon runway while androids applaud.",
        "characters": ["Shiva"],
        "dialogue": [{"character": "Narrator", "line": "Dawn came at last."}],
        "assets": ["deer"],
        "location": "forest",
        "camera": "close-up",
        "script_line": "",
        "script_line_start": None,
        "script_line_end": None,
        "script_line_match": "",
        "script_excerpt": "Ravi drew his sword and charged the dragon",
    },
]

sb._attach_script_lines(shots, SCRIPT)

check("grounded shot got its line", shots[0]["script_line_match"], "exact")
check_true("grounded shot has text", shots[0]["script_line"])
check("grounded shot line number", shots[0]["script_line_start"], 1)
check("invented shot has no match", shots[1]["script_line_match"], "")
check("invented shot shows no line", shots[1]["script_line"], "")


# ---------------------------------------------------------------------------
# 4. The grounding report
# ---------------------------------------------------------------------------
print("\n[4] grounding report")

characters = [
    {"name": "Lubdhaka", "description": "a lean South Asian hunter"},
    {"name": "Ravi", "description": "invented — never in the script"},
]
assets = [
    {"name": "bilva tree", "category": "background", "description": "a broad tree"},
    {"name": "laser rifle", "category": "prop", "description": "invented"},
]

report = sb.build_grounding_report(shots, characters, assets, SCRIPT)

check("counts every shot", report["shots_total"], 2)
check("counts exact quotes", report["quotes_exact"], 1)
check("counts missing quotes", report["quotes_missing"], 1)
check("quote rate", report["quote_rate"], 0.5)

weak = {(w["scene_number"], w["shot_number"]) for w in report["weak_descriptions"]}
check("invented description is flagged", (2, 1) in weak, True)
check("grounded description is not flagged", (1, 1) in weak, False)

check("cast not in script", report["unknown_characters"], ["Ravi"])
check("asset not in script", report["unknown_assets"], ["laser rifle"])
check("shot character missing from cast", report["uncast_shot_characters"], ["Shiva"])
check("shot asset missing from list", report["unlisted_shot_assets"], ["deer"])
check("speaker missing from cast", report["uncast_speakers"], ["Narrator"])
check_true("warnings are written for a human", report["warnings"])

# A clean breakdown must stay quiet — the report is worthless if it cries wolf.
clean = sb.build_grounding_report(
    [shots[0]], [characters[0]], [assets[0]], SCRIPT
)
check("clean breakdown has no warnings", clean["warnings"], [])
check("clean breakdown quote rate", clean["quote_rate"], 1.0)

# Descriptions made only of camera vocabulary aren't evidence of anything.
check(
    "pure camera direction is not flagged",
    sb._describes_script("Wide establishing shot, low angle.", sb._content_words(SCRIPT)),
    1.0,
)


# ---------------------------------------------------------------------------
print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All grounding checks passed.")

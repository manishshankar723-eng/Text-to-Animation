"""THE SAME BRIEF TWICE GIVES THE SAME PLAN TWICE.

    python tests/director_determinism_check.py

⚠ WHY THIS MATTERS MORE THAN IT LOOKS. "Read it again" is a button in the 🎬
preview, and it has exactly one job: let you change something about your film and
see what the Director makes of it NOW. That is only a comparison if everything
else held still. A planner that re-rolls gives you two different films and no way
to tell which difference came from your edit — so the button stops being a
comparison and becomes a fruit machine, and the honest thing to do would be to
remove it.

⚠ AND HERE IS THE LIMIT OF THE CLAIM, said out loud, because the alternative is a
test that promises something no Gemini endpoint delivers:

  · NO GEMINI ENDPOINT IS BIT-EXACT. Serving-side batching means even temperature
    0 can differ occasionally. Greedy decoding with a fixed seed makes two runs
    COMPARABLE, not identical, and this file therefore never asserts that two
    live calls matched — it has no live calls in it.
  · `gemini-2.5-flash` IS A ROLLING ALIAS. Pin `DIRECTOR_MODEL` to a dated
    snapshot if you need plans comparable across weeks. Sampling settings cannot
    hold a moving model still.

So what IS asserted is everything on OUR side of the line, which is the part that
can be made exactly reproducible and the part that actually breaks:

  1. THE REQUEST IS BYTE-IDENTICAL. Same board, same brief, same fingerprint —
    covering the prompt, the schema AND the sampling settings, because a schema
    change moves the answer as surely as a prompt change does.
  2. THE SAMPLING IS GREEDY AND SEEDED, and BOTH calls use the same settings.
    The polish call decides from what analyse said; two different decodings would
    be a plan written from a story half-remembered.
  3. THE POST-PROCESSING IS A PURE FUNCTION. Given one model answer, folding,
    the language fence and the payload come out identical every time — no dict
    iteration order, no set, no clock, no `random`.
  4. THE BOARD READS THE SAME WAY TWICE, including when the caller hands the
    shots over in a different object shape.

⚠ NO MODEL IS CALLED — `llm_json.use_adapter` stands in for one. Nothing here
touches a browser, a backend, a model or a dollar.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import director
import llm_json

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def manifest() -> dict:
    code = (
        "import('%s').then(m=>process.stdout.write(JSON.stringify(m.capabilities())))"
        % (AGENT / "capabilities.js").as_uri()
    )
    proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8")
    return json.loads(proc.stdout) if proc.returncode == 0 else {}


BOARD = {
    "title": "The Long Way Round",
    "aspect_ratio": "16:9",
    "fps": 24,
    "shots": [
        {"label": "Kitchen", "ms": 2000, "description": "She reads the note.", "dialogue": ""},
        {"label": "Hall", "ms": 6000, "description": "She stands in the doorway.",
         "dialogue": "Mira: I'm not going."},
        {"label": "Street", "ms": 2500, "description": "Rain on the tarmac.", "dialogue": ""},
        {"label": "Bus", "ms": 5000, "description": "She is on it anyway.", "dialogue": ""},
    ],
    "existing": {"transitionCuts": [], "texts": 0, "shapes": 0, "audioTracks": 1},
}

REPLY = {
    "analyse": {
        "logline": "She says she is not going, and goes.",
        "mood": "resigned",
        "genre": "kitchen-sink",
        "scenes": [
            {"start_shot": 1, "end_shot": 2, "title": "The house", "why": "she leaves it"},
            {"start_shot": 3, "end_shot": 4, "title": "Outside", "why": "new place"},
        ],
        "shots": [
            {"shot": 1, "beat": "she reads it", "emphasis": "normal",
             "motion": "Static close on the note.", "dialogue": ""},
            {"shot": 2, "beat": "she refuses", "emphasis": "high",
             "motion": "Slow push in on the doorway.", "dialogue": "I'm not going."},
            {"shot": 3, "beat": "the weather", "emphasis": "low",
             "motion": "Rain, static wide.", "dialogue": ""},
            {"shot": 4, "beat": "she goes", "emphasis": "high",
             "motion": "Handheld, following the bus.", "dialogue": ""},
        ],
        "title_card": "",
        "notes": ["No time of day is given anywhere."],
    },
    "polish": {
        "summary": "Dissolve out of the house; push in on the refusal.",
        "mood": "resigned",
        "steps": [
            {"verb": "note", "args": {"text": "Two scenes, four shots."}, "note": ""},
            {"verb": "add_transition", "args": {"cut": 2, "kind": "dissolve", "ms": 900},
             "note": "she leaves the house"},
            {"verb": "push_in", "args": {"shot": 2, "from": 1, "to": 1.08, "ease": "ease-in-out"},
             "note": "the refusal"},
            {"verb": "add_effect", "args": {"shot": 3, "kind": "saturation",
                                            "params": [{"name": "amount", "value": "0.8"}]},
             "note": "rain"},
        ],
    },
}


def run_once(caps, board=None, brief="Kitchen-sink drama. Hold the doorway."):
    """One whole pass through the brain, against a fixed model answer."""
    seen = []

    def adapter(request):
        seen.append(request)
        return json.dumps(REPLY[request.purpose])

    previous = llm_json.use_adapter(adapter)
    try:
        out = director.direct(
            board=board or BOARD,
            vocabulary=caps,
            include={"transitions": True, "effects": True, "text": True, "shapes": True},
            language="english",
            brief_text=brief,
        )
    finally:
        llm_json.use_adapter(previous)
    return out, seen


def main():
    caps = manifest()
    if not caps.get("verbs"):
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 1

    print("\n⚠ THE SAMPLING IS GREEDY, AND IT IS THE SAME FOR BOTH CALLS\n")
    sampling = llm_json.sampling()
    check("temperature is 0 — the model's best answer, not one of its answers",
          sampling.get("temperature") == 0.0, json.dumps(sampling))
    check("top_p is 1 — no nucleus trimming on top of that", sampling.get("top_p") == 1.0)
    check("⚠ A SEED IS SENT. Without one, temperature 0 still varies between runs",
          "seed" in sampling, json.dumps(sampling))
    check("`is_greedy` agrees, which is what the rest of the app asks",
          llm_json.is_greedy(), json.dumps(sampling))
    check("DIRECTOR_SEED=none turns it off deliberately, and says so",
          not llm_json.is_greedy({"temperature": 0.0, "top_p": 1.0}))

    # -------------------------------------------------------- two whole runs
    print("\n⚠ THE SAME BOARD TWICE: THE REQUEST IS BYTE-IDENTICAL\n")
    first, sent_a = run_once(caps)
    second, sent_b = run_once(caps)

    check("both runs made both calls", len(sent_a) == len(sent_b) == 2)
    for a, b in zip(sent_a, sent_b):
        check(f"[{a.purpose}] the prompt is identical", a.prompt == b.prompt)
        check(f"[{a.purpose}] the schema is identical", a.schema == b.schema)
        check(f"[{a.purpose}] ⚠ SO IS THE FINGERPRINT — prompt, schema AND sampling",
              a.fingerprint() == b.fingerprint(), f"{a.fingerprint()[:10]} vs {b.fingerprint()[:10]}")
    check("⚠ THE TWO CALLS SHARE ONE SET OF SAMPLING SETTINGS",
          json.loads(json.dumps({**llm_json.sampling(), **sent_a[0].sampling}))
          == json.loads(json.dumps({**llm_json.sampling(), **sent_a[1].sampling})))

    print("\n⚠ AND THE PLAN THAT COMES OUT IS IDENTICAL, KEY FOR KEY\n")
    blob = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False)
    check("the plan", blob(first["plan"]) == blob(second["plan"]))
    check("the reading it was written from", blob(first["analysis"]) == blob(second["analysis"]))
    check("the Veo motion prompts", blob(first["veo"]) == blob(second["veo"]))
    check("what was dropped, and why", blob(first["dropped"]) == blob(second["dropped"]))
    check("...and it is not identical because it is EMPTY",
          len(first["plan"]["steps"]) >= 4 and len(first["veo"]) == 4,
          f"{len(first['plan']['steps'])} steps, {len(first['veo'])} prompts")

    # ------------------------------------------------------- the brief itself
    print("\n⚠ THE BRIEF IS BUILT IN A FIXED ORDER — no dict iteration, no set, no clock\n")
    brief_a = director.build_brief(BOARD, brief_text="x", language="english")
    brief_b = director.build_brief(BOARD, brief_text="x", language="english")
    check("built twice, byte for byte the same", blob(brief_a) == blob(brief_b))
    # ⚠ THE SAME BOARD IN A DIFFERENT SHAPE IS THE SAME BOARD. The editor sends
    # `ms`; an older payload might send `duration_ms`. Both are the same film and
    # must produce the same request, or "read it again" after a client update
    # would look like the Director changed its mind.
    other_shape = {
        **BOARD,
        "shots": [
            {"label": s["label"], "duration_ms": s["ms"], "description": s["description"],
             "dialogue": s["dialogue"]}
            for s in BOARD["shots"]
        ],
    }
    check("⚠ `ms` and `duration_ms` are the same board, and hash the same",
          blob(director.build_brief(other_shape, brief_text="x", language="english"))
          == blob(brief_a))
    check("the shot count is never truncated — a plan must see the ending",
          brief_a["shot_count"] == len(BOARD["shots"]) == len(brief_a["shots"]))

    print("\n⚠ A DIFFERENT BRIEF IS A DIFFERENT REQUEST — the seed does not freeze the INPUT\n")
    _, sent_c = run_once(caps, brief="Make it a comedy.")
    check("changing the sentence changes the fingerprint",
          sent_c[0].fingerprint() != sent_a[0].fingerprint())
    longer = {**BOARD, "shots": BOARD["shots"] + [
        {"label": "Window", "ms": 3000, "description": "The house, from outside.", "dialogue": ""}
    ]}
    _, sent_d = run_once(caps, board=longer)
    check("adding a shot changes it too", sent_d[0].fingerprint() != sent_a[0].fingerprint())

    print("\n⚠ FOLDING IS A PURE FUNCTION OF ITS INPUT\n")
    raw = REPLY["polish"]["steps"]
    once = director.fold_steps(raw, caps)
    twice = director.fold_steps(raw, caps)
    check("same steps in, same steps out", blob(once) == blob(twice))
    check("⚠ AND THE ARGUMENT ORDER IS STABLE — a dict built in iteration order "
          "would hash differently run to run",
          [list(s["args"]) for s in once[0]] == [list(s["args"]) for s in twice[0]],
          json.dumps([list(s["args"]) for s in once[0]]))
    fenced = director.enforce_language(once[0], "english")
    check("the language fence is pure as well",
          blob(fenced) == blob(director.enforce_language(twice[0], "english")))

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

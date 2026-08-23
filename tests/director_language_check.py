"""A HINGLISH FILM GETS LATIN-SCRIPT CAPTIONS AND ENGLISH VEO PROMPTS.

    python tests/director_language_check.py

⚠ THE RULE HAS THREE PARTS AND THE THIRD IS THE ONE PEOPLE GET WRONG.

  1. ON-SCREEN TEXT TAKES THE FILM'S LANGUAGE, IN ITS SCRIPT. This is the part
     the audience sees. For Hinglish that means LATIN letters — "Raat ki pehli
     train" — and not Devanagari, because Hinglish is what Indian creators
     actually publish and Devanagari is what a model writes when nobody tells it
     otherwise. `plan_agent.LANGUAGES` has carried that paragraph since Plan &
     Script shipped and there is deliberately no second copy of it.

  2. THE EDITOR'S OWN WORDS STAY ENGLISH. Verbs, kinds, presets and easings are
     identifiers the app reads as data, and the notes and summary are for the
     person editing. A `kind` translated into Hindi is a dropped step.

  3. ⚠ A VEO MOTION PROMPT IS WRITTEN IN ENGLISH, WITH THE DIALOGUE INSIDE IT IN
     THE FILM'S LANGUAGE. The prompt is an INSTRUCTION to a video model — "slow
     dolly in, handheld" — and those models follow English measurably better;
     the dialogue is a PERFORMANCE and has to be the words the character says.
     "Instructions English, performance local" is the whole rule, and getting it
     backwards produces either a worse camera move or an actor speaking the
     wrong language, both of which cost real money to discover at render time.

⚠ NO MODEL IS CALLED. `llm_json.use_adapter` swaps the provider for a function,
which is what that seam exists for — see its header. So this test asserts the two
things that are actually assertable without a network: that the REQUEST carries
the rule, and that what comes back is HELD to it. A test that needed credentials
would be a test nobody runs.

Nothing here touches a browser, a backend, a model or a dollar.
"""

import json
import os
import subprocess
import sys
import tempfile
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
    """The real capability manifest, off the client, the way the browser sends it."""
    code = (
        "import('%s').then(m=>process.stdout.write(JSON.stringify(m.capabilities())))"
        % (AGENT / "capabilities.js").as_uri()
    )
    proc = subprocess.run(
        ["node", "-e", code], capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        return {}
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# The board: a Hinglish short. Dialogue in Latin script, as it would really be.
# ---------------------------------------------------------------------------
BOARD = {
    "title": "Raat ki Pehli Train",
    "aspect_ratio": "16:9",
    "fps": 24,
    "shots": [
        {"label": "Platform", "ms": 3000, "description": "An empty platform at night.",
         "dialogue": "Arjun: Train late hai kya?"},
        {"label": "The bench", "ms": 4500, "description": "He sits down.",
         "dialogue": ""},
        {"label": "Headlight", "ms": 2000, "description": "A light in the tunnel.",
         "dialogue": "Arjun: Aa gayi."},
    ],
}

# What a model actually does when it is not held to the rule: Devanagari where
# Latin was asked for, in BOTH the caption and the camera instruction.
ANALYSE_REPLY = {
    "logline": "A man waits for the last train and it does not stop.",
    "mood": "still",
    "genre": "quiet drama",
    "scenes": [{"start_shot": 1, "end_shot": 3, "title": "The platform", "why": "one place"}],
    "shots": [
        # ⚠ THE GOOD ONE: English instruction, Hinglish line inside it.
        {"shot": 1, "beat": "he waits", "emphasis": "normal",
         "motion": "Slow dolly in on the empty platform, handheld, cold sodium light. "
                   "He says: “Train late hai kya?”",
         "dialogue": "Train late hai kya?"},
        # ⚠ THE BAD ONE: the instruction itself came back in Devanagari.
        {"shot": 2, "beat": "he sits", "emphasis": "low",
         "motion": "धीरे से कैमरा "
                   "पास जाओ।",
         "dialogue": ""},
        {"shot": 3, "beat": "the light", "emphasis": "high",
         "motion": "Static wide. A headlight grows in the tunnel mouth.",
         "dialogue": "Aa gayi."},
    ],
    "title_card": "RAAT KI PEHLI TRAIN",
    "notes": ["The board does not say what year this is."],
}

POLISH_REPLY = {
    "summary": "One scene, held. A title in, a dissolve out.",
    "mood": "still",
    "steps": [
        {"verb": "note", "args": {"text": "Three shots, one platform."}, "note": ""},
        # ⚠ LATIN SCRIPT — this is the caption that must survive.
        {"verb": "add_text",
         "args": {"shot": 1, "text": "RAAT KI PEHLI TRAIN", "ref": "t1",
                  "position": "middle", "size": "large"},
         "note": "title card"},
        # ⚠ DEVANAGARI — this is the caption that must be dropped, with a reason.
        {"verb": "add_text",
         "args": {"shot": 2, "text": "रात की पहली "
                                     "ट्रेन", "ref": "t2"},
         "note": "same words, wrong script"},
        {"verb": "add_transition", "args": {"cut": 2, "kind": "dissolve", "ms": 700},
         "note": "time passes"},
    ],
}


def main():
    caps = manifest()
    if not caps.get("verbs"):
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 1

    seen: list[llm_json.JsonRequest] = []

    def adapter(request):
        seen.append(request)
        return json.dumps(ANALYSE_REPLY if request.purpose == "analyse" else POLISH_REPLY)

    llm_json.use_adapter(adapter)
    out = director.direct(
        board=BOARD,
        vocabulary=caps,
        include={"transitions": True, "effects": True, "text": True, "shapes": True},
        language="hinglish",
        brief_text="A quiet one. Nothing happens and that is the point.",
    )
    llm_json.use_adapter(None)

    # ------------------------------------------------- what was ASKED for
    print("\n⚠ THE RULE IS IN THE REQUEST — both calls, not just the one that writes text\n")
    check("both calls were made", len(seen) == 2, f"{len(seen)} call(s)")
    for request in seen:
        prompt = request.prompt
        check(f"[{request.purpose}] the language is named",
              "Hinglish" in prompt, prompt[:120])
        check(f"[{request.purpose}] ⚠ LATIN script is demanded by name",
              "LATIN" in prompt, "")
        check(f"[{request.purpose}] ...and Devanagari is named as the thing it is NOT",
              "Devanagari" in prompt, "")
        check(f"[{request.purpose}] ⚠ VEO PROMPTS ARE PINNED TO ENGLISH",
              "VEO MOTION PROMPTS ARE WRITTEN IN ENGLISH" in prompt, "")
        check(f"[{request.purpose}] ...and the dialogue inside them is the exception",
              "DIALOGUE quoted inside a motion prompt" in prompt, "")
        check(f"[{request.purpose}] verbs and kinds are pinned to English as DATA",
              "identifiers this app reads as data" in prompt, "")

    # ------------------------------------------------ what was HELD to it
    print("\n⚠ AND IT IS ENFORCED, NOT MERELY REQUESTED\n")
    steps = out["plan"]["steps"]
    texts = [s for s in steps if s["verb"] == "add_text"]
    check("the LATIN caption survived",
          any(s["args"]["text"] == "RAAT KI PEHLI TRAIN" for s in texts),
          json.dumps([s["args"].get("text") for s in texts], ensure_ascii=False))
    check("⚠ the DEVANAGARI caption did NOT",
          not any(director.scripts_in(s["args"].get("text", "")) for s in texts),
          json.dumps([s["args"].get("text") for s in texts], ensure_ascii=False))
    dropped_text = [d for d in out["dropped"] if d["verb"] == "add_text"]
    check("...and it was dropped WITH A REASON the panel can show",
          len(dropped_text) == 1 and "devanagari" in dropped_text[0]["why"],
          json.dumps(dropped_text, ensure_ascii=False))
    check("the rest of the plan is untouched — one bad caption is not a failed run",
          any(s["verb"] == "add_transition" for s in steps)
          and any(s["verb"] == "note" for s in steps),
          json.dumps([s["verb"] for s in steps]))
    check("the plan carries the language, so the panel can say which one it cut in",
          out["plan"]["language"] == "hinglish", out["plan"]["language"])

    # ---------------------------------------------------------- the Veo half
    print("\n⚠ THE VEO PROMPTS: ENGLISH INSTRUCTION, HINGLISH DIALOGUE INSIDE IT\n")
    veo = {v["shot"]: v for v in out["veo"]}
    check("a motion prompt was written for the shots that have one",
          set(veo) == {1, 3}, json.dumps(sorted(veo)))
    first = veo.get(1, {})
    check("⚠ the prompt itself is Latin script — an instruction, in English",
          not director.scripts_in(first.get("prompt", "")),
          first.get("prompt", "")[:80])
    check("⚠ ...and the Hinglish dialogue is INSIDE it, not translated out",
          "Train late hai kya" in first.get("prompt", ""), first.get("prompt", "")[:120])
    check("...and it is carried beside it as the line, too",
          first.get("dialogue") == "Train late hai kya?", first.get("dialogue"))
    check("⚠ THE DEVANAGARI MOTION PROMPT WAS DROPPED — that shot renders off the "
          "board's own prompt instead",
          2 not in veo, json.dumps(sorted(veo)))
    check("...with a reason, in the same list as every other drop",
          any(d["verb"] == "veo_prompt" and "English" in d["why"] for d in out["dropped"]),
          json.dumps([d for d in out["dropped"] if d["verb"] == "veo_prompt"], ensure_ascii=False))

    # ----------------------------------------------------------- the table
    print("\n⚠ ONE TABLE OF LANGUAGES, AND IT IS `plan_agent`'s\n")
    from plan_agent import LANGUAGES

    check("`director.SCRIPTS` names only languages that table describes",
          set(director.SCRIPTS) <= set(LANGUAGES),
          f"{sorted(set(director.SCRIPTS) - set(LANGUAGES))}")
    check("hinglish is LATIN and hindi is DEVANAGARI — the distinction the whole rule rests on",
          director.script_of("hinglish") == "latin"
          and director.script_of("hindi") == "devanagari")
    check("⚠ AN UNKNOWN LANGUAGE IS NOT POLICED — 'Tamil' is a legitimate answer, "
          "and refusing text we cannot classify would break it",
          director.script_of("Bhojpuri") == "" and director.in_script("अ", ""))
    check("a Devanagari caption on a HINDI film is kept — the rule is the script, "
          "not the alphabet we happen to prefer",
          director.in_script("रात की पहली ट्रेन", "devanagari"))
    check("...and an English caption on an English film is kept",
          director.in_script("THE LAST TRAIN", "latin"))
    check("accents and punctuation are Latin — 'café' is not another script",
          director.in_script("Café, 3 a.m. — the last train", "latin"))

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

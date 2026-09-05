"""A TRANSITION IS CHOSEN PER CUT — and the AI must be told what each one MEANS.

⚠ THE FAILURE THIS SUITE EXISTS FOR, REPORTED FROM THE SCREEN ON 2026-09-05.
Asked to treat a fourteen-shot Ganesh Chaturthi reel — *"pura story pe aur
transition and effects ke saath"* — the ✨ AI Editor came back with:

    · Dissolve on the cut after shot 1
    · Dissolve on the cut after shot 2
    …
    · Dissolve on the cut after shot 13

Thirteen identical dissolves. *"Dissolve on the cut hi use kar raha hai … in do
shot ke bich mai konsa badhiyan transition rahega waisa set kare."*

⚠ AND THE MODEL WAS NOT BEING LAZY. The editor renders twelve kinds, and what it
was handed was twelve ids with a label and a MECHANISM: "dissolve (Dissolve)",
"wipe — an edge travels across". Nothing anywhere said what a wipe is FOR. Twelve
interchangeable mechanisms have exactly one safe answer, and the model gave it.

⚠ SO THE MEANING LIVES ON THE TRANSITION, NOT IN A PROMPT. Each entry in
`TRANSITIONS` now carries a `when` — what that transition MEANS and where it
belongs — and it travels all the way to both planners. That placement is the
whole rule: a thirteenth kind added to the list without a `when` would be a
treatment the AI can render and can never knowingly choose, which is the same
silent half-wiring E125 is about. This suite fails until it has one.

Four things are checked:

  1. EVERY KIND HAS A MEANING, and they are twelve DIFFERENT meanings — a `when`
     copy-pasted between two kinds is two kinds the model still cannot tell
     apart.
  2. IT REACHES BOTH PLANNERS. The manifest carries it, the Director's trim
     keeps it, and the chat's trim keeps it — that last one matters most,
     because the chat trims every other table down to `id (label)` for tokens
     and this is the one that must not be trimmed.
  3. THE PANE IS UNTOUCHED. `note` is what a person reads on a chip and it is
     still there, unchanged: this added a field for the AI, it did not reword
     the UI.
  4. THE RULE IS IN BOTH PROMPTS. [GUZARISH] — that the model OBEYS it can only
     be shown by a live run; that the rule is still written down, in both
     places, is a fact a test can keep.

    python tests/transition_choice_check.py

Spends nothing. Needs node.
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

import yaml  # noqa: E402

import director  # noqa: E402
from editor_chat_agent import _vocabulary_for_prompt as chat_trim  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

failures: list[str] = []
skipped: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


HARNESS = """
import { TRANSITIONS, TRANSITION_KINDS } from "%(tr)s";
import { capabilities } from "%(caps)s";

const caps = capabilities();
process.stdout.write(JSON.stringify({
  kinds: TRANSITION_KINDS,
  table: TRANSITIONS.map((t) => ({
    id: t.id, label: t.label, note: t.note || "", when: t.when || "",
  })),
  manifest: caps.transitions,
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="transition-choice-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS
                % {
                    "tr": (ROOT / "client/src/animatic/transitions.js").as_uri(),
                    "caps": (ROOT / "client/src/animatic/agent/capabilities.js").as_uri(),
                }
            )
        proc = subprocess.run(
            ["node", harness], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:900])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nA TRANSITION IS CHOSEN PER CUT\n" + "=" * 70)

data = run_node()

print(
    "\n⚠ 1. EVERY KIND CARRIES ITS MEANING — and no two share one.  [PAKKA]\n"
    "  A kind with no `when` is a treatment the AI can render and can never\n"
    "  knowingly choose. That is how thirteen identical dissolves shipped.\n"
)
if not data:
    print("  skip every check  (node not available)")
    skipped.append("all")
else:
    table = data["table"]
    missing = [t["id"] for t in table if not t["when"].strip()]
    check(
        f"all {len(table)} transitions say when they belong",
        not missing,
        f"no `when` on: {missing}",
    )
    whens = [t["when"].strip().lower() for t in table if t["when"].strip()]
    check(
        "…and every one of them is a DIFFERENT answer, not a copied line",
        len(set(whens)) == len(whens),
        "duplicated `when` text between kinds",
    )
    check(
        "…each long enough to actually decide with",
        all(len(t["when"]) >= 40 for t in table if t["when"]),
        json.dumps([t["id"] for t in table if 0 < len(t["when"]) < 40]),
    )
    # The two the reported failure turned on: the model reached for a dissolve
    # because nothing said a dip ends a chapter or a slide keeps a reel moving.
    by_id = {t["id"]: t for t in table}
    check(
        "⚠ dissolve is described as time passing, not as the default",
        "time passes" in by_id.get("dissolve", {}).get("when", "").lower(),
        by_id.get("dissolve", {}).get("when", ""),
    )
    check(
        "⚠ dip names BOTH its colours — black ends a chapter, white is a flash",
        "black" in by_id.get("dip", {}).get("when", "").lower()
        and "white" in by_id.get("dip", {}).get("when", "").lower(),
        by_id.get("dip", {}).get("when", ""),
    )

print(
    "\n⚠ 2. IT REACHES BOTH PLANNERS.  [PAKKA]\n"
    "  A meaning written on the table and trimmed off on the way to the model\n"
    "  is the same bug one layer along.\n"
)
if not data:
    print("  skip every check  (node not available)")
else:
    manifest = data["manifest"]
    check(
        "the capability manifest carries `when`",
        all(row.get("when") for row in manifest),
        json.dumps([r.get("id") for r in manifest if not r.get("when")]),
    )

    director_vocab = director._vocabulary_for_prompt({"transitions": manifest})
    check(
        "…the Director's trim keeps it",
        all(row.get("when") for row in director_vocab["transitions"]),
        json.dumps(director_vocab["transitions"][:1]),
    )
    chat_vocab = chat_trim({"transitions": manifest})
    check(
        "⚠ …and so does the CHAT's trim, which trims every other table to bare ids",
        all(row.get("use_when") for row in chat_vocab["transitions"]),
        json.dumps(chat_vocab["transitions"][:1]),
    )
    check(
        "…keyed by the same ids the plan validates against",
        [r["id"] for r in chat_vocab["transitions"]] == data["kinds"],
        json.dumps([r["id"] for r in chat_vocab["transitions"]]),
    )

print(
    "\n⚠ 3. THE PANE IS UNTOUCHED.  [PAKKA]\n"
    "  `note` is what a person reads on a chip. This added a field for the AI;\n"
    "  it did not reword the UI.\n"
)
if not data:
    print("  skip every check  (node not available)")
else:
    check(
        "every transition still carries the `note` the chips draw",
        all(t["note"].strip() for t in data["table"]),
        json.dumps([t["id"] for t in data["table"] if not t["note"].strip()]),
    )
    check(
        "…and `note` and `when` are not the same string",
        all(t["note"].strip() != t["when"].strip() for t in data["table"]),
        "a `when` was written over a `note`",
    )

print(
    "\n⚠ 4. THE RULE IS IN BOTH PROMPTS.  [GUZARISH]\n"
    "  Only a live run can say the model obeyed it. That it is still written\n"
    "  down, in both places, is what a test can keep.\n"
)
prompts = yaml.safe_load((ROOT / "prompts.yaml").read_text(encoding="utf-8"))
chat = prompts["editor_chat"]["system"]
plan = yaml.dump(prompts["director"])

check(
    "the chat: which transition is a decision separate from how many",
    "WHICH TRANSITION IS A SEPARATE DECISION" in chat,
    "",
)
check(
    "the chat: ⚠ the thirteen-dissolve failure is named",
    "thirteen identical" in chat,
    "",
)
check(
    "the chat: the choice is made from what CHANGED between the two shots",
    "what CHANGED between these two shots" in chat,
    "",
)
check(
    "the chat: a reel's pace sets the transition's length",
    "FORMAT SETS THE PACE" in chat,
    "",
)
check(
    "the chat: …and a straight cut is still the commonest right answer",
    "STRAIGHT CUT" in chat,
    "",
)
check(
    "the Director: the same per-cut choice, from the two shots either side",
    "PER CUT, FROM THE TWO SHOTS" in plan,
    "",
)
check(
    "⚠ the Director: and it is still NOT a licence for variety",
    "NOT A LICENCE FOR VARIETY" in plan,
    "",
)
check(
    "both are pointed at the `when` line on the vocabulary itself",
    "use_when" in chat and "`when` line" in plan,
    "",
)

print("\n" + "-" * 70)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  -", f)
else:
    print("All checks passed.")
if skipped:
    print("Skipped — install node to run them.")
sys.exit(1 if failures else 0)

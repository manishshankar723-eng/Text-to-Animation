"""THE FREE PLANNER CAN RENDER — and it still invents nothing.

    python tests/director_house_veo_check.py

⚠ WHAT THIS IS ABOUT. "Just the rhythm" is the Director's Phase 0 planner: rules,
no model, no network. It writes no words on purpose (see the header of
`house_style.js`) — and because of that it wrote no MOTION PROMPTS either, so
phase C had nothing to render. The Veo tick box was live on that plan, the panel
priced the run at zero and said "Free. This plan spends nothing", and Run applied
the camera moves and rendered no footage at all. Reported as:

    "when i want i generate veo video so then i click make video buttun and then
     click just the rythem buutun and i all unchcek keep only veo check mark and
     generet so video gnereted but not come in layer"

`housePrompts` is the fix and the whole of it: the prompt for a shot is the
DESCRIPTION THAT SHOT WAS DRAWN FROM, read off the storyboard by
`GET /animatics/{id}/panels` — the same sentence the ✨ Animate dialog opens its
prompt box on. Arithmetic still chooses which shots and how long; the board says
what is in them, so Phase 0 keeps its discipline and can still buy footage.

⚠ AND A SHOT THE BOARD SAYS NOTHING ABOUT IS REFUSED BY NAME. Veo bills for a
blank prompt exactly as it bills for a good one, so a promptless shot is not
quietly rendered and not quietly dropped: it comes back in `skipped` with a
reason the panel prints under the table.

Needs node. Nothing here touches a browser, a backend or a dollar.
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

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


HARNESS = """
import { housePlan } from "__HOUSE__";
import { housePrompts, shotRow, veoDue, veoShots } from "__VEO__";
import { capabilities } from "__CAPS__";

// A board of four panels, one generated in-between shot drawn from its own
// wording, a colour card, and a Veo take already sitting on the video row.
const frames = [
  { id: "f1", duration_ms: 2000, label: "Shot 1",
    src: { kind: "panel", storyboard_id: "b1", index: 0 } },
  { id: "f2", duration_ms: 6000, label: "Shot 2",
    src: { kind: "panel", storyboard_id: "b1", index: 1 } },
  // On the board, but the breakdown wrote no description for it.
  { id: "f3", duration_ms: 2000, label: "Shot 3",
    src: { kind: "panel", storyboard_id: "b1", index: 2 } },
  // A generated in-between shot: no panel, but it carries what it was drawn from.
  { id: "f4", duration_ms: 2000, label: "After Shot 3",
    src: { kind: "upload", storyboard_id: "b1", shot_id: "g1",
           prompt: "the same kitchen, seen from the doorway" } },
  { id: "f5", duration_ms: 2000, label: "Card", src: { kind: "color" } },
  // Already footage. Not a picture to animate, and not a shot either.
  { id: "f6", duration_ms: 8000, label: "Shot 2 take", kind: "video",
    src: { kind: "video", storyboard_id: "b1", index: 1, upload_id: "u9" } },
];

// What `GET /animatics/{id}/panels` hands back. Shot 3 has a row and an empty
// description — the server blanks a wording that is only the clip's label.
const said = [
  { frame_id: "f1", description: "a hand picks up the phone from the table" },
  { frame_id: "f2", description: "the phone lights up in the dark" },
  { frame_id: "f3", description: "" },
  { frame_id: "f4", description: "" },
];

const row = shotRow(frames, frames.map((_, i) => i * 1000));
const prompts = housePrompts(row.frames, said);
const shoot = veoShots({ veo: prompts, frames: row.frames, done: [] });
const due = veoDue({ veo: true }, shoot.shots);
const off = veoDue({ veo: false }, shoot.shots);

// The same thing with no panels read at all — a project whose board has gone, or
// a build handed no `readPanels`.
const blind = veoShots({ veo: housePrompts(row.frames, []), frames: row.frames, done: [] });

// A shot already paid for is kept, never re-rendered.
const paid = veoShots({ veo: prompts, frames: row.frames, done: ["f1"] });

// And the plan the same film produces, so the two halves are read off one board.
const ctx = { frames: row.frames, starts: row.starts, texts: [], shapes: [],
              transitions: [], overlays: [], audioTracks: [], totalMs: 20000,
              caps: capabilities() };
const plan = housePlan(ctx, { include: { veo: true } });

process.stdout.write(JSON.stringify({
  prompts,
  shots: shoot.shots,
  skipped: shoot.skipped,
  due, off,
  blind: { shots: blind.shots.length, skipped: blind.skipped.length },
  paid: paid.skipped,
  planVerbs: plan.steps.map((s) => s.verb),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="director_house_veo_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__HOUSE__", (AGENT / "house_style.js").as_uri())
                .replace("__VEO__", (AGENT / "veo_pass.js").as_uri())
                .replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
            )
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 2

    prompts = data["prompts"]
    shots = data["shots"]
    skipped = data["skipped"]

    print()
    print("THE BOARD WRITES THE MOTION PROMPTS — the free planner writes none")
    print()
    check("a prompt is offered for every DRAWING on the shot row",
          [p["shot"] for p in prompts] == [1, 2, 3, 4], json.dumps(prompts))
    check("⚠ AND IT IS THE PANEL'S OWN DESCRIPTION, word for word — the same "
          "sentence ✨ Animate opens its box on",
          prompts[0]["prompt"] == "a hand picks up the phone from the table"
          and prompts[1]["prompt"] == "the phone lights up in the dark",
          json.dumps(prompts[:2]))
    check("⚠ a GENERATED in-between shot uses the wording it was drawn from — "
          "it has no panel to be read off the board",
          prompts[3]["prompt"] == "the same kitchen, seen from the doorway",
          json.dumps(prompts[3]))
    check("a COLOUR CARD is not offered at all — there is nothing to animate",
          all(p["shot"] != 5 for p in prompts), json.dumps(prompts))
    check("...and neither is a take that is already footage",
          len(prompts) == 4, json.dumps(prompts))

    print()
    print("⚠ A SHOT THE BOARD SAYS NOTHING ABOUT IS REFUSED BY NAME — Veo bills")
    print("  for a blank prompt exactly as it bills for a good one")
    print()
    check("every shot with real wording behind it is rendered — the two panels "
          "and the generated shot's own prompt",
          [s["shot"] for s in shots] == [1, 2, 4], json.dumps([s["shot"] for s in shots]))
    check("...and the wordless one is skipped, not dropped in silence",
          [s["shot"] for s in skipped] == [3], json.dumps(skipped))
    check("...with the reason the panel prints under the table",
          all("no motion prompt" in s["why"] for s in skipped), json.dumps(skipped))
    check("⚠ AND THE LENGTH IS STILL CHOSEN FROM THE HOLD, not from the prompt — "
          "the smallest take that COVERS it",
          [s["seconds"] for s in shots] == [4, 6, 4],
          json.dumps([(s["shot"], s["hold_ms"], s["seconds"]) for s in shots]))

    print()
    print("THE TICK BOX IS LIVE AGAIN — it was the whole bug")
    print()
    check("⚠ WITH VEO TICKED THE PASS IS DUE — this used to say 'there are no "
          "motion prompts to render' on every free plan",
          data["due"]["due"] is True, json.dumps(data["due"]))
    check("...and un-ticking it says so, rather than saying there is nothing",
          data["off"]["due"] is False and "switched off" in data["off"]["why"],
          json.dumps(data["off"]))
    check("⚠ NO PANELS READ AT ALL IS NOT AN ERROR — the board shots come back "
          "promptless and say so, and only the clip carrying its own wording renders",
          data["blind"]["shots"] == 1 and data["blind"]["skipped"] == 3,
          json.dumps(data["blind"]))
    check("⚠ A SHOT ALREADY PAID FOR IS KEPT, never re-rendered",
          any(s["shot"] == 1 and "paid for" in s["why"] for s in data["paid"]),
          json.dumps(data["paid"]))

    print()
    print("AND THE PLAN ITSELF IS UNCHANGED — this buys footage, it does not edit")
    print()
    check("the rules planner still writes no words: no text, no captions",
          not any(v in ("add_text", "captions", "voiceover") for v in data["planVerbs"]),
          json.dumps(data["planVerbs"]))

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

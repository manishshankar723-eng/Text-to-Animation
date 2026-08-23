"""THE FENCE — the caps hold, whoever wrote the plan.

    python tests/director_guardrails_check.py

Two things are checked here and they are not the same thing.

⚠ THE FENCE (`applyGuardrails`) is what stops a plan being applied. It runs on a
plan that has ALREADY passed `validatePlan`, so every step in front of it has
legal arguments and somewhere to land. What is left is the question validation
cannot ask, because it is about the plan as a whole rather than about any step in
it: not "is this effect real" but "is this the ninth effect on a five-shot
animatic".

The caps are not arbitrary, and the test says why each one exists next to the
assertion, because a cap with no stated reason is a number the next agent
"tunes". The short version: an auto-graded cut goes wrong by treating EVERYTHING.
Two effects on every shot is not more graded, it is a film where nothing stands
out — and the user reads that as "the AI ruined my edit", not as "the AI applied
96 effects". A treatment only reads as a treatment while most shots go without.

⚠ THE HOUSE EDITOR (`housePlan`) is the Phase 0 planner: rules, no model, no
network, no spend. It is checked for the two properties everything downstream
assumes — that it is DETERMINISTIC (same project, same plan, every time) and that
it is already house-legal before the fence sees it, because a preview listing 30
dissolves the fence then trims to 16 is a preview of a different film from the
one that gets made.

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
import { capabilities, HOUSE_CAPS } from "__CAPS__";
import { applyGuardrails, housePlan } from "__HOUSE__";
import { validatePlan, planTotals } from "__SCHEMA__";

const caps = capabilities();

/** A timeline of `n` shots, `ms` each, with `long` given three times the hold. */
function timeline(n, ms, long = []) {
  const frames = [];
  const starts = [];
  let at = 0;
  for (let i = 0; i < n; i += 1) {
    const d = long.includes(i + 1) ? ms * 3 : ms;
    frames.push({ id: `f${i + 1}`, duration_ms: d, label: `Shot ${i + 1}` });
    starts.push(at);
    at += d;
  }
  return { frames, starts, texts: [], shapes: [], transitions: [], overlays: [],
           audioTracks: [], totalMs: at, caps };
}

const fence = (steps, ctx) => {
  const checked = validatePlan({ steps }, caps, ctx);
  const fenced = applyGuardrails(checked.plan, ctx);
  return {
    kept: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
    trimmed: fenced.trimmed,
    dropped: checked.dropped,
    totals: planTotals(fenced.plan),
  };
};

// ------------------------------------------------------- ONE EFFECT PER CLIP
const ten = timeline(10, 2000);
const twoOnOne = fence([
  { verb: "add_effect", args: { shot: 1, kind: "brightness" } },
  { verb: "add_effect", args: { shot: 1, kind: "contrast" } },
], ten);

// ------------------------------------------------- EFFECTS ON ≤40% OF CLIPS
const everyShot = fence(
  Array.from({ length: 10 }, (_, i) => ({
    verb: "add_effect", args: { shot: i + 1, kind: "saturation" },
  })), ten);

// --------------------------------------------- ONE TRANSITION PER CUT, CAPPED
const everyCut = fence(
  Array.from({ length: 9 }, (_, i) => ({
    verb: "add_transition", args: { cut: i + 1, kind: "dissolve" },
  })), ten);
const sameCutTwice = fence([
  { verb: "add_transition", args: { cut: 1, kind: "dissolve" } },
  { verb: "add_transition", args: { cut: 1, kind: "wipe" } },
], ten);

// --------------------------------------------------------- SHAPES PER MINUTE
// 20s of film, so the budget is a third of SHAPES_PER_MINUTE, rounded.
const short = timeline(10, 2000);
const manyShapes = fence(
  Array.from({ length: 12 }, (_, i) => ({
    verb: "add_shape", args: { shot: (i % 10) + 1, kind: "rect", ref: `s${i}` },
  })), short);
const manyTexts = fence(
  Array.from({ length: 40 }, (_, i) => ({
    verb: "add_text", args: { shot: (i % 10) + 1, text: `line ${i}`, ref: `t${i}` },
  })), short);

// ------------------------------------------------ A TRIMMED REF TAKES ITS KIN
// The 40th caption is over the budget; the preset that styles it must go too, or
// the rail reports styling a caption that was never added.
const orphan = fence([
  ...Array.from({ length: 40 }, (_, i) => ({
    verb: "add_text", args: { shot: (i % 10) + 1, text: `line ${i}`, ref: `t${i}` },
  })),
  { verb: "apply_text_preset", args: { ref: "t39", preset: "fade" } },
  { verb: "apply_text_preset", args: { ref: "t0", preset: "rise" } },
], short);

// ------------------------------------------------------------ THE HOUSE PLAN
const varied = timeline(12, 1500, [2, 5, 9]);
const flat = timeline(12, 1500);
const empty = { frames: [], starts: [], texts: [], shapes: [], transitions: [],
                overlays: [], audioTracks: [], totalMs: 0, caps };

function planned(ctx) {
  const raw = housePlan(ctx);
  const checked = validatePlan(raw, caps, ctx);
  const fenced = applyGuardrails(checked.plan, ctx);
  return {
    raw: raw.steps.map((s) => ({ verb: s.verb, args: s.args })),
    kept: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
    dropped: checked.dropped,
    trimmed: fenced.trimmed,
    totals: planTotals(fenced.plan),
  };
}

const house = {
  varied: planned(varied),
  flat: planned(flat),
  empty: planned(empty),
  // Run twice on the same input: the plans must be identical.
  again: JSON.stringify(housePlan(varied)) === JSON.stringify(housePlan(varied)),
  offSwitch: housePlan(varied, { include: { transitions: false } }).steps
    .map((s) => s.verb),
};

process.stdout.write(JSON.stringify({
  capsTable: HOUSE_CAPS,
  twoOnOne, everyShot, everyCut, sameCutTwice, manyShapes, manyTexts, orphan,
  house,
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="director_fence_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            # ⚠ `replace`, NOT `%` — the harness is JavaScript and uses `%` as
            # the modulo operator, which printf-style formatting reads as a
            # broken conversion.
            fh.write(
                HARNESS.replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
                .replace("__HOUSE__", (AGENT / "house_style.js").as_uri())
                .replace("__SCHEMA__", (AGENT / "plan_schema.js").as_uri())
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

    table = data["capsTable"]

    print("\n⚠ ONE EFFECT PER CLIP — two on a shot is not 'more graded', it is a\n"
          "  shot the audience cannot read as treated at all\n")
    two = data["twoOnOne"]
    check("the house cap is 1 effect per clip", table["EFFECTS_PER_CLIP"] == 1,
          str(table["EFFECTS_PER_CLIP"]))
    check("a second effect on the same shot is trimmed", len(two["kept"]) == 1,
          json.dumps(two["kept"]))
    check("...and the FIRST one is the one kept",
          two["kept"] and two["kept"][0]["args"]["kind"] == "brightness",
          json.dumps(two["kept"]))
    check("...with a reason the user can read",
          two["trimmed"] and "already carries" in two["trimmed"][0]["why"],
          json.dumps(two["trimmed"]))

    print("\n⚠ EFFECTS ON AT MOST 40% OF CLIPS — a treatment only reads as one\n"
          "  while most shots go without\n")
    every = data["everyShot"]
    check("the house share is 40%", abs(table["EFFECT_CLIP_SHARE"] - 0.4) < 1e-9,
          str(table["EFFECT_CLIP_SHARE"]))
    check("10 effects on 10 shots come back as 4", len(every["kept"]) == 4,
          f"{len(every['kept'])} kept")
    check("...and 6 are trimmed, each with a reason",
          len(every["trimmed"]) == 6 and all(t["why"] for t in every["trimmed"]),
          json.dumps(every["trimmed"][:2]))
    check("...on the SHOTS the plan asked for first",
          [s["args"]["shot"] for s in every["kept"]] == [1, 2, 3, 4],
          json.dumps([s["args"]["shot"] for s in every["kept"]]))

    print("\n⚠ ONE TRANSITION PER CUT, AND AT MOST 35% OF THE CUTS — a cut is the\n"
          "  default; a film where every edit dissolves has no edits\n")
    cuts = data["everyCut"]
    check("9 transitions on 9 cuts come back as 3", len(cuts["kept"]) == 3,
          f"{len(cuts['kept'])} kept")
    check("...all on different cuts",
          len({s["args"]["cut"] for s in cuts["kept"]}) == len(cuts["kept"]),
          json.dumps([s["args"]["cut"] for s in cuts["kept"]]))
    twice = data["sameCutTwice"]
    check("⚠ two transitions on ONE cut is trimmed to one", len(twice["kept"]) == 1,
          json.dumps(twice["kept"]))
    check("...because two would make the render depend on list order",
          twice["trimmed"] and "already a transition" in twice["trimmed"][0]["why"],
          json.dumps(twice["trimmed"]))

    print("\n⚠ SHAPES AND TEXT ARE CAPPED PER MINUTE — an arrow that points at\n"
          "  something is a device; six on screen at once is clip art\n")
    shapes = data["manyShapes"]
    # 20s of film = 1/3 minute. round(0.333 * 4) = 1.
    check("the house allows 4 shapes a minute", table["SHAPES_PER_MINUTE"] == 4)
    check("12 shapes on a 20-second film come back as 1", len(shapes["kept"]) == 1,
          f"{len(shapes['kept'])} kept")
    texts = data["manyTexts"]
    check("the house allows 8 text clips a minute", table["TEXTS_PER_MINUTE"] == 8)
    # round(0.333 * 8) = 3.
    check("40 captions on a 20-second film come back as 3", len(texts["kept"]) == 3,
          f"{len(texts['kept'])} kept")

    print("\n⚠ A STEP THAT STYLES A TRIMMED CLIP GOES WITH IT\n")
    orphan = data["orphan"]
    kept_verbs = [s["verb"] for s in orphan["kept"]]
    check("the preset on the caption that survived is kept",
          kept_verbs.count("apply_text_preset") == 1, json.dumps(kept_verbs))
    check("...and the one on the caption that was trimmed is gone",
          any("t39" in t["why"] for t in orphan["trimmed"]),
          json.dumps([t["why"] for t in orphan["trimmed"]][-3:]))

    print("\nTHE HOUSE EDITOR — rules only, and already legal before the fence\n")
    house = data["house"]
    check("⚠ the same project produces the same plan, every time", house["again"])
    varied = house["varied"]
    check("a timeline with three held shots gets transitions",
          varied["totals"]["transitions"] >= 1, json.dumps(varied["totals"]))
    check("...and camera moves on the shots that hold long enough",
          varied["totals"]["moves"] >= 1, json.dumps(varied["totals"]))
    check("⚠ NOTHING IT PROPOSED WAS DROPPED BY VALIDATION",
          not varied["dropped"], json.dumps(varied["dropped"]))
    check("⚠ NOR TRIMMED BY THE FENCE — the preview IS the film that gets made",
          not varied["trimmed"], json.dumps(varied["trimmed"]))
    check("it writes no text and no shapes — no rule produces the right words",
          varied["totals"]["texts"] == 0 and varied["totals"]["shapes"] == 0,
          json.dumps(varied["totals"]))
    check("it spends nothing: no veo, no voiceover, no captions in the plan",
          all(s["verb"] not in ("generate_video", "voiceover", "captions")
              for s in varied["kept"]))

    flat = house["flat"]
    check("⚠ a FLAT timeline gets no transitions — there is no rhythm to read",
          flat["totals"]["transitions"] == 0, json.dumps(flat["totals"]))
    check("...and says so rather than sitting there empty",
          any(s["verb"] == "note" for s in flat["kept"]),
          json.dumps([s["verb"] for s in flat["kept"]]))

    empty = house["empty"]
    check("an empty timeline is one note, not a crash",
          [s["verb"] for s in empty["kept"]] == ["note"],
          json.dumps(empty["kept"]))

    check("un-ticking Transitions leaves the plan with none of them",
          "add_transition" not in house["offSwitch"], json.dumps(house["offSwitch"]))
    check("...and the camera moves are untouched by that box",
          "push_in" in house["offSwitch"], json.dumps(house["offSwitch"]))

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

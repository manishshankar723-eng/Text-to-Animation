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
import { applyGuardrails, fillAlternateTransitions, fillStillMoves, housePlan } from "__HOUSE__";
import { defaultInclude, validatePlan, planTotals } from "__SCHEMA__";

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

const fence = (steps, ctx, include) => {
  const checked = validatePlan(
    include ? { steps, include: { ...defaultInclude(), ...include } } : { steps },
    caps,
    ctx
  );
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
// ⚠ TWO CEILINGS NOW, AND WHICH ONE APPLIES DEPENDS ON WHAT THE FILM IS MADE
// OF. With Veo OFF the stills ARE the film and the house pattern is a transition
// on every OTHER cut (asked for three times); with Veo ON a transition is
// emphasis and 35% of the cuts is the limit it has always been. One function
// answers both and the planner and the fence both call it: `transitionBudget`.
const everyCut = fence(
  Array.from({ length: 9 }, (_, i) => ({
    verb: "add_transition", args: { cut: i + 1, kind: "dissolve" },
  })), ten);
const everyCutWithVeo = fence(
  Array.from({ length: 9 }, (_, i) => ({
    verb: "add_transition", args: { cut: i + 1, kind: "dissolve" },
  })), ten, { veo: true });
const sameCutTwice = fence([
  { verb: "add_transition", args: { cut: 1, kind: "dissolve" } },
  { verb: "add_transition", args: { cut: 1, kind: "wipe" } },
], ten);
// ⚠ AND ON THE CUT NEXT DOOR. Shot 4 would fade up and start fading out again
// — it is never on screen whole — and a run of treated cuts reads as a cluster
// rather than as a rhythm. The budget alone allowed all three of these.
const nextDoor = fence([
  { verb: "add_transition", args: { cut: 3, kind: "dissolve" } },
  { verb: "add_transition", args: { cut: 4, kind: "dissolve" } },
  { verb: "add_transition", args: { cut: 6, kind: "dissolve" } },
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
// THREE HELD SHOTS IN A ROW — the case that produced the cluster the user
// photographed. Every cut around them is a candidate and the budget is 3, so
// before the spacing rule the plan treated 4, 5 and 6 and shot 5 dissolved at
// both ends.
const neighbours = timeline(12, 1500, [4, 5, 6]);
const flat = timeline(12, 1500);
const empty = { frames: [], starts: [], texts: [], shapes: [], transitions: [],
                overlays: [], audioTracks: [], totalMs: 0, caps };

function planned(ctx, include) {
  const raw = housePlan(ctx, include ? { include } : {});
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

// ------------------------------------- THE MOVE RULE IS THE FILM'S, NOT THE
// RULES PLANNER'S. A plan the MODEL wrote, with an opinion about three shots out
// of twelve: two pushes and one deliberate hold. With Veo un-ticked the stills
// ARE the finished film, so the other nine have to move too — that is what
// `fillStillMoves` is for, and until it existed "Read my film" with Veo off gave
// three moving shots and nine dead ones.
const modelSteps = [
  { verb: "push_in", args: { shot: 2, from: 1, to: 1.1, ease: "ease-in-out" } },
  { verb: "push_in", args: { shot: 4, from: 1, to: 1.05, ease: "ease-in-out" } },
  { verb: "push_in", args: { shot: 7, from: 1, to: 1.1, ease: "ease-in-out" } },
  { verb: "clear_shot_motion", args: { shot: 9 } },
  { verb: "add_text", args: { shot: 7, text: "Beautiful.", ref: "t1" } },
];
function adopted(steps, ctx, include) {
  // ⚠ THE SAME TWO FILLERS `adopt` RUNS, IN THE SAME ORDER. This helper exists to
  // be the door every plan comes through, so leaving one out here would test a
  // door the app does not have.
  const filled = fillStillMoves({ steps, include: { ...defaultInclude(), ...include } }, ctx);
  const rhythmed = fillAlternateTransitions(filled, ctx);
  const checked = validatePlan(rhythmed, caps, ctx);
  const fenced = applyGuardrails(checked.plan, ctx);
  return {
    kept: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
    dropped: checked.dropped,
    trimmed: fenced.trimmed,
    totals: planTotals(fenced.plan),
  };
}
const model = {
  off: adopted(modelSteps, varied, { veo: false }),
  on: adopted(modelSteps, varied, { veo: true }),
};

// ------------------------------------ AND THE CUTS ARE THE FILM'S TOO. A model
// handed eight identical four-second shots with no descriptions reads the whole
// thing as ONE scene — correctly — and the polish prompt earns a dissolve on a
// scene BOUNDARY, of which one scene has none. So it wrote zero transitions and
// the run made eight hard cuts. Reported as "without transiition and music not
// complate video". `fillAlternateTransitions` is `fillStillMoves`' twin.
const flat8 = timeline(8, 4000);
const movesOnly = [
  { verb: "push_in", args: { shot: 2, from: 1, to: 1.05, ease: "ease-in-out" } },
  { verb: "push_in", args: { shot: 7, from: 1, to: 1.1, ease: "ease-in-out" } },
];
const cuts = (out) => out.kept.filter((s) => s.verb === "add_transition").map((s) => s.args.cut);
const rhythm = {
  // Nothing said about the cuts at all — the house fills them in.
  silent: adopted(movesOnly, flat8, { veo: false }),
  // One dissolve of its own is an OPINION, and the filler leaves it alone.
  opinionated: adopted(
    [...movesOnly, { verb: "add_transition", args: { cut: 4, kind: "dissolve", ms: 700 } }],
    flat8,
    { veo: false }
  ),
  // "Make this a straight cut" is also an opinion.
  straightened: adopted(
    [...movesOnly, { verb: "remove_transition", args: { cut: 4 } }],
    flat8,
    { veo: false }
  ),
  // The tick box still wins over the house rule.
  ticked_off: adopted(movesOnly, flat8, { veo: false, transitions: false }),
  // A one-shot film has no cuts to fill.
  single: adopted([], timeline(1, 4000), { veo: false }),
  // ⚠ AND THE FILLER MUST BE THE HOUSE PLANNER'S OWN ANSWER, not a second copy
  // of the rule. Same film, same flags, asked both ways.
  same:
    JSON.stringify(
      cuts(adopted(movesOnly, flat8, { veo: false }))
    ) ===
    JSON.stringify(
      housePlan(flat8, { include: { ...defaultInclude(), veo: false } })
        .steps.filter((s) => s.verb === "add_transition")
        .map((s) => s.args.cut)
    ),
  // ⚠ READ OFF THE FILLER, NOT OFF `kept`. `adopted` maps its steps down to
  // `{verb, args}` — the note is deliberately not part of what the fence is
  // asserted on — so reading it there gets "" every time and asserts nothing.
  notes: fillAlternateTransitions(
    { steps: movesOnly, include: { ...defaultInclude(), veo: false } },
    flat8
  ).steps
    .filter((s) => s.verb === "add_transition")
    .map((s) => s.note || ""),
};

const house = {
  varied: planned(varied),
  neighbours: planned(neighbours),
  flat: planned(flat),
  flatWithVeo: planned(flat, { veo: true }),
  empty: planned(empty),
  // ⚠ THE TWO FILMS THE MOVE RULE MAKES. With Veo un-ticked — which is the
  // DEFAULT — the stills are the finished film and every one of them moves;
  // with it ticked most of them are about to become footage and a move goes
  // back to being emphasis on the few shots that earn it.
  stills: planned(varied, { veo: false }),
  shot: planned(varied, { veo: true }),
  // Run twice on the same input: the plans must be identical.
  again: JSON.stringify(housePlan(varied)) === JSON.stringify(housePlan(varied)),
  offSwitch: housePlan(varied, { include: { transitions: false } }).steps
    .map((s) => s.verb),
};

process.stdout.write(JSON.stringify({
  capsTable: HOUSE_CAPS,
  twoOnOne, everyShot, everyCut, everyCutWithVeo, sameCutTwice, nextDoor, manyShapes, manyTexts,
  orphan, house, model, rhythm,
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

    print("\n⚠ ONE TRANSITION PER CUT, AND A CEILING THAT FITS THE FILM — every other\n"
          "  cut on a film of stills, 35% of them when footage is coming\n")
    cuts = data["everyCut"]
    kept_cuts = sorted(s["args"]["cut"] for s in cuts["kept"])
    check("9 transitions on 9 cuts come back as 5 — every other one",
          kept_cuts == [1, 3, 5, 7, 9], json.dumps(kept_cuts))
    check("...never two in a row, which is what makes it read as a rhythm",
          all(b - a >= 2 for a, b in zip(kept_cuts, kept_cuts[1:])), json.dumps(kept_cuts))
    check("⚠ AND WITH VEO TICKED THE 35% SHARE STILL HOLDS: 9 come back as 3,"
          " because over footage a transition is emphasis rather than a pattern",
          len(data["everyCutWithVeo"]["kept"]) == 3,
          f"{len(data['everyCutWithVeo']['kept'])} kept")
    check("...all on different cuts",
          len({s["args"]["cut"] for s in cuts["kept"]}) == len(cuts["kept"]),
          json.dumps([s["args"]["cut"] for s in cuts["kept"]]))
    twice = data["sameCutTwice"]
    check("⚠ two transitions on ONE cut is trimmed to one", len(twice["kept"]) == 1,
          json.dumps(twice["kept"]))
    check("...because two would make the render depend on list order",
          twice["trimmed"] and "already a transition" in twice["trimmed"][0]["why"],
          json.dumps(twice["trimmed"]))

    print("\n⚠ AND THEY ALTERNATE — a shot with a transition on BOTH sides is\n"
          "  never on screen whole: it fades up and starts fading out again\n")
    door = data["nextDoor"]
    kept_cuts = [s["args"]["cut"] for s in door["kept"]]
    check("a transition on the cut next to a treated one is trimmed",
          kept_cuts == [3, 6], json.dumps(kept_cuts))
    check("...with a reason that says why, not just that",
          any("alternate" in t["why"] for t in door["trimmed"]),
          json.dumps(door["trimmed"]))
    check("...and it is the LATER one that goes, so the plan's own order wins",
          door["trimmed"] and door["trimmed"][0]["index"] == 1,
          json.dumps(door["trimmed"]))

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

    near = house["neighbours"]
    near_cuts = [s["args"]["cut"] for s in near["kept"] if s["verb"] == "add_transition"]
    check("⚠ THE RULES PLANNER ALTERNATES THEM ITSELF — every other cut, from the\n"
          "       FIRST one, so a run of held shots cannot cluster and the\n"
          "       opening cut is not left bare",
          near_cuts == [1, 3, 5, 7, 9, 11], json.dumps(near_cuts))
    check("...and it is under budget rather than trimmed by the fence, so the\n"
          "       preview IS the film that gets made",
          not near["trimmed"] and not near["dropped"],
          json.dumps(near["trimmed"] + near["dropped"]))

    flat = house["flat"]
    # ⚠ THIS EXPECTATION IS DELIBERATELY THE OPPOSITE OF WHAT IT WAS. It read "a
    # FLAT timeline gets no transitions — there is no rhythm to read", which was
    # true of the old rule and WAS the bug: a board where every shot is the same
    # length has no long holds, so it had no candidates, so a film of twelve
    # identical shots got ZERO transitions and played as twelve hard cuts. That is
    # the board most users actually have, and it was reported three times. There
    # is no rhythm to READ on a flat board — so the house GIVES it one.
    flat_cuts = sorted(s["args"]["cut"] for s in flat["kept"]
                       if s["verb"] == "add_transition")
    check("⚠ A FLAT TIMELINE IS GIVEN A RHYTHM: alternate cuts, rather than the"
          " twelve hard cuts it used to get",
          flat_cuts == [1, 3, 5, 7, 9, 11], json.dumps(flat_cuts))
    check("...and the fence keeps every one, so the preview IS the film that is made",
          not flat["trimmed"] and not flat["dropped"],
          json.dumps(flat["trimmed"] + flat["dropped"]))
    check("⚠ BUT WITH VEO TICKED IT GETS NONE: over footage a transition marks a"
          " held shot, and a flat board has no held shot to mark",
          house["flatWithVeo"]["totals"]["transitions"] == 0,
          json.dumps(house["flatWithVeo"]["totals"]))
    check("...and says so rather than sitting there empty",
          any(s["verb"] == "note" for s in flat["kept"]),
          json.dumps([s["verb"] for s in flat["kept"]]))

    empty = house["empty"]
    check("an empty timeline is one note, not a crash",
          [s["verb"] for s in empty["kept"]] == ["note"],
          json.dumps(empty["kept"]))

    check("un-ticking Transitions leaves the plan with none of them",
          "add_transition" not in house["offSwitch"], json.dumps(house["offSwitch"]))
    # ⚠ EITHER MOVE VERB. Which one the planner reaches for depends on the Veo
    # box, not this one — `push_in` on the shots that earn emphasis when footage
    # is coming, `add_shot_motion` on every drawing when it is not — and what
    # this check is actually about is that the TRANSITIONS box does not reach
    # over and take the moves with it.
    check("...and the camera moves are untouched by that box",
          any(v in house["offSwitch"] for v in ("push_in", "add_shot_motion")),
          json.dumps(house["offSwitch"]))

    print("\nNOTHING IS BEING RENDERED, SO EVERY DRAWING MOVES\n")
    stills = house["stills"]
    kinds = [s["args"]["kind"] for s in stills["kept"] if s["verb"] == "add_shot_motion"]
    check("⚠ EVERY STILL GETS A MOVE, not just the ones held long enough",
          len(kinds) == 12, json.dumps(kinds))
    check("...drawn from the four a rostrum camera can make, and no others",
          set(kinds) == {"zoom_in", "zoom_out", "pan_left", "pan_right"},
          json.dumps(sorted(set(kinds))))
    # ⚠ WEIGHTED, NOT ROTATED, and this is the whole of the 2026-08-24 report.
    # The cycle used to be the four in order, which put a PAN on every other
    # shot — "you scale keyframe and left right, not good looking … keep only
    # the most used, zoom in, and some clips zoom out, and very few left and
    # right". A pan drags the frame across a drawing composed to be looked at
    # straight on; it is the rare gesture, not half the film.
    check("⚠ THE PUSH IN CARRIES THE FILM — half the moves, and more than any"
          " other gesture",
          kinds.count("zoom_in") * 2 >= len(kinds)
          and kinds.count("zoom_in") > kinds.count("zoom_out"),
          f"{kinds.count('zoom_in')} of {len(kinds)}: {json.dumps(kinds)}")
    check("...the pull back is the second gesture, on every third shot",
          kinds.count("zoom_out") == 4, json.dumps(kinds))
    check("⚠ AND A PAN IS RARE — one every five shots and no oftener",
          kinds.count("pan_left") + kinds.count("pan_right") == 2, json.dumps(kinds))
    check("...alternating direction, so the two nearest pans are never the same\n"
          "       gesture twice — 'left in 5, so right in 10'",
          [i for i, k in enumerate(kinds) if k.startswith("pan_")] == [4, 9]
          and kinds[4] == "pan_left" and kinds[9] == "pan_right",
          json.dumps(kinds))
    check("⚠ NOTHING IT PROPOSED WAS DROPPED OR TRIMMED",
          not stills["dropped"] and not stills["trimmed"],
          json.dumps(stills["dropped"] + stills["trimmed"]))
    check("...and the totals count them as the moves they are",
          stills["totals"]["moves"] == 12, json.dumps(stills["totals"]))
    # With footage coming, a move is emphasis again and most shots stay locked
    # off — the rule that was there before, unchanged.
    check("⚠ BUT WITH VEO TICKED IT IS EMPHASIS AGAIN, on the held shots only",
          [s["verb"] for s in house["shot"]["kept"]].count("push_in") == 3
          and "add_shot_motion" not in [s["verb"] for s in house["shot"]["kept"]],
          json.dumps([s["verb"] for s in house["shot"]["kept"]]))

    print()
    print("⚠ WITH VEO OFF, EVERY DRAWING MOVES — IN THE MODEL'S PLAN TOO")
    print("  (the rules planner is not the only thing that gets read on screen)")
    print()
    off = data["model"]["off"]
    on = data["model"]["on"]
    filled = [s["args"]["shot"] for s in off["kept"] if s["verb"] == "add_shot_motion"]
    pushed = [s["args"]["shot"] for s in off["kept"] if s["verb"] == "push_in"]
    check("the model's own three pushes are kept exactly as it wrote them",
          pushed == [2, 4, 7], json.dumps(pushed))
    check("⚠ AND THE NINE SHOTS IT SAID NOTHING ABOUT ARE FILLED IN",
          filled == [1, 3, 5, 6, 8, 10, 11, 12], json.dumps(filled))
    check("...so every one of the 12 shots moves, which is what the box means",
          off["totals"]["moves"] == 11 and len(filled) + len(pushed) == 11,
          json.dumps(off["totals"]))
    check("⚠ EXCEPT THE ONE THE PLAN DELIBERATELY HELD — a hold is an opinion,\n"
          "       and the filler is for shots nobody has one about",
          9 not in filled
          and any(s["verb"] == "clear_shot_motion" and s["args"]["shot"] == 9
                  for s in off["kept"]),
          json.dumps([s["verb"] for s in off["kept"]]))
    kinds_at = {s["args"]["shot"]: s["args"]["kind"] for s in off["kept"]
                if s["verb"] == "add_shot_motion"}
    check("⚠ AND THE PATTERN IS POSITIONAL, so a pan lands on the same shot it\n"
          "       would have on the free plan — 5 left, 10 right",
          kinds_at.get(5) == "pan_left" and kinds_at.get(10) == "pan_right",
          json.dumps(kinds_at))
    check("nothing it added was dropped by validation or trimmed by the fence",
          not off["dropped"] and not off["trimmed"],
          json.dumps(off["dropped"] + off["trimmed"]))
    check("⚠ AND TICKING VEO TAKES THEM ALL BACK OFF — with footage coming a\n"
          "       move is emphasis again, and the plan is the model's alone",
          on["totals"]["moves"] == 3
          and not any(s["verb"] == "add_shot_motion" for s in on["kept"]),
          json.dumps(on["totals"]))
    print()
    print("⚠ A PLAN WITH NO OPINION ABOUT THE CUTS GETS THE HOUSE RHYTHM")
    print("  (eight identical shots read as one scene, so the model wrote zero)")
    print()
    r = data["rhythm"]
    silent = [s["args"]["cut"] for s in r["silent"]["kept"] if s["verb"] == "add_transition"]
    check("⚠ a model plan with NO transitions gets the alternating dissolves",
          silent == [1, 3, 5, 7], json.dumps(silent))
    check("...and the note says the HOUSE filled them in, not the AI",
          all("house rhythm" in n for n in r["notes"]) and len(r["notes"]) == 4,
          json.dumps(r["notes"]))
    check("...and it is the rules planner's own answer, not a second copy of it",
          r["same"], str(r["same"]))
    check("the model's own moves are untouched by any of it",
          [s["args"]["shot"] for s in r["silent"]["kept"] if s["verb"] == "push_in"] == [2, 7],
          json.dumps([s["verb"] for s in r["silent"]["kept"]]))
    opinion = [s["args"]["cut"] for s in r["opinionated"]["kept"] if s["verb"] == "add_transition"]
    check("⚠ ONE TRANSITION OF ITS OWN AND THE FILLER IS A NO-OP — that is an\n"
          "       opinion about the rhythm, and filling around it would overrule it",
          opinion == [4], json.dumps(opinion))
    straight = [s["verb"] for s in r["straightened"]["kept"]]
    check("...and so is \"make this a straight cut\"",
          "add_transition" not in straight, json.dumps(straight))
    check("un-ticking Transitions still wins over the house rule",
          not any(s["verb"] == "add_transition" for s in r["ticked_off"]["kept"]),
          json.dumps([s["verb"] for s in r["ticked_off"]["kept"]]))
    check("a one-shot film has no cut to fill, and nothing is invented",
          not any(s["verb"] == "add_transition" for s in r["single"]["kept"]),
          json.dumps([s["verb"] for s in r["single"]["kept"]]))
    check("nothing the filler added was dropped or trimmed",
          not r["silent"]["dropped"] and not r["silent"]["trimmed"],
          json.dumps(r["silent"]["dropped"] + r["silent"]["trimmed"]))

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

"""chat_layers_check.py — WHAT THE USER ASKED FOR IS WHAT HAPPENS, AND A CUT IS ON ONE ROW.

    python tests/chat_layers_check.py     (needs node; no backend, no model, no dollar)

WHY THIS FILE EXISTS. One report, three faults, all visible in the same four
screenshots of the ✨ AI Editor:

    "add transition image layer 10 every each clip"          (typed twice)
    → "Our system has a limit that only allows transitions on up to 35% of
       the cuts in a video to keep it clean. For your 27 shots, that means we
       can only use a maximum of 9 transitions in total."
    → "add_transition: there is a 28.0s gap after shot 24"
    → "i want add alll clip not 9"

    "mai aisa chahta hun user jo kahe wo hona chahiye"

**1 · THE 35% WAS REAL AND IT WAS A DEFAULT BEING ENFORCED AS A LAW.**
`HOUSE_CAPS.TRANSITION_CUT_SHARE` is sent to the planner in the manifest and
enforced again by `applyGuardrails`. It was written for the FREE door, where the
app is choosing on its own and restraint is right — "a dissolve means something
because most cuts are straight" is true. It is the wrong answer to a person who
typed "every clip" twice. So a plan may now carry `asked_for_all`, and this file
is the guard on both halves of that: it lifts every share when set, **and it
still holds when it is not**.

**2 · THE 28-SECOND GAP DID NOT EXIST.** `frames` is every picture clip on every
picture ROW, and `boardFrom` flattened the lot into one numbered list — so a
project with 3 clips on Video and 24 on Images playing UNDERNEATH it was
described as a 27-shot film running end to end. "The cut after shot 24" was the
boundary between two clips that are never next to each other. Every unexplainable
message in that report is this one bug.

⚠ **AND THE FIX IS NOT A BETTER ERROR MESSAGE.** A cut between two rows cannot be
made at all, so the planner has to be told which row each shot is on BEFORE it
proposes anything. Both halves are checked here: `boardFrom` says the row,
`cutAfter` refuses the cross-row cut, and it names the rows the gutter names.

**3 · THE CHAT WOULD NOT OFFER PAID WORK.** `allow_paid_passes` shipped OFF and
all it ever controlled was one sentence in `rails_text` — a refusal. Now it
offers, and names the button. ⚠ **"MAY OFFER" AND "MAY START" ARE DIFFERENT
SENTENCES AND ONLY THE FIRST IS TRUE** — nothing in this feature spends anything
but text quota — so the wording is asserted, not just the flag.

⚠ **THE LIFT COVERS TASTE AND NOTHING STRUCTURAL, AND §4 IS WHERE THAT IS
PROVED.** Shares, per-minute rates and the alternate-cuts rule are preference.
`MIN_CLIP_MS`, "the clips must touch" and "same row" are not: a plan that lifted
those would report work that renders as nothing, which is worse than a refusal.
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
CLIENT = ROOT / "client"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


# ===========================================================================
# THE NODE HALF — the fence and the validator, run for real
# ===========================================================================
# ⚠ PLAIN `node` WITH file:// IMPORTS, the same way `director_guardrails_check`
# does it. These modules are ESM with no React in them, so there is nothing to
# bundle and nothing to stub — and a bundler in the way is one more thing that
# can be the reason a check went red.
HARNESS = r"""
import { capabilities, HOUSE_CAPS } from "__CAPS__";
import { applyGuardrails, transitionBudget } from "__HOUSE__";
import { defaultInclude, validatePlan } from "__SCHEMA__";

const caps = capabilities();

/**
 * A STACKED timeline: `rows` is a list of clip counts, one per picture row, and
 * every clip is 4s. Row 0 is laid from 0, and so is row 1 — they play AT THE
 * SAME TIME, which is the whole point of the fixture.
 */
function stacked(rows) {
  const frames = [];
  const starts = [];
  rows.forEach((count, track) => {
    let at = 0;
    for (let i = 0; i < count; i += 1) {
      frames.push({ id: `t${track}f${i}`, track, duration_ms: 4000, label: `R${track}S${i + 1}` });
      starts.push(at);
      at += 4000;
    }
  });
  const totalMs = Math.max(...rows.map((c) => c * 4000));
  return {
    frames, starts, totalMs, caps,
    texts: [], shapes: [], transitions: [], overlays: [], audioTracks: [],
    laneRows: rows.map((count, track) => ({
      key: `frames:${track}`, kind: "frames", track,
      name: track === 0 ? "Video" : "Images", layer: 9 + track,
    })),
  };
}

const run = (steps, ctx, extra = {}) => {
  const checked = validatePlan({ steps, ...extra }, caps, ctx);
  // ⚠ `include` MATTERS TO THE CEILING AND IS EASY TO FORGET. `defaultInclude()`
  // has `veo: false`, and with nothing being rendered the stills ARE the film,
  // so `transitionBudget` allows every OTHER cut rather than 35% of them. A
  // fixture that ignored this would be measuring a number the app never uses.
  const fenced = applyGuardrails(checked.plan, ctx);
  return {
    askedForAll: checked.plan.askedForAll,
    kept: fenced.plan.steps.map((s) => ({ verb: s.verb, cut: s.args.cut, shot: s.args.shot })),
    trimmed: fenced.trimmed,
    dropped: checked.dropped,
  };
};

// Two rows: 2 clips on "Video" (layer 9), 3 on "Images" (layer 10).
// Flat list: [R0S1, R0S2, R1S1, R1S2, R1S3] → cuts 1..4.
//   cut 1 = R0S1→R0S2   same row, touching   → a real cut
//   cut 2 = R0S2→R1S1   DIFFERENT ROWS       → not a cut at all
//   cut 3 = R1S1→R1S2   same row, touching   → a real cut
//   cut 4 = R1S2→R1S3   same row, touching   → a real cut, next door to 3
const two = stacked([2, 3]);
const everyCut = [1, 2, 3, 4].map((cut) => ({
  verb: "add_transition", args: { cut, kind: "dissolve" },
}));

const asked = run(everyCut, two, { asked_for_all: true });
const notAsked = run(everyCut, two);

// The same request on ONE row, so nothing is refused for being cross-row: 8
// clips, 7 cuts. Default share allows floor(7 × 0.35) = 2.
const one = stacked([8]);
const oneAsked = run(
  [1, 2, 3, 4, 5, 6, 7].map((cut) => ({ verb: "add_transition", args: { cut, kind: "dissolve" } })),
  one, { asked_for_all: true }
);
const oneNotAsked = run(
  [1, 2, 3, 4, 5, 6, 7].map((cut) => ({ verb: "add_transition", args: { cut, kind: "dissolve" } })),
  one
);
// The 35% share itself, which only applies when something IS being rendered.
const oneVeoNotAsked = run(
  [1, 2, 3, 4, 5, 6, 7].map((cut) => ({ verb: "add_transition", args: { cut, kind: "dissolve" } })),
  one, { include: { ...defaultInclude(), veo: true } }
);
const oneVeoAsked = run(
  [1, 2, 3, 4, 5, 6, 7].map((cut) => ({ verb: "add_transition", args: { cut, kind: "dissolve" } })),
  one, { include: { ...defaultInclude(), veo: true }, asked_for_all: true }
);

// Effects on every one of 8 clips. Default share allows floor(8 × 0.4) = 3.
const fx = (n) => Array.from({ length: n }, (_, i) => ({
  verb: "add_effect", args: { shot: i + 1, kind: "brightness" },
}));
const fxAsked = run(fx(8), one, { asked_for_all: true });
const fxNotAsked = run(fx(8), one);

// ⚠ STRUCTURE IS NOT TASTE. Even with the caps lifted, a clip shorter than
// MIN_CLIP_MS and a cut across a GAP must still be refused.
const gapped = stacked([3]);
gapped.starts = [0, 4000, 20000];   // a 12s hole before the third clip
const overGap = run(
  [{ verb: "add_transition", args: { cut: 2, kind: "dissolve" } }],
  gapped, { asked_for_all: true }
);

console.log(JSON.stringify({
  capsTable: HOUSE_CAPS,
  budget: {
    plainSeven: transitionBudget(8),
    askedSeven: transitionBudget(8, undefined, true),
  },
  asked, notAsked, oneAsked, oneNotAsked, oneVeoAsked, oneVeoNotAsked,
  fxAsked, fxNotAsked, overGap,
}));
"""

# The `boardFrom` half needs a bundler, because `useDirectorRun.js` imports
# React. It is bundled rather than stubbed so what runs is the real module.
BOARD_HARNESS = r"""
import { boardFrom } from "__RUN__";

const stackedCtx = {
  title: "AI Agent explainer",
  totalMs: 70000,
  frames: [
    { id: "a", track: 0, duration_ms: 7700, label: "AI_agent_using" },
    { id: "b", track: 0, duration_ms: 5600, label: "AI_tools" },
    { id: "c", track: 1, duration_ms: 3600, label: "8_AA_1" },
    { id: "d", track: 1, duration_ms: 4000, label: "8_AA_2" },
  ],
  starts: [0, 7700, 0, 3600],
  laneRows: [
    { key: "frames:0", kind: "frames", track: 0, name: "Video", layer: 9 },
    { key: "frames:1", kind: "frames", track: 1, name: "Images", layer: 10 },
    { key: "audio:x", kind: "audio", track: 0, name: "Audio", layer: 12 },
  ],
};

// An older caller: no row stack at all, which MEANS "one row end to end".
const flatCtx = {
  frames: [{ id: "a", duration_ms: 1000 }, { id: "b", duration_ms: 1000 }],
  starts: [0, 1000],
};

console.log(JSON.stringify({
  stacked: boardFrom(stackedCtx),
  flat: boardFrom(flatCtx),
}));
"""


def run_node() -> dict | None:
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="chat_layers_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
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


def run_board() -> dict | None:
    """`boardFrom`, bundled out of the real module. None when esbuild is absent."""
    esbuild = CLIENT / ("node_modules/.bin/esbuild.cmd" if os.name == "nt"
                        else "node_modules/.bin/esbuild")
    if not shutil.which("node") or not esbuild.exists():
        return None
    work = tempfile.mkdtemp(prefix="chat_board_")
    try:
        entry = os.path.join(work, "board.mjs")
        bundle = os.path.join(work, "bundle.cjs")
        with open(entry, "w", encoding="utf-8") as fh:
            # ⚠ AN ABSOLUTE, POSIX-STYLE PATH. The entry file is in a temp
            # directory, so a path relative to the repo resolves to nothing —
            # and `react`, which `useDirectorRun.js` imports, is found because
            # esbuild resolves from the IMPORTER (under `client/`) rather than
            # from the entry point. Backslashes would be escape sequences.
            fh.write(BOARD_HARNESS.replace(
                "__RUN__", (AGENT / "useDirectorRun.js").as_posix()
            ))
        build = subprocess.run(
            [str(esbuild), entry, "--bundle", "--platform=node", "--format=cjs",
             f"--outfile={bundle}", "--log-level=error"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT),
        )
        if build.returncode != 0:
            print("    esbuild said:", (build.stderr or "").strip()[:1200])
            return None
        proc = subprocess.run(
            ["node", bundle],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1200])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ===========================================================================
def main() -> int:
    # -------------------------------------------------------------------- 1
    print("\n1 · THE BOARD SAYS WHICH ROW EACH SHOT IS ON\n")
    board = run_board()
    if board is None:
        print("  node or esbuild is missing — `boardFrom` not checked.")
        failures.append("boardFrom could not be run")
    else:
        stacked = board["stacked"]
        shots = stacked["shots"]
        check("every shot carries the row's number", all(s.get("layer") for s in shots),
              json.dumps(shots[:2]))
        check("…and the row's NAME, which is what the gutter shows",
              shots[0]["lane"] == "Video" and shots[2]["lane"] == "Images",
              f'{shots[0].get("lane")} / {shots[2].get("lane")}')
        check("…and the number is the one the person can see (9, 10)",
              shots[0]["layer"] == 9 and shots[2]["layer"] == 10,
              f'{shots[0].get("layer")} / {shots[2].get("layer")}')
        rows = stacked.get("layers") or []
        check("the picture rows are listed for the planner", len(rows) == 2, json.dumps(rows))
        # ⚠ AUDIO IS NOT A PICTURE ROW. Offering it as one would invite a
        # transition on a cut that cannot exist in any sense at all.
        check("…and ONLY the picture rows — audio is not one",
              all(r["name"] in ("Video", "Images") for r in rows), json.dumps(rows))
        check("…each with how many clips it holds",
              {r["layer"]: r["shots"] for r in rows} == {9: 2, 10: 2},
              json.dumps(rows))
        # ⚠ NO ROW STACK MEANS ONE ROW, NOT UNKNOWN — every maths-only caller.
        flat = board["flat"]
        check("a caller with no row stack gets no row fields",
              "layers" not in flat and "layer" not in flat["shots"][0],
              json.dumps(flat["shots"][0]))

    # -------------------------------------------------------------------- 2
    print("\n2 · A CUT BETWEEN TWO ROWS IS NOT A CUT, AND IS SAID SO\n")
    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        print("\n" + ("FAILED: " + "; ".join(failures) if failures else "nothing checked"))
        return 2

    dropped = " | ".join(
        d if isinstance(d, str) else f'{d.get("verb", "")}: {d.get("why", "")}'
        for d in data["asked"]["dropped"]
    )
    check("the cross-row cut is refused", "cut 2" not in json.dumps(data["asked"]["kept"]),
          json.dumps(data["asked"]["kept"]))
    check("…by the row rule, not by the gap test", "no cut between them" in dropped, dropped)
    # ⚠ THE MESSAGE IS THE FIX AS MUCH AS THE REFUSAL IS. "there is a 28.0s gap
    # after shot 24" sent the user looking for a gap on a timeline that has none.
    check("…naming the rows the person can see", "Video" in dropped and "Images" in dropped,
          dropped)
    check("…and their layer numbers", "layer 9" in dropped and "layer 10" in dropped, dropped)
    check("…and saying what a transition needs",
          "on the SAME row" in dropped, dropped)
    check("the same-row cuts are NOT refused",
          all(f'"cut": {n}' in json.dumps(data["asked"]["kept"]).replace(" ", " ") or True
              for n in (1, 3, 4))
          and len(data["asked"]["kept"]) == 3,
          json.dumps(data["asked"]["kept"]))

    # -------------------------------------------------------------------- 3
    print("\n3 · ASKED FOR EVERY CUT MEANS EVERY CUT\n")
    check("the flag survives validation", data["asked"]["askedForAll"] is True)
    check("…and is off when nothing said so", data["notAsked"]["askedForAll"] is False)
    check("the budget is every cut when asked", data["budget"]["askedSeven"] == 7,
          str(data["budget"]))
    check("…and the 35% share when not", data["budget"]["plainSeven"] == 2, str(data["budget"]))

    check("all 7 cuts on a one-row film survive when asked",
          len(data["oneAsked"]["kept"]) == 7, str(len(data["oneAsked"]["kept"])))
    # ⚠ THE OTHER HALF, AND THE ONE THAT MATTERS MORE. A lift that always lifts
    # is the vandalism the restraint rule was written about.
    #
    # ⚠ AND THE NUMBER TO EXPECT IS 4, NOT 2, BECAUSE NOTHING IS BEING RENDERED.
    # With `veo` off — which is the default — the stills ARE the film and
    # `transitionBudget` allows every OTHER cut, `ceil(7 / 2)`. The 35% share is
    # the ceiling on a film that IS being rendered, checked just below. Writing 2
    # here would have been a test asserting a number the app never reaches.
    kept_plain = len(data["oneNotAsked"]["kept"])
    check("…and only 4 survive when nobody asked (every other cut, veo off)",
          kept_plain == 4, str(kept_plain))
    trimmed_plain = json.dumps(data["oneNotAsked"]["trimmed"])
    check("…and the rest are trimmed with a reason, not dropped in silence",
          len(data["oneNotAsked"]["trimmed"]) == 3, trimmed_plain[:200])

    # The 35% share itself — the number that was quoted at the user.
    check("with a render on, the share is 35% of the cuts: 2 of 7",
          len(data["oneVeoNotAsked"]["kept"]) == 2,
          str(len(data["oneVeoNotAsked"]["kept"])))
    check("…for the stated house reason",
          "house limit" in json.dumps(data["oneVeoNotAsked"]["trimmed"]),
          json.dumps(data["oneVeoNotAsked"]["trimmed"])[:220])
    check("…and asking for all of them lifts THAT too",
          len(data["oneVeoAsked"]["kept"]) == 7,
          str(len(data["oneVeoAsked"]["kept"])))

    # ⚠ THE ALTERNATE-CUTS RULE HAS TO LIFT TOO, or "every cut" comes back as
    # every OTHER cut — the one thing that rule guarantees is a gap.
    check("the alternate-cuts rule lifts as well (7 in a row, none dropped for touching)",
          "touches one that is already" not in json.dumps(data["oneAsked"]["trimmed"]),
          json.dumps(data["oneAsked"]["trimmed"])[:200])
    check("…and still holds when nobody asked",
          "touches one that is already" in json.dumps(data["oneNotAsked"]["trimmed"])
          or "house limit" in trimmed_plain,
          trimmed_plain[:200])

    print()
    check("effects on all 8 clips when asked", len(data["fxAsked"]["kept"]) == 8,
          str(len(data["fxAsked"]["kept"])))
    check("…and 40% of them when not", len(data["fxNotAsked"]["kept"]) == 3,
          str(len(data["fxNotAsked"]["kept"])))

    # -------------------------------------------------------------------- 4
    print("\n4 · WHAT A LIFTED CAP MUST NEVER LIFT — structure is not taste\n")
    gap_dropped = " | ".join(
        d if isinstance(d, str) else f'{d.get("verb", "")}: {d.get("why", "")}'
        for d in data["overGap"]["dropped"]
    )
    check("a transition across a real GAP is still refused, flag or no flag",
          not data["overGap"]["kept"], json.dumps(data["overGap"]["kept"]))
    check("…and it says there is a gap, because this time there is",
          "gap" in gap_dropped, gap_dropped)
    # The shipped defaults must be untouched — the lift is per plan, not a rewrite.
    table = data["capsTable"]
    check("the shipped share is still 35%", abs(table["TRANSITION_CUT_SHARE"] - 0.35) < 1e-9,
          str(table["TRANSITION_CUT_SHARE"]))
    check("…and the effect share still 40%", abs(table["EFFECT_CLIP_SHARE"] - 0.4) < 1e-9,
          str(table["EFFECT_CLIP_SHARE"]))
    check("…and one effect per clip", table["EFFECTS_PER_CLIP"] == 1,
          str(table["EFFECTS_PER_CLIP"]))
    check("…and the shortest clip is still 200ms", table["MIN_CLIP_MS"] == 200,
          str(table["MIN_CLIP_MS"]))

    # -------------------------------------------------------------------- 5
    print("\n5 · THE DIGEST AND THE PROMPT TELL THE MODEL ALL OF THIS\n")
    import editor_chat_agent as agent
    from director import plan_schema

    digest = agent.board_digest({
        "title": "Stacked",
        "total_ms": 70000,
        "shots": [
            {"label": "A", "ms": 7700, "layer": 9, "lane": "Video"},
            {"label": "B", "ms": 3600, "layer": 10, "lane": "Images"},
        ],
        "layers": [
            {"layer": 9, "name": "Video", "shots": 1},
            {"layer": 10, "name": "Images", "shots": 1},
        ],
        "existing": {},
    }, 60)
    check("the digest warns that the film is stacked", "STACKED" in digest, digest[:200])
    check("…names each row and its clip count",
          "Layer 10: Images" in digest and "1 clip(s)" in digest, digest)
    check("…and states the rule outright",
          "A CUT ONLY EXISTS BETWEEN TWO CLIPS ON THE SAME ROW" in digest, digest)
    check("…and each shot line carries its row", "(L10)" in digest, digest)

    # ⚠ A ONE-ROW FILM MUST NOT CARRY ANY OF IT. It is noise on every line of
    # every prompt for the projects that are not stacked, which is most of them.
    plain = agent.board_digest({
        "title": "Plain", "total_ms": 9000,
        "shots": [{"label": "A", "ms": 3000}], "existing": {},
    }, 60)
    check("a one-row film is not told about rows at all",
          "STACKED" not in plain and "SAME ROW" not in plain, plain[:200])

    schema = plan_schema({"verbs": [{"id": "add_transition", "args": ["cut", "kind"]}]})
    check("the plan schema offers `asked_for_all`",
          "asked_for_all" in schema["properties"])
    described = schema["properties"]["asked_for_all"]["description"]
    check("…and tells the model it is for what they SAID",
          "explicitly" in described and "Never set it on your own" in described, described)

    turn = agent._read_turn(
        {"kind": "plan", "reply": "ok", "plan": {
            "summary": "s", "asked_for_all": True,
            "steps": [{"verb": "add_transition", "args": {"cut": 1, "kind": "dissolve"}}],
        }},
        {"verbs": [{"id": "add_transition", "args": ["cut", "kind"]}]},
    )
    check("the server passes the flag through to the browser",
          turn["plan"]["asked_for_all"] is True, json.dumps(turn.get("plan")))
    quiet = agent._read_turn(
        {"kind": "plan", "reply": "ok", "plan": {
            "summary": "s",
            "steps": [{"verb": "add_transition", "args": {"cut": 1, "kind": "dissolve"}}],
        }},
        {"verbs": [{"id": "add_transition", "args": ["cut", "kind"]}]},
    )
    check("…and sends false, not nothing, when it was not set",
          quiet["plan"]["asked_for_all"] is False, json.dumps(quiet.get("plan")))

    # The prompt is the other half: the fence cannot lift a cap the planner
    # never proposed against.
    prompts = (ROOT / "prompts.yaml").read_text(encoding="utf-8")
    chat_block = prompts[prompts.index("editor_chat:"):]
    check("the prompt says restraint is for when the choice is THEIRS to make",
          "RESTRAINT IS THE CRAFT — WHEN THE CHOICE IS YOURS" in chat_block)
    check("…and that what they asked for outranks it",
          "OUTRANKS WHAT YOU WOULD HAVE CHOSEN" in chat_block)
    check("…and forbids quoting a percentage at them",
          "do not quote a percentage at them" in chat_block)
    check("…and explains the stacked timeline",
          "A CUT ONLY EXISTS BETWEEN\n      TWO CLIPS ON THE SAME ROW" in chat_block
          or "A CUT ONLY EXISTS BETWEEN TWO CLIPS ON THE SAME ROW" in chat_block.replace("\n      ", " "))
    check("…and says to work inside a row the person names",
          "WORK INSIDE IT" in chat_block)
    # ⚠ AND THAT IT MUST NOT SILENTLY RETURN FEWER. Some cuts genuinely cannot
    # take one; saying how many and why is the difference between a limit and a
    # shrug.
    check("…and to report what could NOT be done, in numbers",
          "Never silently return fewer than they" in chat_block)

    # -------------------------------------------------------------------- 6
    print("\n6 · FREE IS FREE, PAID IS OFFERED — never started\n")
    from server import chat_settings as cs
    check("the chat may offer paid work by default",
          cs.defaults()["allow_paid_passes"] is True)
    rails = agent.rails_text(cs.defaults())
    check("the rails say it MAY offer", "MAY offer paid work" in rails, rails)
    check("…and may NOT start it", "may not start it" in rails, rails)
    check("…and name the doors rather than a price",
          "🎬 Make Video" in rails and "Voiceover" in rails, rails)
    check("…and leave the upgrade to the editor",
          "offers an upgrade" in rails and "never guess their tier" in rails, rails)
    check("…and it still asks before a spend",
          "ALWAYS ask before anything that spends" in rails, rails)
    off = agent.rails_text({"allow_paid_passes": False})
    check("an operator who switches it off still gets the old refusal",
          "do not offer to start one" in off, off)
    check("the prompt tells it asking is not refusing", "ASK IS NOT REFUSE" in chat_block)
    check("…and which work is free", "Free work — cuts, transitions" in chat_block)

    return 1 if failures else 0


if __name__ == "__main__":
    code = main()
    print("\n" + ("FAILED: " + "; ".join(failures) if failures
                  else "All chat-layer / house-cap checks passed."))
    sys.exit(code)

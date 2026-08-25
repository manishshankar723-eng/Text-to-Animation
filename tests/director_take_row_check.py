"""A TAKE IS NOT A SHOT — and the two lists must never be indexed against each
other.

    python tests/director_take_row_check.py

⚠ THIS IS A BUG THAT SHIPPED AND WAS REPORTED FROM THE SCREEN, which is why it
now has a test of its own. `attachVeoClip` appends a finished Veo render to
`frames` as an ordinary clip on the Storyboard video row, and a storyboard import
can bring a video row of its own — so the editor's picture list on an animated
8-shot project holds SIXTEEN clips: eight panels and eight takes.

`readDirectorCtx` runs `shotRow` and hands the runner the eight. Two things then
went on counting into the SIXTEEN:

  1. `add_transition` asked the editor for `cut` and the editor resolved
     `frames[cut - 1]` against its own unfiltered list. So the record was created
     against whatever clip sat at that index — usually a take — and then the
     length patch looked it up by `ctx.frames[cut - 1].id`, the PANEL, and found
     nothing. Reported as: "transition ek bhi jagah nahi laga hua hai" on a run
     whose preview promised four dissolves. ⚠ NOTHING THREW AND NOTHING WAS
     LOGGED: the step reported "done", a real record existed, and it rendered
     nowhere a person could see. That is the only kind of bug worth a whole test
     file.
  2. The PANEL was handed the raw list too, so its header read "16 shots · 64.0s"
     for an 8-shot 32-second film and its preview table drew sixteen rows of
     which the last eight were empty dashes.

So the property under test is one sentence: **every index the Director produces is
resolved against the row the Director counted, and nothing else.**

⚠ AND THE LAYOUT OF THE PICTURE LIST DECIDES HOW BADLY IT BREAKS, which is why
three of them are checked rather than one. This was the surprise while writing the
test, and it is the reason the bug survived as long as it did:

  · `appended`    — eight panels, then eight takes. `frames[cut - 1]` for cuts 1-7
                    lands on the right panel BY ACCIDENT, because the panels
                    happen to occupy exactly the indices the shot row does. The
                    old code was CORRECT here, and this is almost certainly the
                    layout it was written and eyeballed against.
  · `interleaved` — take, panel, take, panel. Every EVEN cut is wrong.
  · `takes_first` — eight takes, then eight panels. EVERY cut is wrong, and every
                    transition in the run lands on a clip on the video row where
                    nothing renders it. This is the shape that produces the report.

So the "the naive build would have failed" guard is asserted only where the two
answers genuinely differ, and the `appended` case asserts the OPPOSITE — that they
coincide — because a test that pretended otherwise would be describing a bug that
layout does not have.

Five things are checked:

  1. THE ID PROPERTY. `add_transition` hands the editor a FRAME ID, and in all
     three layouts it is the third PANEL's id.
  2. THE NAIVE BUILD FAILS IT, in the two layouts where it can — so this test
     cannot quietly start passing against the bug it was written for.
  3. THE LENGTH LANDS. `add_transition` patches the record it just made, so the
     id it created against and the id it looks up by have to be the same one.
  4. NOTHING CHANGES FOR A PROJECT WITH NO TAKES. `shotRow` returns the same
     arrays and cut 3 is still the third panel.
  5. THE CONTRACT. Nothing in `ACTION_API` is named `addTransitionAtCut` any
     more, and every verb's `needs` still names something in the list.

Needs node. No browser, no backend, no model, no money — the editor is a stub that
records what it was asked to do.
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
import { ACTIONS, ACTION_API, validateStep } from "__ACTIONS__";
import { capabilities } from "__CAPS__";
import { isTake, shotRow } from "__VEO__";

const caps = capabilities();

/**
 * A project with panels AND takes in one picture list, the way the editor holds
 * it. `order` says how they are arranged: "appended" is what `attachVeoClip`
 * produces (eight panels, then eight takes) and "interleaved" is what a board
 * import that brings both rows can produce.
 */
function project(shots, order) {
  const panels = [];
  const takes = [];
  for (let i = 0; i < shots; i += 1) {
    panels.push({
      id: `p${i + 1}`,
      duration_ms: 4000,
      label: `Shot ${i + 1}`,
      kind: "image",
      src: { kind: "panel", storyboard_id: "sb1", index: i },
    });
    takes.push({
      id: `v${i + 1}`,
      duration_ms: 4000,
      label: `Shot ${i + 1}`,
      kind: "video",
      src: { kind: "panel", storyboard_id: "sb1", index: i },
    });
  }
  const frames = [];
  if (order === "interleaved") {
    for (let i = 0; i < shots; i += 1) frames.push(takes[i], panels[i]);
  } else if (order === "takes_first") {
    frames.push(...takes, ...panels);
  } else {
    frames.push(...panels, ...takes);
  }
  // ⚠ STARTS ARE PER ROW, NOT PER LIST, because the two rows play AT THE SAME
  // TIME. A take sits directly over the panel it was made from, so both rows run
  // 0–32s in parallel — laying all sixteen clips end to end would describe a
  // 64-second film nobody has, and would put a fake GAP under every panel the
  // moment the takes were interleaved. `frameSpans` in the editor does it this
  // way; this is the same arithmetic.
  const ends = {};
  const starts = frames.map((f) => {
    const track = f.kind === "video" ? 1 : 0;
    const at = ends[track] || 0;
    ends[track] = at + f.duration_ms;
    return at;
  });
  return { frames, starts };
}

/** The same project with a HOLE punched in the panel row after shot `n`. */
function withGap(shots, order, n, gapMs) {
  const built = project(shots, order);
  const panelIndexes = built.frames
    .map((f, i) => (f.kind === "video" ? -1 : i))
    .filter((i) => i >= 0);
  // Everything from panel n+1 onwards slides later — which is exactly what
  // `spreadPanelsForRenders` does when a take runs longer than its hold.
  for (let k = n; k < panelIndexes.length; k += 1) {
    built.starts[panelIndexes[k]] += gapMs;
  }
  return built;
}

/** The editor, as a verb can see it: it records, it does not edit. */
function stubEditor(transitions) {
  const calls = [];
  return {
    calls,
    api: {
      addTransitionAfterFrame: (entry, afterFrameId) => {
        calls.push({ fn: "addTransitionAfterFrame", afterFrameId, kind: entry.kind });
        // What the real editor does: makes a record against that id.
        transitions.push({ id: `t${transitions.length + 1}`, after_frame_id: afterFrameId,
                           kind: entry.kind, duration_ms: 500 });
      },
      patchTransition: (id, patch) => calls.push({ fn: "patchTransition", id, patch }),
    },
  };
}

const out = { orders: {} };

for (const order of ["appended", "interleaved", "takes_first"]) {
  const raw = project(8, order);
  const row = shotRow(raw.frames, raw.starts);
  const transitions = [];
  const editor = stubEditor(transitions);
  const ctx = {
    ...row,
    texts: [], shapes: [], overlays: [], audioTracks: [],
    transitions,
    readTransitions: () => transitions,
    totalMs: row.frames.reduce((s, f) => s + f.duration_ms, 0),
    caps,
  };

  // Cut 3 with a length on it — the two halves of `add_transition` that used to
  // disagree about which clip they were talking about.
  const step = validateStep({ verb: "add_transition", args: { cut: 3, kind: "dissolve", ms: 700 } }, caps, ctx);
  // ⚠ A REFUSED STEP NEVER REACHES `run`, in the runner or here. Passing
  // `step.args` regardless would hand `run` an `undefined` and turn a clean drop
  // into a TypeError — which is what this harness did on its first attempt, and a
  // fair warning about how the real runner would behave if it skipped the check.
  if (step.ok) ACTIONS.add_transition.run({ api: editor.api, args: step.args, ctx, refs: {} });

  out.orders[order] = {
    rawCount: raw.frames.length,
    shotCount: row.frames.length,
    // What the plan MEANT: the third shot of the film.
    meantId: row.frames[2].id,
    // What the naive index into the UNFILTERED list would have picked.
    naiveId: raw.frames[2].id,
    naiveIsTake: isTake(raw.frames[2]),
    validated: step.ok,
    why: step.ok ? "" : step.why,
    calls: editor.calls,
    records: transitions.map((t) => ({ after: t.after_frame_id, ms: t.duration_ms })),
  };
}

// =========================================================================
// THE GAP — a transition across a hole is refused, with a reason
// =========================================================================
// ⚠ THIS IS THE SILENT FAILURE, and it is the one an animated project actually
// hits. `spreadPanelsForRenders` pushes the panels after a long take clear of its
// end, so the panel row gets holes in it — and `transitionWindow` will not place a
// transition where the next clip does not start exactly where this one ends
// ("there is no edit point in a gap"). Before the guard the record was still
// created, the step still logged "done", and nothing rendered.
function gapCase(gapMs) {
  const raw = gapMs ? withGap(8, "appended", 3, gapMs) : project(8, "appended");
  const row = shotRow(raw.frames, raw.starts);
  const ctx = {
    ...row, texts: [], shapes: [], overlays: [], audioTracks: [], transitions: [],
    readTransitions: () => [], totalMs: 32000, caps,
  };
  const at = (cut) => validateStep(
    { verb: "add_transition", args: { cut, kind: "dissolve", ms: 700 } }, caps, ctx
  );
  return {
    // Cut 3 is the one the hole was punched after.
    onGap: (({ ok, why }) => ({ ok, why }))(at(3)),
    // Cut 1 is well before it and must be untouched.
    before: (({ ok, why }) => ({ ok, why }))(at(1)),
    // Cut 5 is after it, where the row is contiguous again.
    after: (({ ok, why }) => ({ ok, why }))(at(5)),
  };
}
out.gap = gapCase(4000);
out.noGap = gapCase(0);

// ⚠ AND A CALLER WITH NO `starts` AT ALL IS TRUSTED, because every maths-only
// test and `boardFrom`'s own callers mean "laid end to end" by omitting them. A
// guard that refused those would break the rules planner on a plain animatic.
out.noStarts = (() => {
  const built = project(4, "appended");
  const row = shotRow(built.frames, built.starts);
  const ctx = { ...row, starts: [], transitions: [], readTransitions: () => [],
                texts: [], shapes: [], overlays: [], audioTracks: [], totalMs: 16000, caps };
  const step = validateStep({ verb: "add_transition", args: { cut: 2, kind: "dissolve" } }, caps, ctx);
  return { ok: step.ok, why: step.ok ? "" : step.why };
})();

out.contract = {
  api: ACTION_API,
  hasAtCut: ACTION_API.includes("addTransitionAtCut"),
  hasAfterFrame: ACTION_API.includes("addTransitionAfterFrame"),
  // Every verb's `needs` has to name something the editor is asked to supply.
  strays: Object.values(ACTIONS)
    .flatMap((a) => (a.needs || []).map((n) => ({ verb: a.verb, needs: n })))
    .filter((r) => !ACTION_API.includes(r.needs)),
};

// And a project with NO takes must be untouched by any of this — `shotRow`
// returns the same arrays, and cut 3 is raw index 2 exactly as it always was.
const plain = project(8, "appended");
plain.frames = plain.frames.filter((f) => !isTake(f));
plain.starts = plain.starts.slice(0, plain.frames.length);
const plainRow = shotRow(plain.frames, plain.starts);
out.plain = {
  same: plainRow.frames === plain.frames,
  count: plainRow.frames.length,
  third: plainRow.frames[2].id,
};

process.stdout.write(JSON.stringify(out));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-takerow-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__ACTIONS__", (AGENT / "actions.js").as_uri())
                .replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
                .replace("__VEO__", (AGENT / "veo_pass.js").as_uri())
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
    print("\n=== A TAKE IS NOT A SHOT: every index is resolved against the shot row ===")
    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 2

    for order, o in data["orders"].items():
        print(f"\n⚠ TAKES {order.upper()} — 8 panels + 8 takes in one picture list.\n")
        check(f"[{order}] the editor holds 16 clips",
              o["rawCount"] == 16, str(o["rawCount"]))
        check(f"[{order}] ...and `shotRow` hands the Director 8 shots",
              o["shotCount"] == 8, str(o["shotCount"]))

        check(f"[{order}] the step validates — the panel row has no holes in it",
              o["validated"], o["why"])
        adds = [c for c in o["calls"] if c["fn"] == "addTransitionAfterFrame"]
        check(f"[{order}] the verb asks for the transition BY FRAME ID",
              len(adds) == 1 and "afterFrameId" in adds[0], json.dumps(o["calls"]))
        check(f"[{order}] ⚠ and the id is shot 3 of the FILM, not clip 3 of the list",
              adds and adds[0]["afterFrameId"] == o["meantId"],
              f"got {adds[0]['afterFrameId'] if adds else None}, meant {o['meantId']}")
        # ⚠ THE GUARD THAT STOPS THIS TEST PASSING AGAINST THE BUG IT WAS WRITTEN
        # FOR — and it can only be asserted where the two answers actually differ.
        # In the `appended` layout they do not, which is not a hole in the test: it
        # is the fact that explains how the bug survived. Asserted either way, so
        # neither reading can drift.
        if order == "appended":
            check(f"[{order}] ⚠ the naive index AGREES here — this layout hid the bug",
                  o["naiveId"] == o["meantId"],
                  f"naive={o['naiveId']} meant={o['meantId']}")
        else:
            check(f"[{order}] ...and the naive unfiltered index is a DIFFERENT clip",
                  o["naiveId"] != o["meantId"],
                  f"naive={o['naiveId']} meant={o['meantId']}")

        check(f"[{order}] the record was made against that same frame",
              o["records"] == [{"after": o["meantId"], "ms": 500}], json.dumps(o["records"]))
        patches = [c for c in o["calls"] if c["fn"] == "patchTransition"]
        check(f"[{order}] ⚠ and the 0.7s LENGTH lands — the lookup finds what was made",
              len(patches) == 1 and patches[0]["patch"] == {"duration_ms": 700},
              json.dumps(o["calls"]))

    print("\n⚠ WHERE THE OLD CODE ACTUALLY LANDED. With the takes appended it hit\n"
          "  the right panel by accident; in the other two layouts it hit a clip on\n"
          "  the video row, where no transition is ever drawn.\n")
    check("appended: clip 3 of the raw list is the third PANEL — hence the luck",
          not data["orders"]["appended"]["naiveIsTake"]
          and data["orders"]["appended"]["naiveId"] == "p3",
          json.dumps(data["orders"]["appended"]["naiveId"]))
    check("⚠ interleaved: clip 3 of the raw list is a TAKE",
          data["orders"]["interleaved"]["naiveIsTake"],
          json.dumps(data["orders"]["interleaved"]["naiveId"]))
    check("⚠ takes_first: clip 3 is a TAKE too — so EVERY cut in the run was wrong",
          data["orders"]["takes_first"]["naiveIsTake"],
          json.dumps(data["orders"]["takes_first"]["naiveId"]))

    print("\n⚠ AND A PROJECT WITH NO TAKES IS UNTOUCHED BY ANY OF THIS.\n")
    p = data["plain"]
    check("`shotRow` returns the SAME arrays when it filtered nothing",
          p["same"], str(p["same"]))
    check("...8 shots, and shot 3 is still the third panel",
          p["count"] == 8 and p["third"] == "p3", json.dumps(p))

    print("\n⚠ A TRANSITION ACROSS A GAP IS REFUSED IN THE PREVIEW, not reported as\n"
          "  done. `spreadPanelsForRenders` punches those holes, so this is the case\n"
          "  an already-animated project actually hits.\n")
    g = data["gap"]
    check("⚠ a dissolve on the cut the hole is at is DROPPED",
          not g["onGap"]["ok"], json.dumps(g["onGap"]))
    check("...and the reason names the gap and its length",
          "gap after shot 3" in (g["onGap"]["why"] or "")
          and "4.0s" in (g["onGap"]["why"] or ""),
          g["onGap"]["why"])
    check("...while the cut BEFORE it is still fine",
          g["before"]["ok"], json.dumps(g["before"]))
    check("...and so is the one after, where the row is contiguous again",
          g["after"]["ok"], json.dumps(g["after"]))
    ng = data["noGap"]
    check("⚠ and with no hole, all three cuts validate — the guard costs nothing",
          ng["onGap"]["ok"] and ng["before"]["ok"] and ng["after"]["ok"],
          json.dumps(ng))
    check("a caller that sends no `starts` is trusted, not refused",
          data["noStarts"]["ok"], json.dumps(data["noStarts"]))

    print("\n⚠ THE CONTRACT — `ACTION_API` names what a verb may reach for.\n")
    c = data["contract"]
    check("`addTransitionAfterFrame` is in the list", c["hasAfterFrame"])
    check("⚠ `addTransitionAtCut` is NOT — the index form must not be reachable",
          not c["hasAtCut"], json.dumps(c["api"]))
    check("every verb's `needs` names something in the list",
          not c["strays"], json.dumps(c["strays"]))

    print("\n" + "-" * 70)
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

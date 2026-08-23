"""THE ORDER OF PHASE B — a transition decided after the voiceover lands on the
cut the voiceover MADE, not the one it replaced.

    python tests/director_voice_order_check.py

⚠ THIS IS THE MOST LIKELY BUG IN THE WHOLE FEATURE, WHICH IS WHY IT HAS ITS OWN
TEST. The voiceover pass stretches the shot that owns a line to cover what is
said over it and pushes every shot after it along (`_lay_out_speech`, server
side). Every timing decision in an edit plan — which cuts breathe, how long a
dissolve runs, which shots are held long enough to move on — is read off the shot
LENGTHS. So a plan written before the pass and applied after it is a plan about a
film that no longer exists.

And it fails INVISIBLY. The run reports "24 edits made", every step succeeds,
every transition lands on a real cut. They are simply the wrong cuts, and the
only way anyone finds out is by watching the film and feeling that the dissolves
are in odd places. There is no exception, no red log line and no dropped step —
which is exactly the kind of failure a test has to be written for on purpose,
because nothing else will ever notice it.

So the property under test is one sentence: **the transitions the Director places
are the transitions the film DESERVES AFTER the sound has landed.**

Four things are checked, in order of how much they matter:

  1. THE ORDER PROPERTY. A board whose rhythm the voiceover CHANGES — shot 2 is
     the long one before the pass, shot 6 becomes the long one after it, because
     shot 6 carries a nine-second line. The plan that runs must dissolve after
     shot 6. This test is written so that the naive implementation — plan first,
     speak second — fails it, and it is checked that it does.
  2. THE TIMING DECISION. A re-time the pass invalidated (`set_shot_duration` on
     a shot it stretched) is dropped with a reason, because applying it would cut
     the spoken line off mid-word.
  3. THE SAME WORDS TWICE. An `add_text` of words the pass just laid down as a
     caption is dropped — the "no duplicate words in the Text lane" property.
  4. THE SCRIPT. The board's dialogue wins whenever it has any; a silent board
     falls back to the lines the analyse call wrote, mapped onto real clip ids,
     and a line over a clip that is not a storyboard panel is dropped HERE with a
     reason rather than silently by the server.

Needs node. Nothing here touches a browser, a backend, a model or a dollar — the
voiceover is simulated by doing to the picture row exactly what `_lay_out_speech`
does to it, which is the arithmetic `tests/voiceover_fit_check.py` measures
against the real server.
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
import { capabilities } from "__CAPS__";
import { applyGuardrails, housePlan } from "__HOUSE__";
import { validatePlan } from "__SCHEMA__";
import { reanchor, scriptFor, shiftsOf, spokenWords, speechDue } from "__VOICE__";

const caps = capabilities();

/**
 * A timeline. `long` gives those shots (1-based) three times the hold, and
 * `panels` says which are storyboard shots — the voiceover can only read over
 * those, exactly as `_requested_lines` decides on the server.
 */
function timeline(lengths, panels = null) {
  const frames = [];
  const starts = [];
  let at = 0;
  lengths.forEach((ms, i) => {
    frames.push({
      id: `f${i + 1}`,
      duration_ms: ms,
      label: `Shot ${i + 1}`,
      src: { kind: !panels || panels.includes(i + 1) ? "panel" : "upload", index: i },
    });
    starts.push(at);
    at += ms;
  });
  return { frames, starts, texts: [], shapes: [], transitions: [], overlays: [],
           audioTracks: [], totalMs: at, caps };
}

/**
 * WHAT THE SERVER DOES TO THE PICTURE ROW — `_lay_out_speech` with `fit_shots`
 * on, in five lines. The shot that owns a line is stretched to cover it plus the
 * gap, and it never SHRINKS: a line shorter than its hold moves nothing. Forward
 * only, and the ripple is implicit because the row is laid end to end.
 */
const GAP_MS = 250;
function speakOver(ctx, spoken) {
  const frames = ctx.frames.map((f) => {
    const line = spoken.find((s) => s.frame_id === f.id);
    if (!line) return { ...f };
    return { ...f, duration_ms: Math.max(f.duration_ms, line.ms + GAP_MS) };
  });
  const starts = [];
  let at = 0;
  for (const f of frames) { starts.push(at); at += f.duration_ms; }
  return { ...ctx, frames, starts, totalMs: at };
}

/** The one door: raw plan in, the steps that would actually run out. */
const door = (raw, ctx, include = {}) => {
  const checked = validatePlan({ ...raw, include }, caps, ctx);
  const fenced = applyGuardrails(checked.plan, ctx);
  return {
    steps: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
    cuts: fenced.plan.steps.filter((s) => s.verb === "add_transition").map((s) => s.args.cut),
    dropped: checked.dropped,
    trimmed: fenced.trimmed,
  };
};

const out = {};

// =========================================================================
// 1. THE ORDER PROPERTY
// =========================================================================
// Six shots. Shot 2 is held (6s) and everything else is 2s, so BEFORE the pass
// the rhythm says "dissolve after shot 2". Shot 6 carries a nine-second line, so
// AFTER the pass shot 6 is by far the longest hold in the film and the rhythm
// says "dissolve after shot 5"... and shot 6 is the last shot, so the cut that
// now earns a dissolve is the one after shot 2 AND the one after shot 5 is where
// the long hold begins. What matters is only that the two answers DIFFER and
// that the run uses the second one.
const before = timeline([2000, 6000, 2000, 2000, 2000, 2000]);
const script = [{ frame_id: "f5", ms: 9000, text: "the machine is finished" }];
const after = speakOver(before, script);

out.rhythm = {
  before: door(housePlan(before, {}), before).cuts,
  after: door(housePlan(after, {}), after).cuts,
  grewFrom: before.frames.map((f) => f.duration_ms),
  grewTo: after.frames.map((f) => f.duration_ms),
};

// ⚠ THE RUN, AS THE RUNNER DOES IT: speak, re-read, re-anchor, then the door.
const shifts = shiftsOf(before.frames, after.frames);
const anchored = reanchor({ source: "house", raw: housePlan(before, {}), ctx: after,
                            include: {}, shifts, spoken: new Set() });
out.rhythm.reanchored = door(anchored.raw, after).cuts;
out.rhythm.shifts = {
  grew: [...shifts.grew],
  movedMs: shifts.movedMs,
  anyGrew: shifts.anyGrew,
};

// ⚠ AND THE NAIVE ORDER, KEPT ON PURPOSE. This is what "plan first, speak
// second" produces, and the test asserts it is DIFFERENT — otherwise the whole
// suite could pass against a build that never re-anchors at all.
out.rhythm.naive = door(housePlan(before, {}), after).cuts;

// The model's plan goes through the same re-anchor and keeps its own choices —
// a model that dissolved on the cut after shot 4 still dissolves there, because
// it read the STORY and the story did not change. Only the rules planner is
// re-asked; see the header of voice_pass.js.
const aiRaw = {
  steps: [
    { verb: "note", args: { text: "three scenes; the workshop is the middle one" } },
    { verb: "add_transition", args: { cut: 4, kind: "dissolve", ms: 600 } },
  ],
};
out.aiKeeps = reanchor({ source: "ai", raw: aiRaw, ctx: after, include: {}, shifts,
                         spoken: new Set() }).raw.steps.map((s) => s.verb + ":" + (s.args.cut ?? ""));

// =========================================================================
// 2. THE TIMING DECISION THE PASS INVALIDATED
// =========================================================================
const retimeRaw = {
  steps: [
    // Shot 5 is the one the pass stretched to 9.25s. The plan wants it at 2.4s.
    { verb: "set_shot_duration", args: { shot: 5, ms: 2400 } },
    // Shot 3 was never touched by the pass, so its re-time is still a good idea.
    { verb: "set_shot_duration", args: { shot: 3, ms: 3000 } },
    { verb: "add_transition", args: { cut: 2, kind: "dissolve" } },
  ],
};
const retimed = reanchor({ source: "ai", raw: retimeRaw, ctx: after, include: {},
                           shifts, spoken: new Set() });
out.retime = {
  kept: retimed.raw.steps.map((s) => `${s.verb}:${s.args.shot ?? s.args.cut ?? ""}`),
  dropped: retimed.dropped,
};

const allRaw = { steps: [{ verb: "set_all_durations", args: { ms: 2000 } }] };
out.setAll = reanchor({ source: "ai", raw: allRaw, ctx: after, include: {}, shifts,
                        spoken: new Set() });
// ...and on a film where the pass moved NOTHING, the same step is untouched.
const still = shiftsOf(before.frames, before.frames);
out.setAllStill = reanchor({ source: "ai", raw: allRaw, ctx: before, include: {},
                             shifts: still, spoken: new Set() });

// =========================================================================
// 3. THE SAME WORDS TWICE
// =========================================================================
const lines = [
  { frame_id: "f5", text: "The machine is finished." },
  { frame_id: "f2", text: "Nobody is coming." },
];
const spoken = spokenWords(lines);
const textRaw = {
  steps: [
    // The words the pass has just laid down as a caption, with different
    // punctuation and case — which is how a model would write them back.
    { verb: "add_text", args: { shot: 5, text: "the machine is finished", ref: "t1" } },
    // A title that is genuinely its own thing survives.
    { verb: "add_text", args: { shot: 1, text: "2049", ref: "t2" } },
  ],
};
out.dupes = reanchor({ source: "ai", raw: textRaw, ctx: after, include: {}, shifts,
                       spoken });
// ⚠ AND WITH CAPTIONS OFF, THE SAME TITLE IS KEPT — those words are not on
// screen, so it is not a duplicate of anything.
out.dupesOff = reanchor({ source: "ai", raw: textRaw, ctx: after, include: {}, shifts,
                          spoken: new Set() });

// =========================================================================
// 4. THE SCRIPT
// =========================================================================
const board = timeline([2000, 2000, 2000], null);
const reading = {
  shots: [
    { shot: 1, dialogue: "MAYA: it is late" },
    { shot: 2, dialogue: "" },
    { shot: 3, dialogue: "Nobody is coming." },
  ],
};
const sheet = [
  { frame_id: "f1", text: "the board's own line", character: "MAYA", persona: "woman",
    voice: "", shot: "Shot 1" },
];

out.script = {
  fromBoard: scriptFor({ sheet, analysis: reading, frames: board.frames }),
  written: scriptFor({ sheet: [], analysis: reading, frames: board.frames }),
  silent: scriptFor({ sheet: [], analysis: null, frames: board.frames }),
};

// A film with an uploaded still where shot 2 should be, and a reading that gave
// that shot a line: the server would drop it silently (`_requested_lines` keeps
// only board panels), so it is dropped here with the reason on screen.
const mixed = timeline([2000, 2000, 2000], [1, 3]);
const talkative = {
  shots: [
    { shot: 1, dialogue: "MAYA: it is late" },
    { shot: 2, dialogue: "This one is over a photograph." },
    { shot: 3, dialogue: "Nobody is coming." },
    { shot: 9, dialogue: "A line for a shot that is not on the timeline." },
  ],
};
out.script.mixed = scriptFor({ sheet: [], analysis: talkative, frames: mixed.frames });

out.due = {
  on: speechDue({ voiceover: true }, out.script.fromBoard),
  off: speechDue({ voiceover: false }, out.script.fromBoard),
  nothing: speechDue({ voiceover: true }, out.script.silent),
};

process.stdout.write(JSON.stringify(out));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-voice-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
                .replace("__HOUSE__", (AGENT / "house_style.js").as_uri())
                .replace("__SCHEMA__", (AGENT / "plan_schema.js").as_uri())
                .replace("__VOICE__", (AGENT / "voice_pass.js").as_uri())
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

    print("\n⚠ THE ORDER PROPERTY — the voiceover moves the film, so the edit is\n"
          "  decided AFTER it lands. This is the whole reason phase B runs first.\n")
    r = data["rhythm"]
    check("the pass stretched the shot that carries the line",
          r["grewTo"][4] > r["grewFrom"][4] and r["grewTo"][4] == 9250,
          json.dumps(r["grewTo"]))
    check("...and left every other shot exactly as it was",
          [r["grewTo"][i] for i in (0, 1, 2, 3, 5)] == [r["grewFrom"][i] for i in (0, 1, 2, 3, 5)],
          json.dumps(r["grewTo"]))
    check("`shiftsOf` names the shot that grew, and only that one",
          r["shifts"]["grew"] == [5] and r["shifts"]["anyGrew"],
          json.dumps(r["shifts"]))
    check("...and says how far the rest of the film was pushed",
          r["shifts"]["movedMs"] == 7250, str(r["shifts"]["movedMs"]))

    check("BEFORE the pass, the rhythm dissolves after the held shot 2",
          r["before"] == [2], json.dumps(r["before"]))
    check("⚠ AFTER it, the rhythm is a different film and says a different cut",
          r["after"] != r["before"], f"before {r['before']} after {r['after']}")
    check("...and that cut is the one the SPOKEN shot made",
          r["after"] == [5], json.dumps(r["after"]))
    check("⚠ THE RE-ANCHORED PLAN IS THE ONE THE FILM DESERVES NOW",
          r["reanchored"] == r["after"],
          f"re-anchored {r['reanchored']}, should be {r['after']}")
    check("⚠ ...AND IT IS NOT WHAT PLANNING FIRST WOULD HAVE PRODUCED — which is\n"
          "       the bug this whole test exists for: every step would succeed",
          r["naive"] != r["reanchored"],
          f"naive {r['naive']} == re-anchored {r['reanchored']}")
    check("...the naive order lands on the cut the film USED to want",
          r["naive"] == r["before"], json.dumps(r["naive"]))

    check("⚠ A MODEL'S PLAN KEEPS ITS OWN CUTS — it read the story, and the story\n"
          "       did not change; only the rules planner is re-asked",
          data["aiKeeps"] == ["note:", "add_transition:4"], json.dumps(data["aiKeeps"]))

    print("\n⚠ THE TIMING DECISION THE PASS INVALIDATED — a re-time on a stretched\n"
          "  shot would cut the line off mid-word, and it would be HEARD\n")
    t = data["retime"]
    check("the re-time on the shot that grew is dropped",
          "set_shot_duration:5" not in t["kept"], json.dumps(t["kept"]))
    check("...with a reason that names the shot and its new length",
          t["dropped"] and "shot 5" in t["dropped"][0]["why"] and "9.3s" in t["dropped"][0]["why"],
          json.dumps(t["dropped"]))
    check("⚠ ...and the re-time on a shot the pass never touched SURVIVES",
          "set_shot_duration:3" in t["kept"], json.dumps(t["kept"]))
    check("...as does everything else in the plan",
          "add_transition:2" in t["kept"], json.dumps(t["kept"]))

    every = data["setAll"]
    check("`set_all_durations` after a pass that moved something is dropped",
          not every["raw"]["steps"] and len(every["dropped"]) == 1,
          json.dumps(every["dropped"]))
    check("...and it says the pass timed those shots from what is said over them",
          every["dropped"] and "said over them" in every["dropped"][0]["why"],
          json.dumps(every["dropped"]))
    still = data["setAllStill"]
    check("⚠ ...but on a run where nothing moved it is left alone — the drop is\n"
          "       about what the PASS did, never about the verb",
          len(still["raw"]["steps"]) == 1 and not still["dropped"],
          json.dumps(still))

    print("\n⚠ THE SAME WORDS TWICE — the pass writes its captions from what was\n"
          "  ACTUALLY read, so a title built out of a spoken line is one sentence\n"
          "  on screen twice, half a second apart, in two different styles\n")
    d = data["dupes"]
    kept = [s["args"]["text"] for s in d["raw"]["steps"]]
    check("the title made of a spoken line is dropped", "2049" in kept and len(kept) == 1,
          json.dumps(kept))
    check("...matched past case and punctuation, which is how it would be written back",
          d["dropped"] and "already put these words on screen" in d["dropped"][0]["why"],
          json.dumps(d["dropped"]))
    off = [s["args"]["text"] for s in data["dupesOff"]["raw"]["steps"]]
    check("⚠ ...and with Captions un-ticked the same title is KEPT — those words\n"
          "       are not on screen, so it duplicates nothing", len(off) == 2,
          json.dumps(off))

    print("\n⚠ THE SCRIPT — the board's own words whenever it has any, and lines the\n"
          "  Director wrote only when the board is silent\n")
    s = data["script"]
    check("⚠ THE BOARD WINS: its line is read, the model's paraphrase is not",
          [l["text"] for l in s["fromBoard"]["lines"]] == ["the board's own line"],
          json.dumps(s["fromBoard"]["lines"]))
    check("...and the script is not labelled as written", s["fromBoard"]["written"] is False)
    check("...it keeps the persona the board guessed",
          s["fromBoard"]["lines"][0]["persona"] == "woman",
          json.dumps(s["fromBoard"]["lines"][0]))

    w = s["written"]
    check("a SILENT board falls back to the lines the reading wrote",
          len(w["lines"]) == 2 and w["written"] is True, json.dumps(w["lines"]))
    check("...anchored to real clip ids, not to shot numbers",
          [l["frame_id"] for l in w["lines"]] == ["f1", "f3"], json.dumps(w["lines"]))
    check("⚠ ...with the speaker split off the line, so the sheet knows who talks",
          w["lines"][0]["character"] == "MAYA" and w["lines"][0]["text"] == "it is late",
          json.dumps(w["lines"][0]))
    check("...and a shot the reading left silent stays silent",
          all(l["frame_id"] != "f2" for l in w["lines"]), json.dumps(w["lines"]))
    check("no reading and no sheet is an empty script, not a crash",
          s["silent"]["lines"] == [] and s["silent"]["written"] is False,
          json.dumps(s["silent"]))

    m = s["mixed"]
    check("⚠ A LINE OVER A CLIP THAT IS NOT A BOARD PANEL IS DROPPED HERE — the\n"
          "       server would drop it silently, and the user was shown a price",
          [l["frame_id"] for l in m["lines"]] == ["f1", "f3"], json.dumps(m["lines"]))
    check("...with a reason naming the shot and why it cannot be read over",
          any("shot 2" in row["why"] and "storyboard panel" in row["why"]
              for row in m["skipped"]),
          json.dumps(m["skipped"]))
    check("...and a line for a shot that is not on the timeline goes the same way",
          any("no shot 9" in row["why"] for row in m["skipped"]),
          json.dumps(m["skipped"]))

    due = data["due"]
    check("with dialogue and the box ticked, the pass is due", due["on"]["due"] is True)
    check("un-ticked, it is not — and says so in words the panel prints",
          due["off"]["due"] is False and "switched off" in due["off"]["why"],
          json.dumps(due["off"]))
    check("with nothing to read, it is not due either",
          due["nothing"]["due"] is False and "no dialogue" in due["nothing"]["why"],
          json.dumps(due["nothing"]))

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

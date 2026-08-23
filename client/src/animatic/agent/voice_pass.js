// voice_pass.js — PHASE B: THE SOUND, and the re-anchor that has to follow it.
//
// ---------------------------------------------------------------------------
// ⚠ PHASE B RUNS FIRST. That sentence is the whole file.
// ---------------------------------------------------------------------------
// The build plan calls the voiceover "phase B" and the step list is what Phase 0
// built, which reads as though the sound comes second. It cannot.
// `POST /voiceover` with `fit_shots` on — the default, and the only setting
// anyone wants — STRETCHES the shot that owns a line to cover what is said over
// it and pushes every shot after it along (`_lay_out_speech`, server side). A
// ten-second line under a two-second picture moves the rest of the film eight
// seconds later:
//
//     image   [S9][S10][S11][S12]                        <- the plan was written
//     audio   |========= S9's line =========|               against THIS row
//
//     image   [ S9 ..................... ][S10][S11][S12] <- and this is the film
//     audio   |========= S9's line =========|                the steps will edit
//
// So every timing decision in the plan — which cuts breathe, how long a dissolve
// runs, where a caption starts, how far a push-in travels — was decided about a
// film that stops existing the moment the pass lands. The sound goes first, the
// document is re-read, and the plan is re-anchored against what came back.
//
// ---------------------------------------------------------------------------
// ⚠ RE-ANCHORING IS MOSTLY RE-ASKING, AND THAT IS THE DESIGN.
// ---------------------------------------------------------------------------
// The rules planner reads the RHYTHM off the shot lengths — "dissolve after a
// shot held 1.5× the median". After the pass the held shots are the ones
// carrying dialogue, which is a different set, so the honest answer is not to
// patch its old plan but to ask it again against the new row: it is pure,
// deterministic and free, and re-running it is the difference between a dissolve
// on the cut that now ends a scene and a dissolve on the cut that used to.
//
// A model's plan cannot be re-asked for free, so its raw steps are kept and
// simply re-validated against the new document — exactly what `recost` already
// does for a tick box. `validatePlan` re-resolves every target and
// `applyGuardrails` re-computes every budget off the film's NEW length, which is
// most of the job. What this file adds is the two things re-validation cannot
// know, because they are about what the PASS did rather than about the document:
//
//   · a `set_shot_duration` on a shot the pass stretched would cut the line off
//   · an `add_text` of words the pass just captioned is the same words twice
//
// ---------------------------------------------------------------------------
// ⚠ THE SCRIPT: THE BOARD'S, OR ONE THE DIRECTOR WROTE.
// ---------------------------------------------------------------------------
// A voiceover normally reads the STORYBOARD's dialogue — the board knows who
// says what in which shot and the timeline knows when that shot is up, so a
// line's place is a lookup rather than a drag (`_dialogue_sheet`). On a board
// with no dialogue at all that pass has nothing to read, and the 🎙 button is
// correctly disabled.
//
// The Director is the one caller that can do better, and it costs nothing extra:
// the analyse call already returns a `dialogue` line per shot, because it was
// asked what is said in this film in order to write the Veo prompts. So when the
// board is silent, THOSE lines are the script — shown in the preview, in the
// user's own language, before a single one is read aloud.
//
// ⚠ AND IT IS LABELLED AS WRITTEN. A line the board carries and a line the model
// invented are not the same object, and a user who cannot tell which they are
// looking at will assume the first. `written: true` rides with the script and
// the panel says so above the table.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE. No React, no fetch, no editor import.
// ---------------------------------------------------------------------------
// Same rule as the other four modules here: `tests/director_voice_order_check.py`
// imports this under node and drives the whole re-anchor with no browser and no
// backend. The runner owns the async — the call, the poll, the re-read — and
// this owns every decision that call's result leads to.

import { housePlan } from "./house_style.js";

/** The include flag phase B answers to. One name, and it is in `INCLUDE_KEYS`. */
export const SPEECH_KEY = "voiceover";

/**
 * A shot has to grow by more than this before anything is treated as moved.
 *
 * ⚠ NOT ZERO. `_lay_out_speech` writes whole milliseconds computed from a
 * decoded audio length, so a shot it decided not to move can still come back a
 * millisecond different through rounding. Treating that as "the pass stretched
 * this shot" would drop a perfectly good re-time and tell the user a story about
 * a line that does not exist.
 */
const MOVED_MS = 40;

/** Which frames the pass can actually read over — the server drops the rest. */
const BOARD_KINDS = ["panel", "pose"];

export function isBoardShot(frame) {
  return BOARD_KINDS.includes((frame && frame.src && frame.src.kind) || "");
}

/**
 * WHAT THE PASS DID TO THE PICTURE ROW, shot by shot.
 *
 * ⚠ MATCHED BY ID, NOT BY INDEX. The pass stretches and pushes; it never
 * reorders, splits or removes a clip. Matching on id says that out loud and
 * degrades visibly if it ever stops being true, whereas index matching would
 * quietly report nonsense about a film nobody recognises.
 *
 * @param before the picture row as it was when the plan was written
 * @param after  the row the server laid out and the editor re-read
 * @returns {{ shots, grew, movedMs, anyGrew }} — `grew` holds 1-based shot numbers
 */
export function shiftsOf(before, after) {
  const was = new Map((before || []).map((f) => [f.id, Number(f.duration_ms) || 0]));
  const shots = [];
  const grew = new Set();
  let movedMs = 0;
  let at = 0;
  (after || []).forEach((frame, i) => {
    const now = Number(frame.duration_ms) || 0;
    const old = was.has(frame.id) ? was.get(frame.id) : now;
    const grewMs = now - old;
    shots.push({
      shot: i + 1,
      id: frame.id,
      label: frame.label || "",
      was: old,
      now,
      grewMs,
      // Where this shot starts NOW. The pass is forward-only, so this doubles as
      // how far into the film everything from here on has been pushed.
      startsAt: at,
    });
    if (grewMs > MOVED_MS) {
      grew.add(i + 1);
      movedMs += grewMs;
    }
    at += now;
  });
  return { shots, grew, movedMs, anyGrew: grew.size > 0 };
}

/** Words, with everything that is not a word taken out. For comparing lines. */
function words(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** The lines the pass read, as a set of comparable strings. */
export function spokenWords(lines) {
  return new Set((lines || []).map((line) => words(line.text)).filter(Boolean));
}

/**
 * `"MAYA: it is late"` becomes `{ character: "MAYA", text: "it is late" }`.
 *
 * The analyse call is asked for "the words spoken in this shot", and a model
 * writing dialogue writes it in script form about half the time. Splitting the
 * name off is what lets the speaker reach the sheet — and the speaker is the
 * only thing the user has to go on when they want to re-cast a line.
 */
function speaker(line) {
  const raw = String(line || "").trim();
  const at = raw.indexOf(":");
  if (at > 0 && at <= 40) {
    const who = raw.slice(0, at).trim();
    const said = raw.slice(at + 1).trim();
    // A name, not a sentence that happens to have a colon in it.
    if (said && who && who.split(/\s+/).length <= 4) return { character: who, text: said };
  }
  return { character: "", text: raw };
}

/**
 * THE SCRIPT THIS RUN WILL READ.
 *
 * @param sheet    `GET /animatics/{id}/dialogue` — the board's own lines, free
 * @param analysis the reading from the analyse call, or null
 * @param frames   the picture row, for turning a shot number into a clip id
 * @returns {{ lines, written, skipped }}
 *
 * ⚠ THE BOARD WINS WHENEVER IT HAS ANYTHING TO SAY. A model asked what is spoken
 * in a shot that already has a line will paraphrase it, and a paraphrase of the
 * user's own dialogue read aloud in their own film is the worst thing this pass
 * could do. The written script is a fallback for silence, never a second opinion.
 *
 * ⚠ AND IT IS SENT AS THE SHEET EITHER WAY, rather than left to the server to
 * rebuild. `AnimaticVoiceoverRequest.lines` is the edited sheet and it wins
 * entirely, so sending what the preview showed is what makes the words on screen
 * and the words read aloud provably the same words — the same discipline the 🎙
 * dialog follows, for the same reason.
 */
export function scriptFor({ sheet, analysis, frames } = {}) {
  const rows = (sheet || []).filter((line) => String((line && line.text) || "").trim());
  if (rows.length) {
    return {
      lines: rows.map((line) => ({
        frame_id: line.frame_id || "",
        character: line.character || "",
        persona: line.persona || "",
        voice: line.voice || "",
        text: String(line.text).trim(),
        shot: line.shot || "",
      })),
      written: false,
      skipped: [],
    };
  }

  const list = frames || [];
  const lines = [];
  const skipped = [];
  for (const shot of (analysis && analysis.shots) || []) {
    const said = String((shot && shot.dialogue) || "").trim();
    if (!said) continue;
    const frame = list[Number(shot.shot) - 1];
    if (!frame) {
      skipped.push({ shot: shot.shot, why: `there is no shot ${shot.shot} to say it over` });
      continue;
    }
    // ⚠ THE SERVER DROPS A LINE THAT IS NOT OVER A BOARD PANEL
    // (`_requested_lines`) — there is no panel to stretch and nothing to ripple
    // — so it is dropped HERE too, with the reason on screen. A line silently
    // missing from a run the user watched being priced is worse than one they
    // were told about before it ran.
    if (!isBoardShot(frame)) {
      skipped.push({
        shot: shot.shot,
        why: `shot ${shot.shot} is not a storyboard panel, so there is nothing to read it over`,
      });
      continue;
    }
    const { character, text } = speaker(said);
    lines.push({
      frame_id: frame.id,
      character,
      // "" — let the server's own casting decide. Guessing a persona from a name
      // the model invented would be a guess about a guess.
      persona: "",
      voice: "",
      text,
      shot: frame.label || `Shot ${shot.shot}`,
    });
  }
  return { lines, written: lines.length > 0, skipped };
}

/**
 * IS THERE A SOUND PASS TO RUN AT ALL?
 *
 * Returns a reason rather than a boolean, because each way of answering "no" is
 * a different thing to tell the user and the panel prints it verbatim.
 */
export function speechDue(include, script) {
  if (include && include[SPEECH_KEY] === false) {
    return { due: false, why: "Voiceover is switched off for this run." };
  }
  if (!script || !script.lines.length) {
    return { due: false, why: "There is no dialogue to read." };
  }
  return { due: true, why: "" };
}

/**
 * HOW EACH PAID PASS IS NAMED WHEN IT COSTS A STEP ITS PLACE.
 *
 * ⚠ THE REASON HAS TO NAME THE PASS THAT ACTUALLY DID IT. Two phases grow shots
 * now — the voiceover stretches one to cover its line, the Veo take stretches
 * one to match the footage over it — and they are dropped for the same reason
 * but not by the same culprit. "The voiceover stretched shot 9" printed under
 * the preview of a run with no voiceover in it is the kind of wrong sentence
 * that makes a user stop believing the right ones. `growthCauses` in
 * `veo_pass.js` works out which; this is what each one is called.
 */
const CAUSES = {
  voiceover: {
    by: "the voiceover",
    cover: "to cover its line",
    harm: "cut the line off",
    all: "the voiceover set the length of",
    from: "from what is said over them",
  },
  veo: {
    by: "the Veo take",
    cover: "to match the take rendered over it",
    harm: "cut the take off",
    all: "the Veo pass set the length of",
    from: "from the footage rendered over them",
  },
  both: {
    by: "the voiceover and the Veo take",
    cover: "to cover its line and the take over it",
    harm: "cut both off",
    all: "the two paid passes set the length of",
    from: "from what is laid over them",
  },
};

/**
 * RE-ANCHOR A PLAN ONTO THE FILM THE SOUND PASS LEFT BEHIND.
 *
 * @param source   "house" | "ai" — who wrote the raw plan
 * @param raw      the plan as it arrived, before validation
 * @param ctx      the read-model AFTER the pass, freshly read
 * @param include  the tick boxes
 * @param shifts   `shiftsOf(before, after)`
 * @param spoken   `spokenWords(script.lines)` when captions were written, else empty
 * @param causes   `growthCauses(...)` — `{ [shot]: "voiceover" | "veo" | "both" }`,
 *                 so a dropped step names the pass that actually moved its shot.
 *                 Absent means "the voiceover", which is what it meant before
 *                 phase C existed and what a rules-only run still means.
 * @returns {{ raw, dropped }} — a RAW plan for `adopt`, and what the passes removed
 *
 * ⚠ IT RETURNS A RAW PLAN, NOT A VALIDATED ONE. Everything re-validation already
 * does — resolving shots, clamping a caption into the hold it belongs to,
 * re-computing the per-minute budgets off the film's new length — is left to
 * `validatePlan` and `applyGuardrails`, which the caller runs on the way out.
 * Doing any of it here as well would be a second copy of the arithmetic that
 * decides what the user is shown.
 */
export function reanchor({ source, raw, ctx, include, shifts, spoken, causes } = {}) {
  // ⚠ THE RULES PLANNER IS RE-ASKED, NOT PATCHED. See the header: its entire
  // input is the shot lengths, and the pass has just rewritten them.
  if (source !== "ai") {
    return { raw: housePlan(ctx, { include }), dropped: [] };
  }

  const grew = (shifts && shifts.grew) || new Set();
  const said = spoken || new Set();
  const blame = causes || {};
  /** What to call whatever stretched this shot. Defaults to the voiceover. */
  const cause = (shot) => CAUSES[blame[shot]] || CAUSES.voiceover;
  const dropped = [];
  const steps = [];

  ((raw && raw.steps) || []).forEach((step, index) => {
    const verb = String((step && step.verb) || "");
    const args = (step && step.args) || {};

    // ⚠ THE TIMING DECISION THE PASS INVALIDATED. The plan asked for shot 9 to
    // hold 2.4s because that is what it held when the plan was written; the pass
    // then stretched it to 10.4s to cover the line spoken over it. Applying the
    // re-time now would cut the line off mid-word, and the user would hear it.
    if (verb === "set_shot_duration" && grew.has(Number(args.shot))) {
      const row = ((shifts && shifts.shots) || []).find((s) => s.shot === Number(args.shot));
      const it = cause(Number(args.shot));
      dropped.push({
        index,
        verb,
        why:
          `${it.by} stretched shot ${args.shot} to ${(((row && row.now) || 0) / 1000).toFixed(1)}s ` +
          `${it.cover} — re-timing it now would ${it.harm}`,
      });
      return;
    }
    if (verb === "set_all_durations" && grew.size) {
      // ⚠ ONE SENTENCE FOR THE WHOLE FILM, so the culprit is whichever pass
      // touched the most of it — the alternative is a reason that lists both
      // passes on a run where only one of them ran.
      const tally = {};
      for (const n of grew) {
        const key = blame[n] || "voiceover";
        tally[key] = (tally[key] || 0) + 1;
      }
      const worst = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0] || "voiceover";
      const it = CAUSES[worst] || CAUSES.voiceover;
      dropped.push({
        index,
        verb,
        why:
          `${it.all} ${grew.size} shot${grew.size === 1 ? "" : "s"} ` +
          `${it.from}, and one length for every shot would undo that`,
      });
      return;
    }

    // ⚠ THE SAME WORDS TWICE. The polish prompt says never to subtitle the
    // dialogue and mostly it does not — but the pass writes its captions from
    // what was ACTUALLY read, so a title the model built out of a spoken line is
    // one sentence on screen twice, half a second apart, in two different
    // styles. Cheap to catch here and impossible to miss on screen.
    if (verb === "add_text" && said.size) {
      const mine = words(args.text);
      if (mine && said.has(mine)) {
        dropped.push({
          index,
          verb,
          why: "the voiceover already put these words on screen as a caption",
        });
        return;
      }
    }

    steps.push(step);
  });

  return { raw: { ...raw, steps }, dropped };
}

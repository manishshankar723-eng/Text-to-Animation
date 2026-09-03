// chat_turn.js — WHAT ONE REPLY FROM THE AI EDITOR IS ALLOWED TO BE.
//
// The Director (`plan_schema.js`) has exactly one shape of answer: a plan. That
// is right for a popup you open once, fill in, and run — the user has already
// said everything they are going to say. It is wrong for a conversation, where
// most turns are not an edit at all.
//
// So a chat turn is ONE OF THREE THINGS, and which one it is decides what the
// panel draws:
//
//   answer   Words. Nothing changes. "48 shots, 2m 14s."
//   ask      A question WITH OPTIONS. Nothing changes yet. ⭐ See below.
//   plan     An `EditPlan`, previewed, applied only when the user presses Apply.
//
// ---------------------------------------------------------------------------
// ⚠ `ask` IS THE WHOLE REASON THIS FILE EXISTS.
// ---------------------------------------------------------------------------
// Every competitor in this category guesses. Descript's Underlord, Premiere's
// AI Assistant, CapCut, VEED and ChatCut all take a sentence and act on it; if
// the guess is wrong you type again, and on a paid pass you have already been
// billed for the wrong answer. Asked for outright: *"if it unsure about anything
// give us the options and ask if not these then what"*.
//
// So `ask` is a first-class reply kind rather than a paragraph of prose ending
// in a question mark. The difference is not cosmetic:
//
//   - The panel draws real buttons, so answering is one click, not a sentence.
//   - `allow_other` is ALWAYS true, which is the "if not these then what" half.
//     A closed list of options is a form, and this is a conversation.
//   - Nothing is applied, so a wrong guess costs nothing to correct.
//
// ---------------------------------------------------------------------------
// ⚠ AN ANSWER TO AN `ask` GOES BACK AS AN ORDINARY USER MESSAGE.
// ---------------------------------------------------------------------------
// There is no "pending question" record on the server and no session state. The
// browser owns the transcript and posts the whole thing every turn — the same
// decision `server/script_chat.py` documents and for the same reason. Clicking
// option B appends the text of option B to the transcript, exactly as if it had
// been typed. That is what keeps the route stateless, what makes the scrollback
// an honest record of what was agreed, and what lets the user scroll up and see
// WHY the AI did what it did.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE — no React, no DOM, no editor import.
// ---------------------------------------------------------------------------
// Same rule as `actions.js`, `capabilities.js` and `plan_schema.js`: a test has
// to be able to load it under node, and `tests/editor_chat_contract_check.py`
// does. The half most worth testing is the coercion below, where a model that
// answers with something almost-right is turned into a usable turn instead of
// an exception in the middle of somebody's conversation.

import { PLAN_VERSION, validatePlan } from "./plan_schema.js";
// ⚠ THE SOUND PASS'S OWN CEILINGS, READ RATHER THAN RESTATED. `MAX_SFX_SOUNDS`
// and `MAX_SFX_CLIPS` exist because the Freesound budget is 60 requests a
// minute for the WHOLE deployment; a second copy of those numbers here would
// be a preview promising more cues than the pass will actually fetch.
import { MAX_SFX_CLIPS, MAX_SFX_SOUNDS, cueKey } from "./sound_pass.js";

/** The three kinds of reply. Anything else is coerced to `answer`. */
export const TURN_KINDS = ["answer", "ask", "plan"];

/**
 * HOW MANY OPTIONS AN `ask` MAY OFFER, AND WHY IT IS FOUR.
 *
 * ⚠ TWO IS THE FLOOR BECAUSE ONE OPTION IS NOT A QUESTION. A model that comes
 * back with a single choice has decided, and dressing that up as a question
 * wastes a turn — so a one-option ask is folded into a plain `answer` below
 * rather than drawn as a chip nobody has a reason not to click.
 *
 * ⚠ FOUR IS THE CEILING BECAUSE THE FIFTH CHIP WRAPS. The panel is a docked
 * rail, and a question that arrives as a scrolling list of choices is the form
 * this feature exists to avoid. Extra options are DROPPED, not rejected — see
 * the drop rule below.
 */
export const MIN_OPTIONS = 2;
export const MAX_OPTIONS = 4;

/** Caps on what one turn may put on screen. Long enough to be useful, bounded. */
export const MAX_REPLY_CHARS = 1200;
export const MAX_QUESTION_CHARS = 300;
export const MAX_OPTION_LABEL_CHARS = 60;
export const MAX_OPTION_NOTE_CHARS = 120;

/**
 * THE THREE THINGS THAT MAKE THE AI ASK INSTEAD OF ACT.
 *
 * ⚠ THIS LIST IS ALSO IN THE SYSTEM PROMPT (`prompts.yaml`), AND IT HAS TO BE.
 * The prompt is what makes the model ask; this is what the panel and the tests
 * can name. Exported so `tests/editor_chat_ask_check.py` asserts against the
 * same three ids the prompt teaches, rather than a second copy of them.
 *
 * ⚠ AND THERE IS A FOURTH RULE THAT IS NOT HERE: *do not ask when the user was
 * clear*. It is not a reason, it is the absence of one, and a bot that opens a
 * question box on "add a dissolve to every cut" is a bot people stop opening.
 */
export const ASK_REASONS = [
  // The user named a change but not what it lands on. "add text" — on which shot?
  "target",
  // The next step would spend money or quota. ALWAYS asked, however clear the
  // sentence was. See `PAID_INTENT` in the runner.
  "spend",
  // The next step removes or overwrites something that exists.
  "destructive",
];

const isReason = (value) => ASK_REASONS.includes(String(value || "").trim());

// ------------------------------------------------------------------ readers
// Small, boring coercions. Each returns a clean value or "" / undefined, and
// NONE of them raise: the caller is holding a model's output, and a chat that
// throws on a malformed reply is a chat that loses the user's message with it.

const str = (value, limit) => {
  const s = typeof value === "string" ? value : value == null ? "" : String(value);
  const clean = s.replace(/\s+/g, " ").trim();
  return limit ? clean.slice(0, limit) : clean;
};

/** Multi-line kept — a reply is prose and may legitimately have paragraphs. */
const prose = (value, limit) => {
  const s = typeof value === "string" ? value : value == null ? "" : String(value);
  const clean = s.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  return limit ? clean.slice(0, limit) : clean;
};

/**
 * One option on an `ask`, cleaned. Returns null when there is nothing to draw.
 *
 * ⚠ THE ID IS OURS, NOT THE MODEL'S. Whatever it called the option is replaced
 * by its position (`o1`, `o2`, …) in `normaliseAsk`. Two options that came back
 * with the same id would otherwise collide as React keys and as the thing the
 * click handler looks up — and a model repeating an id is exactly the sort of
 * small mistake this file exists to absorb rather than surface.
 */
function readOption(raw) {
  const row = raw && typeof raw === "object" ? raw : { label: raw };
  const label = str(row.label ?? row.text ?? row.title, MAX_OPTION_LABEL_CHARS);
  if (!label) return null;
  return {
    label,
    // The half-line under the chip that says what this choice would MEAN. It is
    // what turns three nouns into a decision somebody can actually make —
    // "Dhol / festive" tells you nothing next to "celebration mood".
    note: str(row.note ?? row.why ?? row.hint, MAX_OPTION_NOTE_CHARS),
  };
}

/**
 * The `ask` half of a turn, cleaned. Returns null when it is not a real ask.
 *
 * ⚠ AN ASK THAT LOSES ITS OPTIONS BECOMES AN ANSWER, NOT AN ERROR. The question
 * still has words in it and those words are worth showing; what is gone is the
 * one-click shortcut. Silently degrading to prose keeps the conversation alive,
 * which is always better than a red box telling the user their own message
 * produced a malformed reply they cannot do anything about.
 */
function normaliseAsk(raw, drops) {
  const row = raw && typeof raw === "object" ? raw : {};
  const question = str(row.question ?? row.prompt, MAX_QUESTION_CHARS);
  if (!question) {
    drops.push({ what: "ask", why: "The question was empty." });
    return null;
  }

  const seen = new Set();
  const options = [];
  for (const item of Array.isArray(row.options) ? row.options : []) {
    const option = readOption(item);
    if (!option) continue;
    // ⚠ DE-DUPED ON THE LABEL, because that is what the user reads. Two chips
    // saying the same words are two chips that cannot be told apart, whatever
    // the model meant by them.
    const key = option.label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    if (options.length >= MAX_OPTIONS) {
      drops.push({ what: "ask", why: `More than ${MAX_OPTIONS} options — the rest were dropped.` });
      break;
    }
    options.push({ ...option, id: `o${options.length + 1}` });
  }

  if (options.length < MIN_OPTIONS) {
    drops.push({
      what: "ask",
      why: `An ask needs at least ${MIN_OPTIONS} options — shown as a plain reply instead.`,
    });
    return null;
  }

  return {
    question,
    options,
    // ⚠ NOT READ OFF THE MODEL, AND NOT A SETTING. "If not these then what" is
    // the point of the feature; a model that came back with `allow_other: false`
    // would be closing the door this was built to open. It is a constant that
    // lives in the shape so the panel has one place to read it from.
    allow_other: true,
    reason: isReason(row.reason) ? str(row.reason) : "",
  };
}


/**
 * THE `sound` HALF OF A TURN, checked against the real film.
 *
 * ⚠ **SOUND IS NOT A VERB AND CANNOT BE ONE.** Every entry in `ACTIONS` is
 * synchronous — it calls one editor function and returns — and finding a sound is
 * a round trip to a stock library. So a turn carries its sound BESIDE its steps,
 * and the runner does the fetching after the steps have finished moving the shots
 * the cues land on. That ordering is not a convenience: a cue lands on a MOMENT,
 * and `set_shot_duration` moves every moment after it.
 *
 * ⚠ **A CUE FOR A SHOT THAT DOES NOT EXIST IS DROPPED, LOUDLY.** Same rule as a
 * step: what survives is exactly what will be fetched, and what did not is on
 * screen beside it.
 *
 * ⚠ **AND THE CEILINGS ARE ENFORCED HERE, NOT ONLY IN THE PASS.** `sfxCues`
 * would refuse the eleventh distinct sound anyway — but it does so when Apply is
 * pressed, long after the preview has told the user it would fetch fourteen.
 * The number on screen has to be the number that happens.
 */
function normaliseSound(raw, ctx, drops) {
  const row = raw && typeof raw === "object" ? raw : {};
  const shots = (ctx && ctx.frames) || [];

  const sfx = [];
  const keys = new Set();
  const onShot = new Set();
  for (const item of Array.isArray(row.sfx) ? row.sfx : []) {
    const cue = item && typeof item === "object" ? item : {};
    const query = str(cue.query, MAX_OPTION_LABEL_CHARS);
    const shot = Math.round(Number(cue.shot));
    if (!query || !Number.isFinite(shot)) continue;
    if (shot < 1 || shot > shots.length) {
      drops.push({ what: "sound", why: `there is no shot ${cue.shot} to sound` });
      continue;
    }
    // ⚠ ONE CUE PER SHOT. Three sounds on one shot is three files starting at the
    // same instant, which is noise rather than sound design.
    if (onShot.has(shot)) {
      drops.push({ what: "sound", why: `shot ${shot} already has a sound cued` });
      continue;
    }
    const key = cueKey(query);
    if (!key) continue;
    if (!keys.has(key) && keys.size >= MAX_SFX_SOUNDS) {
      drops.push({
        what: "sound",
        why: `“${query}” — one pass fetches at most ${MAX_SFX_SOUNDS} different sounds`,
      });
      continue;
    }
    if (sfx.length >= MAX_SFX_CLIPS) {
      drops.push({ what: "sound", why: `past the ${MAX_SFX_CLIPS}-cue ceiling` });
      continue;
    }
    keys.add(key);
    onShot.add(shot);
    sfx.push({ shot, query });
  }

  // ⚠ ONE BED, NEVER A LIST. The mix ducks one bed under speech; two over one
  // film is not a thing anybody asked for.
  let music = null;
  const bed = row.music && typeof row.music === "object" ? row.music : null;
  const bedQuery = bed ? str(bed.query, MAX_OPTION_LABEL_CHARS) : "";
  if (bedQuery) music = { query: bedQuery, mood: str(bed.mood, 40) };

  if (!sfx.length && !music) return null;
  return { sfx, music };
}

/**
 * ONE REPLY FROM THE MODEL, TURNED INTO SOMETHING THE PANEL CAN DRAW.
 *
 * ⚠ IT RETURNS A TURN AND A LIST OF DROPS. IT NEVER THROWS. Same contract as
 * `validatePlan`, and for the same reason: what survived is exactly what the
 * user will see, and what did not is reported beside it rather than swallowed.
 *
 * ⚠ THE KIND IS DECIDED BY WHAT IS ACTUALLY THERE, not by what the model called
 * it. A reply labelled `plan` whose every step was dropped is not a plan — it is
 * an answer with an apology attached, and drawing an Apply button over zero
 * edits is the worst kind of lie a panel can tell. So the label is a hint and
 * the CONTENT is the decision.
 *
 * @param {object} raw    what came back from `/editor-chat/{id}/turn`
 * @param {object} caps   the capability manifest, from `capabilities()`
 * @param {object} ctx    the read-model, for `validatePlan` — plans are checked
 *                        against the real project, never just against themselves
 * @returns {{turn: object, drops: Array<{what: string, why: string}>}}
 */
export function normaliseTurn(raw, caps, ctx) {
  const drops = [];
  const row = raw && typeof raw === "object" ? raw : {};
  const reply = prose(row.reply ?? row.text ?? row.message, MAX_REPLY_CHARS);
  const wanted = TURN_KINDS.includes(row.kind) ? row.kind : "";

  // ⚠ READ BEFORE THE BRANCHES, because sound alone is a plan. "Put some music
  // under it" produces no steps at all — every edit it makes is a clip the sound
  // pass lays down — and treating that as an `answer` would draw a chat bubble
  // where an Apply button belongs.
  const sound = normaliseSound(row.sound, ctx, drops);

  // ----------------------------------------------------------------- an ask
  // ⚠ TESTED FIRST, BECAUSE ASKING MEANS NOTHING HAPPENS YET. A reply that
  // carries both a question and an edit is a model hedging; honouring the
  // question is the safe reading and the one this feature exists for.
  if (wanted === "ask" || (!wanted && row.ask)) {
    const ask = normaliseAsk(row.ask, drops);
    if (ask) {
      return {
        turn: {
          kind: "ask",
          // ⚠ THE REPLY IS OPTIONAL HERE AND THE QUESTION IS NOT. If the model
          // sent only a question, the question IS the bubble — repeating it as
          // prose above its own chips is the thing that makes assistants feel
          // padded.
          reply,
          ask,
        },
        drops,
      };
    }
    // No usable options. Fall through: it may still have sent an edit.
  }

  // ---------------------------------------------------------------- a plan
  // ⚠ THE LABEL DOES NOT GATE THIS, AND THAT IS THE FIX FOR A REAL BUG. The
  // condition used to be `wanted === "plan" || (!wanted && …)`, so a model that
  // sent a music cue under `kind: "answer"` — which is exactly what one does when
  // it thinks of "add music" as answering rather than editing — produced a chat
  // bubble and NO Apply button. The user was told what would happen and given no
  // way to make it happen. The header of this file already said the content is
  // the decision; this line now agrees with it.
  if (wanted === "plan" || row.plan || sound) {
    // ⚠ `caps` SECOND, `ctx` THIRD — the argument order is `validatePlan`'s, not
    // the one that reads best here. Getting it the other way round validates a
    // plan against a manifest that is really a project and drops every step with
    // an unhelpful reason, which is a very quiet way to break this whole panel.
    const { plan, dropped } = validatePlan(row.plan, caps, ctx);
    for (const d of dropped || []) {
      drops.push({
        what: "step",
        why:
          typeof d === "string"
            ? d
            : `${d?.verb ? `${d.verb}: ` : ""}${d?.why || "dropped"}`,
      });
    }
    if ((plan && (plan.steps || []).length) || sound) {
      return {
        turn: {
          kind: "plan",
          // ⚠ A PLAN STILL CARRIES WORDS. The table says what will happen; the
          // reply says why. A preview with no sentence over it reads as a
          // machine's output rather than an answer to what was asked.
          reply: reply || plan?.summary || "Here's what I'd change — nothing has happened yet.",
          // ⚠ A SOUND-ONLY TURN STILL GETS A PLAN OBJECT, empty steps and all.
          // Everything downstream — the preview, the apply, the log — reads
          // `turn.plan.steps`, and a null there would be a second shape for the
          // same thing.
          plan: plan || { version: PLAN_VERSION, summary: "", mood: "", include: {}, steps: [] },
          sound,
        },
        drops,
      };
    }
    // Every step went. Fall through: whatever prose came with it is the honest
    // answer, and the drops explain themselves underneath.
    drops.push({ what: "plan", why: "No usable edits were left in the plan." });
    return {
      turn: {
        kind: "answer",
        reply:
          reply ||
          "I couldn't turn that into an edit this project can take. Tell me a bit more?",
      },
      drops,
    };
  }

  // -------------------------------------------------------------- an answer
  // An `ask` whose options were all dropped lands here, and its question is the
  // best words available.
  if (!reply && row.ask) {
    return {
      turn: {
        kind: "answer",
        reply: str(row.ask.question, MAX_REPLY_CHARS) || "Could you say a bit more?",
      },
      drops,
    };
  }
  if (!reply) {
    drops.push({ what: "reply", why: "The model returned nothing to show." });
    return {
      turn: { kind: "answer", reply: "I didn't catch that — could you say it again?" },
      drops,
    };
  }
  return { turn: { kind: "answer", reply }, drops };
}

/**
 * The text an option becomes when it is clicked.
 *
 * ⚠ IT IS THE USER'S MESSAGE, NOT A COMMAND. It goes into the transcript as
 * something a person said, because that is what it is — and because the next
 * turn re-posts the whole transcript, this sentence is all the memory the server
 * needs of the question that was asked. Keeping the question in it is what makes
 * the scrollback readable a week later.
 */
export function answerText(ask, option) {
  const question = str(ask?.question, MAX_QUESTION_CHARS);
  const label = str(option?.label, MAX_OPTION_LABEL_CHARS);
  if (!label) return "";
  return question ? `${question} — ${label}` : label;
}

/**
 * WHAT GETS POSTED AS THE TRANSCRIPT, TRIMMED.
 *
 * ⚠ ONLY `role` AND `text` CROSS THE WIRE. A turn in the panel also carries its
 * plan, its drops, whether it was applied and when — none of which the model
 * needs and all of which would be tokens on every subsequent turn. A 48-step
 * plan re-sent ten times is the difference between a cheap conversation and an
 * expensive one, and it buys the model nothing it cannot see in the read-model
 * it is handed fresh each turn.
 *
 * ⚠ AND THE TRIM IS FROM THE FRONT. The newest turns are the ones the next reply
 * depends on; the oldest are the ones the project state has already superseded.
 */
export function wireMessages(messages, keep = 20) {
  const rows = [];
  for (const m of Array.isArray(messages) ? messages : []) {
    const text = prose(m?.text, MAX_REPLY_CHARS);
    if (!text) continue;
    const role = m?.role === "user" ? "user" : "agent";
    rows.push({ role, text });
  }
  return rows.slice(-Math.max(2, keep));
}

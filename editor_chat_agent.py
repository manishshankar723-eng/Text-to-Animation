"""editor_chat_agent.py — the ✨ AI Editor chat's brain. One turn in, one turn out.

    chat(messages=…, board=…, vocabulary=…, settings=…) -> {kind, reply, ask, plan}

---------------------------------------------------------------------------
⚠ **IT IS STATELESS, LIKE `script_agent.chat`.**
---------------------------------------------------------------------------
The browser owns the transcript and posts the whole thing every turn. Nothing is
written to a job, a draft or a collection. Same decision `server/script_chat.py`
spells out, and the reason is stronger here: the thing the conversation is about
is the TIMELINE, which already has a durable home and is re-sent fresh every
turn. A remembered conversation plus a re-sent document is two sources of truth
about the same film, and the remembered one is always the stale one.

---------------------------------------------------------------------------
⚠ **IT MAKES NO EDIT, AND IT COULD NOT.**
---------------------------------------------------------------------------
What comes back is DATA. It crosses to the browser, goes through `normaliseTurn`
→ `validatePlan` → `useDirectorRun`, and the user presses Apply. Nothing in this
module — or in the route that calls it — can move a clip. Exactly the rule
`server/director.py` opens with, and it is why the chat was safe to point at a
real project on day one.

---------------------------------------------------------------------------
⚠ **THE VERB SCHEMA IS THE DIRECTOR'S, NOT A SECOND COPY.**
---------------------------------------------------------------------------
`director.plan_schema` and `director.fold_steps` are imported and used as they
are. A `plan` from this chat is the same shape as a plan from 🎬 Make Video,
because it runs through the same registry on the other side — and a second
description of what `add_transition` takes would be a description that goes
stale the first time a verb gains an argument.

⚠ **WHAT IS NOT SHARED IS THE PROMPT.** The Director reads a whole board once and
answers with a whole plan. This answers a person mid-sentence, and most of the
time the right answer is a QUESTION. See the `editor_chat:` block in
`prompts.yaml` for the three ask triggers and, more importantly, for the rule
about when NOT to ask.

⚠ **TOKENS ARE NOT COUNTED HERE, AND THAT IS A KNOWN GAP.** `llm_json` does not
report usage — the Director has the same hole — so what the route records is
TURNS, which is the unit the tier actually sells (`limits["chat_turns"]`). If
`llm_json` learns to report usage, this is where it would be merged in.
"""

import json
import logging
import os
from typing import Any

import yaml

# Which set of provider/model/key settings this agent's calls run on:
# `CHAT_PROVIDER`, `CHAT_MODEL`, `GEMINI_KEY_CHAT`. ⚠ A KEY OF `llm_json.CAPABILITIES`
# — the name is the whole wiring, so it lives in one place and both this module
# and the router that reports the provider back to the browser read it from here.
CAPABILITY = "chat"

logger = logging.getLogger(__name__)

PROMPTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.yaml")


class EditorChatError(Exception):
    """A turn that could not be produced, with a reason written for a person."""


# ---------------------------------------------------------------------------
# Limits on what one turn may carry
# ---------------------------------------------------------------------------
# ⚠ THESE BOUND THE PROMPT, WHICH IS THE BILL. The admin's `shot_detail_limit`
# decides how many shots are described in full; these decide how long each
# description may be. A 400-word shot note pasted into a board is one clip's
# worth of prose and thirty of them is the whole budget.
SHOT_LABEL_CHARS = 60
SHOT_TEXT_CHARS = 180
MAX_MESSAGE_CHARS = 2000
MAX_REPLY_CHARS = 1200

# ⚠ THE SOUND CEILINGS ARE THE SOUND PASS'S, NOT NEW ONES. `sound_pass.js`
# already refuses past `MAX_SFX_SOUNDS` (10 distinct sounds) and
# `MAX_SFX_CLIPS` (32 placements) because the Freesound budget is 60 requests a
# minute for the WHOLE deployment, shared. These are a first cut on the way in
# so a model asking for two hundred cues does not put two hundred cues in a
# preview the client will then refuse — the honest number has to be the one on
# screen. Deliberately looser than the client's, which stays the real limit.
MAX_SFX_CUES = 32

# ---------------------------------------------------------------------------
# THE PAID DOORS — the four things in this editor that cost money
# ---------------------------------------------------------------------------
# ⚠ **THE CHAT NAMES A DOOR; IT NEVER OPENS ONE AND CANNOT.** Nothing in this
# feature spends: `/editor-chat/{id}/turn` spends text quota and hands back a
# proposal. What an offer buys the user is the BUTTON — the chat says a voiceover
# would help and the panel puts a real one in front of them, wired to the very
# same priced confirm that ✨ Animate and 🎙 Voiceover already go through.
#
# ⚠ **AND THAT IS WHY THERE IS NO PRICE IN HERE.** A number quoted by the chat
# would be a second opinion about money, computed from a board the browser sent,
# next to a door that asks the server. The first time the two disagreed the user
# would be right to stop trusting both. The door quotes; the chat points.
#
# ⚠ **MIRRORED IN `PAID_DOORS` IN `client/…/agent/chat_turn.js`**, the same way
# `ASK_REASONS` is, and `tests/editor_chat_doors_check.py` asserts the two lists
# agree — so a door renamed on one side is a test failure, not a silent drift.
PAID_DOORS = ("voiceover", "captions", "veo", "images")

# ---------------------------------------------------------------------------
# LOOKING — the one thing this chat could never do
# ---------------------------------------------------------------------------
# ⚠ **THE CHAT USED TO BE BLIND, AND SAID SO ON EVERY TURN.** It is handed
# labels, descriptions, dialogue and durations, so *"mera video analyse karo aur
# bekar part cut karo"* could only ever be answered halfway: it can find the
# SILENCE off the waveform (free, already measured in the browser) and it cannot
# find the boring shot, because it has never seen one.
#
# So a turn may come back as `kind: "look"` — *"let me see shots 3 to 9"* — and
# the browser answers it by sending those shots' pictures with the SAME message
# again. The model then answers a question about a film it has actually watched.
#
# ⚠ **IT IS A REQUEST, NOT A SETTING, AND THAT IS THE WHOLE COST CONTROL.** Every
# picture on every turn would be the single biggest line on the bill for a
# feature whose ordinary turn is "how long is this?" — so nothing is sent until
# the model says it needs to see, and it is told to ask only when the answer
# genuinely depends on what is in frame.
#
# ⚠ **ONE LOOK PER MESSAGE.** The browser honours a look exactly once and the
# prompt tells the model it is already looking, because two models in a row each
# asking for a slightly different set of stills is a loop that spends money.
MAX_LOOK_SHOTS = 12

# ⚠ **A LOOK SENDS THE RULES AS CONTENT, NOT AS A SYSTEM INSTRUCTION, AND THIS
# STRING IS WHY.** Measured, not guessed: the full ~9.8KB system prompt with five
# stills attached took **149 seconds and then failed** — twice, reproducibly —
# while the *same* pictures, the *same* prompt and the *same* response schema
# under a two-line system instruction answered in **7.5 seconds**. Nothing else
# differed. Bisected in that order: images alone are fast (3–5s), the big schema
# with images is fast (5s), a short system with images is fast; only the long
# system instruction *together with* image parts hangs.
#
# ⚠ **SO NOTHING IS DROPPED — IT MOVES.** The rules travel at the top of the
# prompt instead, which is why there is no second, trimmed copy of them anywhere:
# a look that obeyed a shorter rulebook would quietly plan worse than a turn that
# could not see, and the divergence would be invisible until somebody read two
# transcripts side by side.
LOOK_SYSTEM = (
    "You are a film editor sitting beside someone in their video editor, looking "
    "at stills from their own timeline. You answer only in JSON, in the shape you "
    "are given, with no prose around it. YOUR FULL WORKING RULES ARE AT THE TOP OF "
    "THE MESSAGE BELOW — read them and follow them exactly, as if they had been "
    "given to you as instructions."
)

# How many offers one turn may carry. Four doors exist, and a turn that offers
# more than two of them has stopped answering the question and started selling.
MAX_PASSES = 2
WHY_CHARS = 120
SOUND_QUERY_CHARS = 60

_prompt_cache: dict | None = None


def prompts(reload: bool = False) -> dict:
    """The `editor_chat:` block out of prompts.yaml. Cached."""
    global _prompt_cache
    if _prompt_cache is not None and not reload:
        return _prompt_cache
    try:
        with open(PROMPTS_PATH, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except OSError as e:
        raise EditorChatError(f"Could not read {PROMPTS_PATH} ({e}).") from None
    block = config.get("editor_chat") or {}
    missing = [k for k in ("system", "turn") if not (block.get(k) or "").strip()]
    if missing:
        raise EditorChatError(
            f"{PROMPTS_PATH} is missing the editor_chat prompt block(s): {', '.join(missing)}."
        )
    _prompt_cache = {k: str(block[k]).strip() for k in ("system", "turn")}
    return _prompt_cache


def _fill(template: str, values: dict[str, str]) -> str:
    """`<<TOKEN>>` substitution. NOT `str.format` — the prompts contain braces."""
    out = template
    for name, value in values.items():
        out = out.replace(f"<<{name}>>", value)
    return out


def _clip(text: Any, limit: int) -> str:
    out = " ".join(str(text or "").split())
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def _ms(value: Any) -> str:
    """Milliseconds as seconds, the way every other surface in this app reads them."""
    try:
        n = max(0, int(value or 0))
    except (TypeError, ValueError):
        return "0s"
    return f"{n / 1000:.1f}s"


# ===========================================================================
# THE BOARD — the timeline as the model sees it
# ===========================================================================
def board_digest(board: dict, detail_limit: int = 60) -> str:
    """The timeline, as compact prose-and-numbers.

    ⚠ **PROSE, NOT THE RAW DOCUMENT.** The editor's document is deeply nested and
    most of it is render state a model cannot act on. Sending it would spend
    thousands of tokens per turn on keyframe arrays to buy nothing — the model
    edits through VERBS, and a verb takes a shot number, not a clip object.

    ⚠ **AND IT DEGRADES INSTEAD OF TRUNCATING.** Past `detail_limit` shots the
    list stops being per-shot and becomes a count plus the first and last few.
    A 400-shot feature must still be answerable ("how long is this?") without
    putting 400 descriptions in a prompt — and a digest that simply cut off at
    shot 60 would have the model confidently discussing a film that ends there.
    """
    shots = board.get("shots") or []
    total = len(shots)
    existing = board.get("existing") or {}
    # ⚠ THE PICTURE ROWS, BECAUSE A STACKED TIMELINE IS NOT A LIST. Without this
    # block the model reads N shots running one after another and proposes a
    # transition "after shot 21" where shot 21 and shot 22 are playing at the
    # same time on two different rows. See `boardFrom` for the whole story.
    layers = [row for row in (board.get("layers") or []) if isinstance(row, dict)]

    # ⚠ WHAT THE FILM IS, BEFORE WHAT IS ON THE TIMELINE. Filled in by
    # `fill_board_words` on the server from the board this project was made
    # from — the browser has none of it. It is first because it is the question
    # every treatment decision depends on and the one the model used to answer
    # by guessing: handed "Shot 1 … Shot 14" and nothing else, a Diwali puja
    # board came back scored with "mouse click", "digital beep" and a bed of
    # "upbeat energetic corporate pop vlog".
    film = board.get("film") if isinstance(board.get("film"), dict) else {}
    lines: list[str] = []
    if any(film.values()):
        lines.append("WHAT THIS FILM IS:")
        for key, label in (
            ("title", "Called"),
            ("genre", "Kind of film"),
            ("world", "Its world"),
            ("market", "Made for"),
            ("language", "Its language"),
        ):
            value = _clip(film.get(key), 200)
            if value:
                lines.append(f"- {label}: {value}")
        logline = _clip(film.get("logline"), 400)
        if logline:
            lines.append(f"- What it is about: {logline}")
        lines.append("")

    lines += [
        "THE TIMELINE, RIGHT NOW:",
        f"- Title: {_clip(board.get('title'), 120) or '(untitled)'}",
        f"- {total} shot(s), {_ms(board.get('total_ms'))} total, "
        f"{board.get('aspect_ratio') or '?'} at {board.get('fps') or 24}fps",
    ]

    cuts = existing.get("transitionCuts") or []
    lines.append(
        f"- Already on it: {len(cuts)} transition(s)"
        + (f" (after shot {', '.join(str(c) for c in cuts[:12])}"
           + (", …" if len(cuts) > 12 else "") + ")" if cuts else "")
        + f", {existing.get('texts') or 0} text clip(s), "
        f"{existing.get('shapes') or 0} shape(s), "
        f"{existing.get('audioTracks') or 0} audio track(s)"
    )

    if len(layers) > 1:
        lines.append("")
        lines.append(
            f"⚠ THIS FILM IS STACKED — {len(layers)} picture rows play AT THE SAME TIME, "
            "one over another. The shot numbers below run through all of them:"
        )
        for row_info in layers:
            lines.append(
                f"- Layer {row_info.get('layer') or '?'}: "
                f"{_clip(row_info.get('name'), 40) or 'picture'} "
                f"— {row_info.get('shots') or 0} clip(s)"
            )
        lines.append(
            "A CUT ONLY EXISTS BETWEEN TWO CLIPS ON THE SAME ROW. Two shots that are "
            "neighbours in the numbering but sit on different rows have no cut between "
            "them, and a transition there cannot be made."
        )

    def row(i: int, shot: dict) -> str:
        bits = [f"{i}. [{_ms(shot.get('ms'))}]"]
        # Which row this clip is on, when the film has more than one. On a
        # single-row film it is noise on every line.
        if len(layers) > 1 and shot.get("layer"):
            bits.append(f"(L{shot.get('layer')})")
        label = _clip(shot.get("label"), SHOT_LABEL_CHARS)
        if label:
            bits.append(label)
        # WHERE IT HAPPENS, from the panel this frame came from. Short, and in
        # front of the description because a location is the single most useful
        # word there is for deciding what a shot SOUNDS like.
        where = _clip(shot.get("location"), 60)
        if where:
            bits.append(f"({where})")
        body = _clip(shot.get("description"), SHOT_TEXT_CHARS)
        if body:
            bits.append(f"— {body}")
        said = _clip(shot.get("dialogue"), SHOT_TEXT_CHARS)
        if said:
            bits.append(f'· says: "{said}"')
        return " ".join(bits)

    # ⚠ MEASURED IN THE BROWSER AND SENT AS PROSE, not recomputed here. The
    # editor has already decoded every audio upload for its waveforms, so the dead
    # air is a number it holds; asking the server for it would mean re-decoding a
    # file to learn something the client knew. See `client/…/agent/speech.js`.
    sound = _clip(board.get("sound"), 2000) if isinstance(board.get("sound"), str) else ""
    if sound:
        lines.append("")
        # ⚠ The digest is newline-shaped and `_clip` collapses whitespace, so it
        # is taken from the board unflattened — the bullet list is the point.
        lines.append(str(board.get("sound")).strip()[:2000])

    # ⚠ BLINDNESS IS STATED, NOT LEFT TO BE INFERRED — and this line is the whole
    # difference between a wrong answer and a good question. A project made from
    # uploads, or from a board that has since been deleted, genuinely has no
    # words in it: every shot is "Shot 7, 2.0s" and nothing more. A model given
    # that and asked for sound will fill the hole with the average stock video —
    # whooshes, beeps, clicks, "corporate pop vlog" — and sound completely
    # confident doing it. Told that it is blind, it has two honest moves instead,
    # and it has both: `look` at the pictures, or `ask` what the film is.
    described = sum(1 for s in shots if isinstance(s, dict) and str(s.get("description") or "").strip())
    if total and not described and not any(film.values()):
        lines.append("")
        lines.append(
            "⚠ NOTHING HERE SAYS WHAT THIS FILM IS ABOUT. There are no shot "
            "descriptions, no genre and no script — only durations and numbers. "
            "You do NOT know what is in these pictures. Before proposing anything "
            "that depends on the content — sound effects, a music bed, a title, "
            "which shot is dull — either `look` at the shots, or `ask` what the "
            "film is. Guessing produces a soundtrack from somebody else's film."
        )

    lines.append("")
    lines.append("SHOTS (numbered as the person sees them, 1-based):")
    if total <= max(10, detail_limit):
        lines.extend(row(i + 1, s) for i, s in enumerate(shots))
    else:
        head, tail = 6, 4
        lines.extend(row(i + 1, s) for i, s in enumerate(shots[:head]))
        lines.append(
            f"… {total - head - tail} more shots, not listed one by one because this "
            f"film is long. Ask about a range and you will be told about it."
        )
        lines.extend(row(total - tail + i + 1, s) for i, s in enumerate(shots[-tail:]))

    return "\n".join(lines)


def rails_text(settings: dict) -> str:
    """The two safety rails, stated as instructions — or their absence, stated too.

    ⚠ **A RAIL THAT IS OFF MUST STILL BE MENTIONED.** Saying nothing would leave
    the model with the system prompt's "ask every time" and no way to know an
    operator has relaxed it, so it would keep asking and the setting would look
    broken. Both states are written out.
    """
    ask_spend = bool(settings.get("ask_on_spend", True))
    ask_kill = bool(settings.get("ask_on_destructive", True))
    allow_paid = bool(settings.get("allow_paid_passes", False))

    out = ["HOUSE RULES FOR THIS DEPLOYMENT:"]
    if ask_spend:
        out.append(
            "- ALWAYS ask before anything that spends money or quota (video renders, "
            "generated images, spoken voiceover), however clearly it was asked for."
        )
    else:
        out.append(
            "- You need not ask again before a spend the person has already asked for "
            "clearly. The editor still shows them a price before anything is charged."
        )
    if ask_kill:
        out.append(
            "- ALWAYS ask before deleting, trimming away or overwriting something that "
            "already exists. Say what would go, and how much of it."
        )
    else:
        out.append(
            "- You may propose deletions without asking first. The person still reads "
            "the plan and presses Apply, and Revert puts it back."
        )
    if not allow_paid:
        out.append(
            "- This deployment does NOT let the chat start paid work at all. You may "
            "say a render would help; do not offer to start one."
        )
    else:
        # ⚠ "YOU MAY OFFER IT" AND "YOU MAY START IT" ARE DIFFERENT SENTENCES, and
        # only the first one is true. Nothing the chat returns can spend a penny —
        # see the header of `server/editor_chat.py` — so the useful thing it can do
        # is name the door and let the editor price it. Told it may "start" paid
        # work, the model reports a render it has not begun.
        out.append(
            "- You MAY offer paid work (a Veo render, generated pictures, a spoken "
            "voiceover) and you may not start it. Say what it would do, then name the "
            "button: 🎬 Make Video, Voiceover, or 🖼 Animatic images. The editor shows "
            "the price and offers an upgrade if their plan does not cover it — never "
            "quote a price and never guess their tier."
        )
    out.append(
        "- You never report an edit as done. A plan is a proposal until they press Apply."
    )
    return "\n".join(out)


# ===========================================================================
# THE SHAPE OF ONE REPLY
# ===========================================================================
def reply_schema(vocabulary: dict) -> dict:
    """What one turn may be. `plan` is the Director's own schema, not a copy.

    ⚠ **ONLY `kind` AND `reply` ARE REQUIRED.** Making `ask` and `plan` required
    would force the model to send empty ones on every conversational turn, and a
    schema that demands a field the answer does not have is a schema that gets
    filled with plausible rubbish. The client's `normaliseTurn` decides the kind
    from what is actually present anyway — the label here is a hint.
    """
    from director import plan_schema

    return {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["answer", "ask", "plan"],
                "description": "answer = words only. ask = a question with options. "
                               "plan = edits for them to approve.",
            },
            "reply": {
                "type": "string",
                "description": "What you say, in their language. Two or three sentences.",
            },
            "ask": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "enum": ["target", "spend", "destructive"],
                        "description": "Which of the three triggers made you ask.",
                    },
                    "options": {
                        "type": "array",
                        "description": "Two to four real, different answers.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "A few words."},
                                "note": {
                                    "type": "string",
                                    "description": "Half a line: what this would MEAN "
                                                   "for this film.",
                                },
                            },
                            "required": ["label"],
                        },
                    },
                },
                "required": ["question", "options"],
            },
            "plan": plan_schema(vocabulary),
            # ⚠ SOUND IS NOT A VERB AND CANNOT BE ONE. Every verb in the registry
            # is synchronous — it calls one editor function and returns — and
            # finding a sound is a round trip to a stock library. So a turn carries
            # its sound BESIDE its steps, and the client runs the existing sound
            # pass after the steps have finished moving the shots the cues land on.
            # Same order, and for the same reason, as the Director's phases D and E.
            # ⚠ ASKING TO SEE IS A KIND OF TURN, NOT A VERB AND NOT AN EDIT.
            # Nothing happens on the timeline; the browser reads this, fetches
            # those shots' pictures and asks the same question again with them
            # attached. See `MAX_LOOK_SHOTS`.
            "look": {
                "type": "object",
                "description": "Ask to SEE some shots, when the answer really depends on "
                               "what is in frame. Costs the person money, so ask only when "
                               "labels and descriptions genuinely cannot answer it.",
                "properties": {
                    "shots": {
                        "type": "array",
                        "description": "Shot numbers to look at. Fewest that could answer "
                                       f"the question; at most {MAX_LOOK_SHOTS}.",
                        "items": {"type": "integer"},
                    },
                    "why": {
                        "type": "string",
                        "description": "One short line the person will read while they wait.",
                    },
                },
                "required": ["shots"],
            },
            # ⚠ PAID WORK IS AN OFFER, AND IT RIDES BESIDE THE PLAN FOR THE SAME
            # REASON `sound` DOES: it is not a verb. A verb runs inside one React
            # commit and spends nothing; these are server calls with a price on
            # them, and the chat's part is finished when it has named the door.
            "passes": {
                "type": "array",
                "description": "Paid work you are OFFERING, not starting. Use it when the "
                               "person asks for something that costs money or quota. At "
                               "most two, and only when it is really what they asked for.",
                "items": {
                    "type": "object",
                    "properties": {
                        "door": {
                            "type": "string",
                            "enum": list(PAID_DOORS),
                            "description": "voiceover = read the dialogue aloud (this writes "
                                           "the captions too). captions = write captions from "
                                           "audio ALREADY on the timeline, without adding a "
                                           "voice — offer this one when they have a recording "
                                           "of their own. veo = render real footage from a "
                                           "shot. images = draw the key poses for the "
                                           "animatic.",
                        },
                        "why": {
                            "type": "string",
                            "description": "Half a line: what it would do for THIS film. "
                                           "Never a price and never a guess at their plan.",
                        },
                        "shot": {
                            "type": "integer",
                            "description": "Only for `veo`, and only when they named ONE "
                                           "shot. Leave it out for the whole film.",
                        },
                    },
                    "required": ["door"],
                },
            },
            "sound": {
                "type": "object",
                # ⚠ THE FIELD DESCRIPTIONS ARE THE STRONGEST INSTRUCTION THERE IS ON
                # THIS PATH, AND ONE OF THEM WAS ARGUING WITH THE PROMPT. The chat runs
                # on NATIVE structured output (`schema=native` in the llm_json log), so
                # the model is decoding straight into this shape and reads these lines
                # while it fills each field. `sfx` used to say "Sparingly." — written
                # for a model choosing on its own, and quoted at somebody who ASKED.
                # ⚠ Proved live, 2026-09-05: "add music and sound effects in this
                # storyboard story wise" on a 14-shot board returned `music` filled,
                # `sfx: []`, and a `reply` that NAMED the effects it had not sent —
                # "the lighting of the lamps, the rustle of gifts, and the fireworks".
                # The model knew what it wanted; the field told it not to. Lifting the
                # rule in `prompts.yaml` alone did nothing, because this line is closer
                # to the token being written. See RULEBOOK E106 and E123.
                "description": "The sound to fetch and lay down. Search TERMS, not prose.",
                "properties": {
                    "sfx": {
                        "type": "array",
                        "description": "The sound effects to place, one entry per shot "
                                       "that needs one. FILL THIS whenever they asked "
                                       "for sound effects — an empty list is a refusal, "
                                       "and naming the effects in `reply` instead puts "
                                       "nothing on the timeline. If they asked for every "
                                       "shot (\"story wise\", \"each one\"), give every "
                                       "shot an entry. Leave it empty ONLY when they "
                                       "asked for music alone.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot": {"type": "integer"},
                                "query": {
                                    "type": "string",
                                    "description": "Two or three words a stock "
                                                   "library would have tagged.",
                                },
                            },
                            "required": ["shot", "query"],
                        },
                    },
                    "music": {
                        "type": "object",
                        "description": "ONE bed for the whole film, or nothing.",
                        "properties": {
                            "query": {"type": "string"},
                            "mood": {"type": "string"},
                        },
                    },
                },
                # ⚠ `sfx` IS REQUIRED BECAUSE A PROPERTY THIS MODEL IS NOT ASKED FOR
                # IS A PROPERTY IT SIMPLY DOES NOT WRITE. This is the whole bug, and
                # it was proved by changing this one line and nothing else — four
                # live runs on `gemini-3.5-flash`, native structured output, same
                # board, same message:
                #
                #   before   "sound": {"music": {…}}                 — no `sfx` KEY AT ALL
                #   after    "sound": {"sfx": [14 cues], "music": {…}}
                #
                # ⚠ AND EVERY OTHER FIX FOR IT FAILED FIRST, which is why this comment
                # is long. Lifting the "sparingly" rule in `prompts.yaml`, rewriting
                # this field's own description to say "an empty list is a refusal",
                # putting the rule directly under the verb list in the turn prompt,
                # and finally naming the field path in the user's own message — all
                # four left `sfx` empty. Words do not make a model fill a slot the
                # SCHEMA says it may skip.
                #
                # ⚠ IT IS NOT "ALWAYS SEND SOUND": `sound` itself stays optional at the
                # root, so a turn with no sound simply has no `sound` object. This says
                # only that a `sound` object which EXISTS must answer about effects —
                # and `_coerce_sound` still turns an empty list into nothing, so a
                # music-only turn is unharmed.
                #
                # ⚠ `music` IS LEFT OPTIONAL ON PURPOSE. It arrived in all four runs
                # without being asked for, and requiring it would push the model to
                # invent a bed for somebody who asked only for a door slam.
                "required": ["sfx"],
            },
        },
        "required": ["kind", "reply"],
    }


def _transcript(messages: list[dict]) -> str:
    """The conversation, as lines. Roles named the way the prompt refers to them."""
    out = []
    for m in messages:
        who = "THEM" if (m.get("role") or "") == "user" else "YOU"
        text = _clip(m.get("text"), MAX_MESSAGE_CHARS)
        if text:
            out.append(f"{who}: {text}")
    return "\n".join(out)


def _coerce_ask(raw: Any) -> dict | None:
    """Read the `ask` half. Anything unusable becomes None, never an exception.

    ⚠ **THE CLIENT CLEANS THIS AGAIN**, and that is not duplicated work: this side
    decides whether there is an ask at all (so the log and the `kind` are honest),
    and `chat_turn.js` decides what can be DRAWN — de-duping labels, numbering
    ids, capping at four. Neither can do the other's job from where it stands.
    """
    row = raw if isinstance(raw, dict) else {}
    question = _clip(row.get("question"), 300)
    options = []
    for item in row.get("options") or []:
        if isinstance(item, dict):
            label = _clip(item.get("label") or item.get("text"), 60)
            note = _clip(item.get("note") or item.get("why"), 120)
        else:
            label, note = _clip(item, 60), ""
        if label:
            options.append({"label": label, "note": note})
    if not question or len(options) < 2:
        return None
    reason = row.get("reason") if row.get("reason") in ("target", "spend", "destructive") else ""
    return {"question": question, "reason": reason, "options": options[:4]}


def _coerce_sound(raw: Any) -> dict | None:
    """Read the `sound` half. Anything unusable becomes None, never an exception.

    ⚠ **A QUERY IS SEARCH TERMS, AND THE PROMPT SAYS SO TWICE** — models write
    "the gentle sound of a door closing in the distance" when what goes into a
    stock library is "door close". Nothing here can fix that; what it CAN do is
    cap the length, so a sentence at least does not become the whole search.

    ⚠ **ONE MUSIC BED, NEVER A LIST.** `sound_pass.js` places one, the mix ducks
    one under speech, and two beds over one film is not a thing anybody asked for.
    """
    row = raw if isinstance(raw, dict) else {}

    sfx = []
    seen = set()
    for item in row.get("sfx") or []:
        if not isinstance(item, dict):
            continue
        query = _clip(item.get("query"), SOUND_QUERY_CHARS)
        try:
            shot = int(item.get("shot"))
        except (TypeError, ValueError):
            continue
        if not query or shot < 1:
            continue
        # ⚠ ONE CUE PER SHOT. A shot given three sounds is three files stacked at
        # the same instant, which is noise rather than sound design — and it is
        # the shape a model falls into when it is trying to be thorough.
        if shot in seen:
            continue
        seen.add(shot)
        sfx.append({"shot": shot, "query": query})
        if len(sfx) >= MAX_SFX_CUES:
            break

    music = None
    bed = row.get("music")
    if isinstance(bed, dict):
        query = _clip(bed.get("query"), SOUND_QUERY_CHARS)
        if query:
            music = {"query": query, "mood": _clip(bed.get("mood"), 40)}

    if not sfx and not music:
        return None
    return {"sfx": sfx, "music": music}


def chat(
    *,
    messages: list[dict],
    board: dict,
    vocabulary: dict,
    settings: dict | None = None,
    language: str = "",
    pictures: tuple = (),
) -> dict:
    """One conversational turn against a real timeline.

    Args:
        messages: [{role: "user"|"agent", text}, …], oldest first, ending with
            the user's newest. The browser owns this and sends the whole thing.
        board: the read-model, from `boardFrom(ctx)` in the browser.
        vocabulary: the capability manifest, from `capabilities()`.
        settings: the admin panel's row (`server/chat_settings.py`).
        language: the project's language, when it has one.

    Returns:
        {kind, reply, ask, plan, dropped, provider, model}

    Raises:
        EditorChatError: with a reason written for a person.
    """
    from llm_json import JsonRequest, LLMJsonError, complete_json

    settings = settings or {}
    convo = [m for m in (messages or []) if str(m.get("text", "") or "").strip()]
    if not convo:
        raise EditorChatError("Type a message to get started.")
    if (convo[-1].get("role") or "") != "user":
        raise EditorChatError("The last message must be the one you just typed.")

    block = prompts()
    system = block["system"]
    if language.strip():
        # ⚠ A HINT, NOT AN OVERRIDE. The system prompt already says "answer in
        # their language", and what they typed is better evidence than a project
        # setting — somebody can set a film in Hindi and still ask their question
        # in English. This only breaks the tie on turn one, when there is nothing
        # of theirs to read yet.
        system += (
            f"\n\nThis film's language is set to {language.strip()}. Prefer it when "
            "what they typed does not make their own language obvious."
        )

    detail = int(settings.get("shot_detail_limit") or 60)
    prompt = _fill(
        block["turn"],
        {
            "BOARD": board_digest(board or {}, detail),
            "VOCABULARY": json.dumps(
                _vocabulary_for_prompt(vocabulary or {}), ensure_ascii=False, sort_keys=True, indent=1
            ),
            "TRANSCRIPT": _transcript(convo),
            "RAILS": rails_text(settings),
        },
    )

    # ⚠ THE PICTURES ARE ANNOUNCED IN THE PROMPT AS WELL AS ATTACHED. A model
    # handed eight stills and no sentence about them describes them; told which
    # shot each one is, and that it is now looking at the film it was asked
    # about, it answers the question. The order here IS the order they were
    # attached in — see `_coerce_look`, which sorts for exactly this reason.
    if pictures:
        # ⚠ THE RULES BECOME CONTENT AND THE SYSTEM BECOMES TWO LINES. See
        # `LOOK_SYSTEM` — this is the difference between a look that answers in
        # seven seconds and one that hangs for two and a half minutes.
        prompt = (
            "YOUR WORKING RULES — these are your instructions, not background:\n\n"
            + system
            + "\n\n---\n\nTHE JOB:\n\n"
            + prompt
        )
        system = LOOK_SYSTEM
        prompt += (
            "\n\nYOU CAN SEE THESE SHOTS NOW. The pictures attached to this message "
            "are, in order, shot "
            + ", ".join(str(row.get("shot")) for row in pictures)
            + ". Answer from what is actually in them — this is the film they asked "
            "about. ⚠ DO NOT ASK TO LOOK AGAIN: you are already looking, and a second "
            "request costs them money and shows you nothing new. If the pictures still "
            "do not settle it, say what you can see and ask them a plain question."
        )

    request = JsonRequest(
        system=system,
        prompt=prompt,
        schema=reply_schema(vocabulary or {}),
        purpose="editor chat",
        images=tuple(
            {"mime": row.get("mime") or "image/png", "data": row.get("data") or b""}
            for row in (pictures or ())
        ),
        # ⚠ THIS IS WHAT PUTS THE CHAT ON ITS OWN KEY. Everything else about the
        # call is shared with the Director; the one word here is what decides
        # whose credentials and whose bill answer it. See `CAPABILITIES` in
        # `llm_json` — the string must be a key of that table or it silently
        # falls back to the shared text settings.
        capability=CAPABILITY,
        # ⚠ THE OPERATOR'S CLOCK, NOT THE MODULE'S. `llm_json` resolves a budget
        # from the environment for everything else in this app; this one call is
        # governed by a field in the admin panel (`turn_seconds`), because the
        # person who hits the ceiling is the person who owns the deployment and
        # they hit it with a real request — *"sound effects and background music
        # lago pura story pe aur transition and effects ke saath"*, three jobs on
        # a full film, 504. 0 here means "resolve it as usual", which is what a
        # caller with no settings row (a test, the worker) gets.
        budget_seconds=float(settings.get("turn_seconds") or 0),
        budget_source="the admin panel → Chat",
    )

    try:
        raw = complete_json(request)
    except LLMJsonError as e:
        raise EditorChatError(str(e)) from None

    return _read_turn(
        raw,
        vocabulary or {},
        len((board or {}).get("shots") or []),
        # Already looking? Then a second look is refused — see `_read_turn`.
        blind=not pictures,
    )


def _vocabulary_for_prompt(vocabulary: dict) -> dict:
    """The manifest, trimmed to what a CONVERSATION needs.

    ⚠ **THE FULL MANIFEST IS FOR THE SCHEMA, NOT FOR THE PROSE.** `reply_schema`
    is built from all of it, so the model is still constrained to every legal
    verb and argument. What goes in the prompt is the part it has to READ to
    choose between them — and `animatable`, `easings` and the per-effect
    parameter tables are hundreds of tokens the model does not consult when
    deciding whether to answer or ask. Sent on every single turn, that is the
    single biggest avoidable line on the bill.

    ⚠ Deliberately mirrors `director._vocabulary_for_prompt`'s reasoning without
    calling it: that one trims for a PLANNER, which does need the easings.
    """
    def ids(rows, *, with_label=True):
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            if not rid:
                continue
            label = row.get("label") or row.get("hint") or ""
            out.append(f"{rid} ({label})" if with_label and label else str(rid))
        return out

    text = vocabulary.get("text") or {}
    return {
        # ⚠ `label` IS THE GLOSS, and `verbVocab()` calls it that — `{id, label,
        # args, creates}` is the whole shape. `creates` is carried because a verb
        # that makes a clip is the only kind that may name a `ref`, and a planner
        # that does not know which those are writes forward references, which is
        # the commonest fault in a generated plan (see `validatePlan`).
        "verbs": [
            {
                "id": v.get("id"),
                "does": _clip(v.get("label"), 120),
                "args": v.get("args") or [],
                **({"creates": True} if v.get("creates") else {}),
            }
            for v in (vocabulary.get("verbs") or []) if isinstance(v, dict) and v.get("id")
        ],
        # ⚠ THE ONE TABLE THAT KEEPS ITS PROSE, AND IT EARNS THE TOKENS. Every
        # other row here is trimmed to `id (label)` because the model does not
        # need to be told what "bounce" means to choose it. Transitions are
        # different: twelve of them are interchangeable mechanisms until
        # something says what each one MEANS, and given a bare list the model has
        # exactly one safe answer — dissolve, on every cut. That is what shipped.
        # A fourteen-shot Ganesh Chaturthi reel came back with thirteen identical
        # dissolves: *"Dissolve on the cut hi use kar raha hai"*. Twelve short
        # lines is what buys a real choice. See `TRANSITIONS` in `transitions.js`.
        "transitions": [
            {
                "id": row.get("id"),
                "is": _clip(row.get("label") or row.get("id"), 40),
                "does": _clip(row.get("note"), 80),
                "use_when": _clip(row.get("when"), 220),
                **({"directions": row["directions"]} if row.get("directions") else {}),
            }
            for row in (vocabulary.get("transitions") or [])
            if isinstance(row, dict) and row.get("id")
        ],
        "motions": ids(vocabulary.get("motions")),
        "effects": ids(vocabulary.get("effects")),
        "shapes": ids(vocabulary.get("shapes")),
        "audio_transitions": ids(vocabulary.get("audioTransitions")),
        "text_presets": ids(text.get("presets")),
        "text_positions": text.get("positions") or [],
        "text_places": text.get("places") or [],
        "text_backdrops": text.get("backdrops") or [],
        "text_sizes": text.get("sizes") or [],
        "text_aligns": text.get("aligns") or [],
        "transition_ms": vocabulary.get("transitionDurationMs") or {},
        # ⚠ LABELLED "DEFAULTS", BECAUSE THAT IS WHAT THEY ARE. Sent as
        # `house_caps` the model read them as law and quoted "our system has a
        # limit that only allows transitions on up to 35% of the cuts" back at a
        # person who had asked twice for every clip. They are the numbers to use
        # when NOBODY has said — see the restraint rules in `prompts.yaml`.
        "house_defaults_when_not_asked": vocabulary.get("caps") or {},
    }


def _coerce_look(raw: Any, shot_count: int) -> dict | None:
    """A request to see some shots, read into `{shots, why}` or None.

    ⚠ **A SHOT THAT IS NOT THERE IS DROPPED, AND AN EMPTY LIST IS NOT A LOOK.**
    "Show me shots 40-52" on a 27-shot film would otherwise become a look that
    fetches nothing, sends nothing, and comes back as a second identical answer —
    a paid round trip that cannot change anything.

    ⚠ **SORTED AND DEDUPED, because the pictures travel in this order** and the
    model is told which shot each one is. Handed 7, 3, 3, 9 it would be told
    "these are shots 3, 7, 9" over stills in another order, which is worse than
    not looking.
    """
    row = raw if isinstance(raw, dict) else None
    if not row:
        return None
    seen = []
    for value in (row.get("shots") or []) if isinstance(row.get("shots"), list) else []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= max(0, shot_count) and n not in seen:
            seen.append(n)
    if not seen:
        return None
    return {"shots": sorted(seen)[:MAX_LOOK_SHOTS], "why": _clip(row.get("why"), WHY_CHARS)}


def _coerce_passes(raw: Any, shot_count: int) -> list[dict]:
    """The paid doors this turn offers, read into `[{door, why, shot}]`.

    ⚠ **AN UNKNOWN DOOR IS DROPPED, NOT RENAMED.** A model that invents
    `"door": "music"` has offered something this editor has no priced button for,
    and guessing which of the three it meant would put a spend in front of
    somebody for work they did not ask about. Music is free and goes in `sound`.

    ⚠ **AND A SHOT NUMBER IS ONLY KEPT FOR `veo`, AND ONLY IF IT EXISTS.** The
    other two doors are whole-film; a stray `shot` on one of them would have the
    panel offering to render a shot through a button that never renders one. Out
    of range it is dropped rather than clamped — "shot 61 on a 48-shot film" is a
    misunderstanding, and shot 48 is not what was meant.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in (raw or []) if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        door = str(row.get("door") or "").strip().lower()
        if door not in PAID_DOORS or door in seen:
            continue
        seen.add(door)
        offer = {"door": door, "why": _clip(row.get("why"), WHY_CHARS)}
        shot = row.get("shot")
        if door == "veo" and isinstance(shot, int) and 1 <= shot <= max(0, shot_count):
            offer["shot"] = shot
        out.append(offer)
        if len(out) >= MAX_PASSES:
            break
    return out


def _read_turn(raw: dict, vocabulary: dict, shot_count: int = 0, blind: bool = True) -> dict:
    """What came back, read into a turn. Never raises on a shape problem.

    ⚠ **THE KIND IS DECIDED BY WHAT IS THERE.** A reply labelled `plan` with no
    usable steps is an answer; a reply labelled `answer` that came with a real
    question and options is an ask. The label is what the model INTENDED and the
    content is what it managed, and the second is the one a panel can draw. The
    client applies the same rule again for the same reason — see `normaliseTurn`.
    """
    from director import fold_steps

    row = raw if isinstance(raw, dict) else {}
    # ⚠ THE SHOT COUNT IS NEEDED TO CHECK A `veo` OFFER'S SHOT NUMBER, and it is
    # passed rather than inferred: this function is handed the MODEL's answer, and
    # the only honest source for "how many shots are there" is the board the
    # browser sent. Defaulted so every existing caller (and every test) still
    # works — an offer simply loses its shot number rather than the whole turn.
    passes = _coerce_passes(row.get("passes"), shot_count)
    # ⚠ ONLY WHEN IT IS NOT ALREADY LOOKING. `blind` is False on the second call
    # of a look — the pictures are attached — and a model that asks to see again
    # from there is a loop that spends money on every lap. It is refused here
    # rather than trusted to the prompt, because the prompt is a request and this
    # is the only place that can make it a guarantee.
    look = _coerce_look(row.get("look"), shot_count) if blind else None
    reply = _clip(row.get("reply"), MAX_REPLY_CHARS)
    ask = _coerce_ask(row.get("ask"))

    sound = _coerce_sound(row.get("sound"))

    steps, dropped = [], []
    plan_row = row.get("plan")
    if isinstance(plan_row, dict):
        steps, dropped = fold_steps(plan_row.get("steps"), vocabulary)

    # ⚠ A NOTE IS NOT AN EDIT, AND A PLAN OF NOTHING BUT NOTES IS NOT A PLAN.
    # `note` is `run: () => {}` in the verb registry — it moves nothing, by
    # design, so that a real plan can explain itself at the top of the preview.
    # A turn whose ONLY steps are notes, with no `sound` beside them, would
    # change nothing at all, and calling that a plan puts an **Apply 0 edits**
    # button under a sentence claiming the work is already done.
    #
    # ⚠ SEEN LIVE, 2026-09-05, WITH A SCREENSHOT. *"add music and sound effects
    # in this storyboard story wise"* on a fourteen-shot board came back as ONE
    # note — "Adding background music and sound effects to enhance the
    # storyboard's narrative" — no `sound`, under the reply *"I've added a
    # cinematic, storytelling music bed and placed sound effects on key shots"*.
    # Nothing was added. The panel drew "0 edits · Apply 0 edits · Nothing has
    # changed yet", which is the panel being honest about a turn that was not.
    #
    # ⚠ THE CLIENT DECIDES THIS AGAIN AND MUST. `chat_turn.js` states this very
    # rule in its own header — *"drawing an Apply button over zero edits is the
    # worst kind of lie a panel can tell"* — and then tested `steps.length`,
    # which a note passes. Same split as `_coerce_ask` / `normaliseAsk`: this
    # side decides whether there is a plan AT ALL (so `kind` and the log are
    # honest), that side decides what can be DRAWN. Neither may assume the other.
    #
    # ⚠ AND THE NOTES GO IN `dropped`, NOT QUIETLY. What the model wrote is the
    # only evidence the user has that it misunderstood the job, and a turn that
    # silently becomes a chat bubble reads as the model choosing to answer.
    edits = [s for s in steps if (s.get("verb") or "") != "note"]
    if steps and not edits and not sound:
        dropped.append({
            "index": 0,
            "verb": "note",
            "why": (
                f"the plan was {len(steps)} note(s) and nothing else — no edit and "
                "no sound in it, so there was nothing to apply"
            ),
        })
        steps = []

    # ⚠ SOUND ALONE IS STILL A PLAN. "Put some music under it" is a request that
    # produces no steps at all — every edit it makes is a clip the sound pass lays
    # down — and reading that as an `answer` would draw a chat bubble where an
    # Apply button belongs, so the user would have been told what would happen and
    # given no way to make it happen.
    # ⚠ A LOOK IS TESTED FIRST AND WINS, because it means the model has said it
    # cannot answer yet. A reply carrying both a look and a plan is a model
    # hedging, and honouring the look is the reading that ends with a better
    # answer — the plan it wrote blind is the one it was unsure enough about to
    # ask for the pictures.
    if look:
        kind, plan = "look", None
    elif steps or sound:
        kind = "plan"
        summary = plan_row.get("summary") if isinstance(plan_row, dict) else ""
        mood = plan_row.get("mood") if isinstance(plan_row, dict) else ""
        plan = {
            "version": 1,
            "summary": _clip(summary, 200),
            "mood": _clip(mood, 60),
            "steps": steps,
            # ⚠ PASSED THROUGH, NOT DECIDED HERE. Whether the person asked for
            # "every clip" is a reading of what they typed, which is the model's
            # job; what it MEANS is `applyGuardrails`'s. This is the wire between
            # them, and dropping it here was how the whole feature would quietly
            # do nothing.
            "asked_for_all": bool(
                isinstance(plan_row, dict) and plan_row.get("asked_for_all") is True
            ),
        }
    elif ask:
        kind, plan = "ask", None
    else:
        kind, plan = "answer", None

    # ⚠ AN OFFER IS NOT A PLAN AND MUST NOT DRAW AN APPLY BUTTON. There is
    # nothing on the timeline to apply — the whole point is that the spend
    # happens behind a priced door the chat cannot open. So `passes` deliberately
    # does NOT join the `steps or sound` test above: a turn that only offers a
    # voiceover is an `answer` carrying a button.
    if not reply and kind == "answer" and not ask and not passes:
        raise EditorChatError("The model returned an empty reply. Try rephrasing.")

    logger.info(
        "[editor-chat] %s — reply %d chars, %d step(s), %d dropped, %s, %s",
        kind, len(reply), len(steps), len(dropped),
        f"{len(ask['options'])} option(s)" if ask else "no options",
        f"{len(sound['sfx'])} sfx{' + music' if sound.get('music') else ''}"
        if sound else "no sound",
    )
    if passes:
        logger.info(
            "[editor-chat] …offering %s", ", ".join(p["door"] for p in passes)
        )
    if look:
        logger.info(
            "[editor-chat] …asking to LOOK at shot(s) %s",
            ", ".join(str(n) for n in look["shots"]),
        )
    return {
        "look": look,
        "passes": passes,
        "kind": kind,
        "reply": reply,
        "ask": ask,
        "plan": plan,
        "sound": sound,
        "dropped": dropped,
    }

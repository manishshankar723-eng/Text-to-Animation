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
import threading
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

# ⚠ **THE WORDS THAT ASK FOR A VOICE — THE ONE OBJECTIVE TEST UNDER THE
# `voiceover` DOOR.** `asked_for_it` (see `reply_schema`) is the model's own
# account of whether the person asked, and a model that has MISREAD the request
# will report that misreading honestly: the live failure offered a voiceover to
# somebody who asked for captions precisely because it believed that was what
# they wanted. So there is a fact under the judgement. A voiceover adds a VOICE,
# and nobody gets one unless they said so somewhere in the conversation — in
# English or in the roman Hindi half this app's users actually type.
#
# ⚠ IT IS READ OVER EVERYTHING THEY HAVE TYPED, NOT JUST THE LAST LINE, so
# "haan kar do" two turns after "voiceover chahiye" still counts. And it only
# ever fires on a turn that proposes NOTHING ELSE — an offer beside a real plan
# is a suggestion, which is allowed. See `_coerce_passes`.
VOICE_WORDS = (
    "voice", "voiceover", "voice-over", "vo ", "narrat", "aloud", "read it out",
    "read out", "speak", "spoken", "dub", "awaz", "awaaz", "aawaz", "bol",
    "bolke", "bolkar", "sunao", "sunana", "padhkar", "padhke",
)


def _asked_for_a_voice(text: str) -> bool:
    """Did the person anywhere ask to be read aloud? Pure, and deliberately dumb."""
    low = " " + " ".join(str(text or "").lower().split()) + " "
    return any(word in low for word in VOICE_WORDS)

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

# ===========================================================================
# BIG WORK — one message that is really five jobs over sixty shots
# ===========================================================================
# ⚠ **THE PROBLEM IS THE LENGTH OF THE ANSWER, NOT THE DIFFICULTY OF THE JOB.**
# A text model is slowest at WRITING, and "sound effects, music, transitions and
# effects, on the whole film" makes it write a line per shot per job — three
# hundred lines in one answer. That is what produced the live 504 (E142), and
# raising the clock only moves where it breaks: a single HTTP request cannot be
# made reliable at ten minutes, because the browser, the proxy and the load
# balancer all cut it long before the model is finished.
#
# ⚠ **SO THE WORK IS SPLIT AND RUN AT THE SAME TIME — AND THE THINKING IS NOT
# TOUCHED.** This was the operator's own objection, and it was the right one:
# *"1 niyam likhne do to har clip pe dissolve hi laga dega na — magar mujhe to
# chahiye ki do shot ke bich ko samajh kar jo jaruri hai wo lage"*. Exactly so.
# Compressing sixty judgements into one rule would undo the whole of the
# per-cut transition work. What is compressed here is nothing: each batch still
# looks at real shot descriptions and still decides cut by cut. It simply looks
# at TWELVE cuts instead of sixty, and five batches look at their twelve at the
# same time.
#
#   before   1 call  × 300 lines            ≈ the length of the answer
#   after    N calls × 12 lines, together   ≈ the length of the LONGEST answer
#
# ⚠ **AND A SHORTER ANSWER IS A MORE ACCURATE ONE.** `llm_json` already measures
# what long answers cost: they arrive malformed and buy a SECOND paid repair call
# inside the same attempt. Twelve steps is a length this model gets right.
#
# How many shots one batch writes for. Twelve because that is `MAX_LOOK_SHOTS` —
# the number this feature already decided a model can hold in mind at once — and
# because twelve steps is comfortably inside the length that comes back valid.
BATCH_SHOTS = 12
# ⚠ ONE SHOT OF CONTEXT EITHER SIDE, AND IT IS NOT OPTIONAL. A transition lives
# BETWEEN two shots, so the cut at a batch's edge is a decision about a shot the
# batch would otherwise never see — and "I cannot tell what changed here" is
# exactly the case the prompt tells it to answer with a plain cut. Without the
# overlap every twelfth cut would be judged blind.
BATCH_OVERLAP = 1
# How many model calls run at once. ⚠ NOT "all of them": every provider rate
# limits, and forty simultaneous calls is a 429 storm that fails the whole job to
# save a few seconds. Four is fast enough to turn a five-minute job into one
# minute and polite enough to survive a free key.
MAX_PARALLEL_CALLS = 4
# ⚠ THE CEILING ON ONE MESSAGE'S FAN-OUT, and it is a MONEY guard, not a
# performance one. Every batch is a paid call, so an unbounded split turns one
# sentence into a bill nobody approved. Past this the job is refused with a
# sentence that says how to make it smaller — which is an honest answer, and
# quietly doing half the work is not.
MAX_WORK_BATCHES = 48
# At most four jobs in one message. More than that is not a request, it is a
# wish, and the honest reply is to ask which of them matters.
MAX_WORK_TASKS = 4
# ⚠ BELOW THIS, THERE IS NOTHING TO SPLIT. A job of eight steps is one short
# answer — a fan-out would spend an extra planning call and be SLOWER, on top of
# putting a progress bar in front of work that finishes before it is drawn.
WORK_MIN_SHOTS = 14
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
    # ⚠ `batch` IS REQUIRED, NOT OPTIONAL, and it is required here rather than
    # discovered when a big job runs. A missing prompt that only breaks the
    # expensive path breaks it in front of a customer who has already waited;
    # asked for on the first turn of the process, it breaks in the log at boot.
    wanted = ("system", "turn", "batch")
    missing = [k for k in wanted if not (block.get(k) or "").strip()]
    if missing:
        raise EditorChatError(
            f"{PROMPTS_PATH} is missing the editor_chat prompt block(s): {', '.join(missing)}."
        )
    _prompt_cache = {k: str(block[k]).strip() for k in wanted}
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
def board_digest(board: dict, detail_limit: int = 60, window: tuple | None = None,
                 writing: tuple | None = None) -> str:
    """The timeline, as compact prose-and-numbers.

    ⚠ **`window` IS HOW A BATCH SEES ITS OWN SLICE WITHOUT LOSING THE NUMBERS.**
    `(first, last)`, 1-based and inclusive: those shots are listed in FULL and
    every other shot is collapsed to a count. It exists for `run_work`, which
    splits one big job into batches that run at the same time — and the one thing
    a batch must never do is renumber the film. Slicing `board["shots"]` would
    hand batch three a list starting at "1", and every step it wrote would land
    twenty-four shots early. So the window narrows what is DESCRIBED and leaves
    the numbering exactly as the person sees it.

    ⚠ **`writing` IS A DIFFERENT RANGE FROM `window`, AND CONFLATING THEM WROTE
    EVERY EDGE STEP TWICE.** A cut is a decision about two shots, so a batch has
    to SEE one shot past each end — but it must not write for it, or the batch
    next door writes that same cut at the same moment and the person gets two
    transitions on one edit. Caught by the fan-out's own test before it ever ran
    against a model: 36 shots came back with 40 steps and four duplicates.
    Defaults to `window` for a caller that really does own everything it sees.

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
    if window:
        # ⚠ THE BATCH'S OWN SLICE, IN FULL, WITH THE REAL NUMBERS — and the rest
        # of the film named as a count rather than dropped, because "there are 48
        # more shots after this" is what stops a batch treating its last shot as
        # the end of the film and putting a fade-out on it.
        first, last = int(window[0]), int(window[1])
        first, last = max(1, first), min(total, last)
        mine_first, mine_last = (first, last) if not writing else (
            max(first, int(writing[0])), min(last, int(writing[1]))
        )
        if first > 1:
            lines.append(f"… {first - 1} earlier shot(s), handled elsewhere in this same job.")
        for n in range(first, last + 1):
            # ⚠ THE CONTEXT SHOTS ARE MARKED IN THE LIST ITSELF, not only in the
            # sentence below it. A rule stated once at the bottom is a rule about
            # a list; a mark on the row is a fact about that shot, and the row is
            # what the model is looking at when it decides to write a step.
            mark = "" if mine_first <= n <= mine_last else "   ← context only, not yours"
            lines.append(row(n, shots[n - 1]) + mark)
        if last < total:
            lines.append(f"… {total - last} later shot(s), handled elsewhere in this same job.")
        lines.append("")
        lines.append(
            f"⚠ YOU ARE WRITING THE STEPS FOR SHOTS {mine_first}–{mine_last} ONLY. The rows "
            "marked \"context only\" are there so the cuts at your edges make sense — they "
            "belong to another pass that is running right now, and a step on one of them "
            "would be written twice."
        )
    elif total <= max(10, detail_limit):
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
def sound_schema() -> dict:
    """What a `sound` object may be. ⚠ ONE DEFINITION, TWO CALLERS.

    The ordinary turn carries this and so does every batch of a fan-out
    (`batch_schema`). It was written inline in `reply_schema` while there was
    only one caller; a copied second version would be a second answer to
    "what may a cue look like", and the field descriptions below are load
    bearing — this feature has already proved that a `sound` schema which
    lets the model skip `sfx` is a `sound` schema the model skips `sfx` in.
    """
    return {
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
    }


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
                "enum": ["answer", "ask", "plan", "work"],
                "description": "answer = words only. ask = a question with options. "
                               "plan = edits for them to approve. work = a BIG job, "
                               "described in `work` and written by a second pass — "
                               "see that field.",
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
            # ⚠ BIG WORK IS DESCRIBED HERE, NOT WRITTEN OUT. See the BIG WORK
            # block at the top of this module for why. The model's judgement is
            # not being taken away — it is being asked for LATER, per batch, with
            # the real shot descriptions in front of it, instead of all at once
            # in an answer three hundred lines long that arrives malformed or not
            # at all. What goes here is the SHAPE of the job.
            "work": {
                "type": "object",
                "description": (
                    "Use this INSTEAD of `plan` when what they asked for is BIG — more "
                    f"than one kind of edit, or one kind across more than {WORK_MIN_SHOTS} "
                    "shots. Do NOT write the steps here. Name the jobs; each one is then "
                    "written properly, a dozen shots at a time, by a pass that sees the "
                    "same shot descriptions you can see. A progress bar is shown and the "
                    "person can stop it. ⚠ If the job is small, write a normal `plan` "
                    "instead — a fan-out over eight steps is slower, not faster."
                ),
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": (
                            f"One entry per KIND of work, at most {MAX_WORK_TASKS}. "
                            "\"Sound effects, music and transitions\" is THREE tasks, not "
                            "one — they are written at the same time, so splitting them is "
                            "what makes it fast."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "goal": {
                                    "type": "string",
                                    "description": (
                                        "What to do, in a sentence or two, written as an "
                                        "instruction to another editor who can see the "
                                        "shots but has NOT read this conversation. Carry "
                                        "over anything they said that changes the answer — "
                                        "the format (\"reel\", \"shorts\"), the feel, a "
                                        "thing they said to avoid. This sentence is the "
                                        "whole brief; nothing else of theirs is passed on."
                                    ),
                                },
                                "verbs": {
                                    "type": "array",
                                    "description": (
                                        "The verbs this job may use, from the manifest. "
                                        "Keep it to what the job really needs: a transition "
                                        "job that may also delete clips is a job that will."
                                    ),
                                    "items": {"type": "string"},
                                },
                                "sound": {
                                    "type": "boolean",
                                    "description": (
                                        "True for a job that is SOUND — effects or a music "
                                        "bed. Sound is not a verb (see `sound` below), so "
                                        "such a job returns cues rather than steps."
                                    ),
                                },
                                "first_shot": {
                                    "type": "integer",
                                    "description": "First shot this job covers, 1-based. "
                                                   "Leave out for the whole film.",
                                },
                                "last_shot": {
                                    "type": "integer",
                                    "description": "Last shot this job covers, 1-based. "
                                                   "Leave out for the whole film.",
                                },
                            },
                            "required": ["goal"],
                        },
                    },
                },
                "required": ["tasks"],
            },
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
                        # ⚠ **THE FIELD THAT STOPS AN OFFER STANDING IN FOR THE
                        # WORK.** Required, and a boolean, for the reason
                        # `_pin_kind` is an enum: a model cannot leave either
                        # blank, and this is a question it must actually answer
                        # rather than a sentence in a prompt it may skim past.
                        # See `_read_turn` for what FALSE costs an offer.
                        "asked_for_it": {
                            "type": "boolean",
                            "description": "TRUE only when THEY named this paid thing "
                                           "themselves — a voice reading it aloud, "
                                           "rendered footage, drawn poses. FALSE when you "
                                           "are suggesting it beside what they asked for. "
                                           "⚠ A FALSE door is only shown when this same "
                                           "turn also proposes the free work they really "
                                           "asked for — otherwise it is dropped, because "
                                           "words plus a button over a request you could "
                                           "have filled is selling, not editing.",
                        },
                    },
                    "required": ["door", "asked_for_it"],
                },
            },
            "sound": sound_schema(),
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
        # ⚠ THE SAME NUMBER THE MODEL WAS SHOWN IN THE BOARD DIGEST ("N audio
        # track(s)"), so what it was told and what is enforced cannot disagree.
        # A `captions` door over a silent timeline opens a price for work that
        # cannot run — see `_coerce_passes`.
        # ⚠ `-1` WHEN THE BROWSER DID NOT SAY, NOT `0`. "No audio" and "nobody
        # told me" are different facts, and reading the second as the first would
        # silently withdraw the `captions` door from every caller that predates
        # `existing` in the board payload.
        audio_tracks=(
            int(((board or {}).get("existing") or {}).get("audioTracks") or 0)
            if isinstance((board or {}).get("existing"), dict)
            and "audioTracks" in ((board or {}).get("existing") or {})
            else -1
        ),
        # ⚠ EVERYTHING THEY HAVE TYPED, not the last line alone — see
        # `_asked_for_a_voice`. Only their own words count: a voiceover this chat
        # SUGGESTED is not a voiceover they asked for, which is the whole point.
        asked_text=" ".join(
            str(m.get("text") or "") for m in convo if (m.get("role") or "") == "user"
        ),
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


def _coerce_passes(raw: Any, shot_count: int, *, audio_tracks: int = -1,
                   proposes_work: bool = True, voice_asked: bool = True) -> list[dict]:
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

    ⚠ **AN OFFER THEY DID NOT ASK FOR IS DROPPED WHEN THE TURN PROPOSES NOTHING
    ELSE** (`proposes_work=False` and `asked_for_it` not true). Live on
    2026-09-06: *"add caption in my story and text on screen"* on a 14-shot reel
    came back as *"I'll add beautiful on-screen text titles…"* with **no steps at
    all** and a **Voiceover** button — a voice nobody had asked for, standing in
    for `add_text`, which is free and was right there in the vocabulary. *"user
    caption manga hai to voiceover kyun karne ke liye bol raha hai."* An offer
    BESIDE a real plan is still welcome; an offer INSTEAD of one is a sale.

    ⚠ **AND `captions` CANNOT BE OFFERED OVER A SILENT TIMELINE.** That door
    reads audio *already on the timeline* — with `audio_tracks == 0` there is
    nothing for it to read, so the button would open a price for work that cannot
    run. `-1` means "not told", and then nothing is enforced (every existing
    caller and test keeps working).

    ⚠ **NEITHER RULE TOUCHES THE WIRE SHAPE.** `asked_for_it` decides here and
    is not passed on: the offer the panel draws is still `{door, why, shot?}`, so
    `normalisePasses` in `chat_turn.js` stays the mirror it has always been.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in (raw or []) if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        door = str(row.get("door") or "").strip().lower()
        if door not in PAID_DOORS or door in seen:
            continue
        if door == "captions" and audio_tracks == 0:
            continue
        if not proposes_work and door == "voiceover" and not voice_asked:
            continue
        if not proposes_work and row.get("asked_for_it") is not True:
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


def _read_turn(raw: dict, vocabulary: dict, shot_count: int = 0, blind: bool = True,
               audio_tracks: int = -1, asked_text: str = "") -> dict:
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
    # ⚠ THE PASSES ARE READ AT THE BOTTOM OF THIS FUNCTION, NOT HERE, because
    # whether an unasked door may be shown depends on whether this turn proposes
    # any work — which is not known until the steps have been folded and the kind
    # decided. See `_coerce_passes`.
    # ⚠ ONLY WHEN IT IS NOT ALREADY LOOKING. `blind` is False on the second call
    # of a look — the pictures are attached — and a model that asks to see again
    # from there is a loop that spends money on every lap. It is refused here
    # rather than trusted to the prompt, because the prompt is a request and this
    # is the only place that can make it a guarantee.
    look = _coerce_look(row.get("look"), shot_count) if blind else None
    reply = _clip(row.get("reply"), MAX_REPLY_CHARS)
    ask = _coerce_ask(row.get("ask"))

    sound = _coerce_sound(row.get("sound"))
    work = _coerce_work(row.get("work"), shot_count)

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
    # ⚠ A BRIEF ONLY WINS WHEN THE MODEL MEANT IT TO. `work` and `steps` on the
    # same answer is a model hedging, and the two readings are not equal: real
    # steps are work it has already DONE, and running the fan-out as well would
    # do that work a second time and charge for it. So the label decides here and
    # only here — everywhere else in this function the content decides, because
    # everywhere else the two readings cost the same.
    wants_work = str(row.get("kind") or "").strip().lower() == "work"
    if look:
        kind, plan = "look", None
    elif work and (wants_work or not (steps or sound)):
        # ⚠ NOTHING HAS BEEN CALLED YET. This turn is a PLAN TO DO WORK, and the
        # route decides whether to run it here and now or hand it to a job with a
        # progress bar — see `server/editor_chat.py`. The panel must not draw an
        # Apply button over it: there is nothing to apply until it has run.
        kind, plan = "work", None
        steps, sound = [], None
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
    #
    # ⚠ **BUT AN OFFER MAY NOT BE THE WHOLE TURN OVER A REQUEST THIS EDITOR
    # COULD HAVE FILLED.** That is the one thing this turn IS allowed to know
    # about its own doors, and it can only be known here — after the steps are
    # folded and the kind is settled. A turn that proposes real work may suggest
    # whatever it likes beside it; a turn that proposes NOTHING may only offer a
    # door THEY named. See `_coerce_passes` for the live failure.
    proposes_work = bool(steps or sound or work or ask or look)
    # ⚠ DEFAULTS TO TRUE WHEN NOTHING WAS PASSED, so every existing caller and
    # every test keeps the behaviour it had — the floor is opt-in from `chat()`,
    # which is the only place that really knows what the person typed.
    voice_asked = _asked_for_a_voice(asked_text) if asked_text else True
    passes = _coerce_passes(
        row.get("passes"), shot_count,
        audio_tracks=audio_tracks, proposes_work=proposes_work,
        voice_asked=voice_asked,
    )
    # ⚠ **AND WHAT WAS DROPPED IS SAID, NOT SWALLOWED.** Same rule as the
    # notes-only plan above: what the model offered is the only evidence the
    # person has that it misread the job, and a button that quietly disappears
    # leaves them with a reply promising work and no sign of why none came.
    for row_pass in (row.get("passes") or []) if isinstance(row.get("passes"), list) else []:
        if not isinstance(row_pass, dict):
            continue
        door = str(row_pass.get("door") or "").strip().lower()
        if door not in PAID_DOORS or any(p["door"] == door for p in passes):
            continue
        if door == "captions" and audio_tracks == 0:
            dropped.append({
                "index": len(dropped), "verb": door,
                "why": "there is no audio on this timeline to write captions from",
            })
        elif not proposes_work and door == "voiceover" and not voice_asked:
            dropped.append({
                "index": len(dropped), "verb": door,
                "why": "you did not ask for a voice, and no edit was proposed instead",
            })
        elif not proposes_work and row_pass.get("asked_for_it") is not True:
            dropped.append({
                "index": len(dropped), "verb": door,
                "why": "you did not ask for this, and nothing was proposed to go with it",
            })
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
    if work and kind == "work":
        logger.info(
            "[editor-chat] …BIG WORK: %d job(s) — %s",
            len(work["tasks"]),
            "; ".join(
                f"{t['goal'][:40]} (shots {t['first_shot']}–{t['last_shot']})"
                for t in work["tasks"]
            ),
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
        # The brief, when this turn is one. `None` on every ordinary turn, which
        # is what keeps the fast path exactly as fast as it was.
        "work": work if kind == "work" else None,
    }


# ===========================================================================
# THE FAN-OUT — one big job becomes N small ones, run at the same time
# ===========================================================================
def _step_shot(step: dict) -> int | None:
    """Which shot a step lands on, or `None` when it is not about one.

    ⚠ THE ARGUMENT IS NOT ALWAYS CALLED `shot`. A transition goes AFTER a shot,
    a cut happens AT one, and a reorder names an index — every one of them is
    "where on the timeline" and the fan-out has to answer that question to sort
    the merged plan and to refuse a step from outside a batch's range.
    """
    args = step.get("args") if isinstance(step.get("args"), dict) else {}
    for key in ("shot", "after_shot", "at_shot", "index", "from_shot"):
        if key in args:
            try:
                return int(args[key])
            except (TypeError, ValueError):
                continue
    return None


def _coerce_work(raw: Any, shot_count: int) -> dict | None:
    """The model's work brief, made safe. `None` when there is nothing runnable.

    ⚠ **EVERY NUMBER HERE COMES FROM A MODEL AND IS TREATED AS SUCH.** A brief is
    not an edit — nothing it says reaches the timeline without a second pass and
    the person's Apply — but it does decide how many PAID calls get made, so the
    shot range is clamped to the real film and the task count to `MAX_WORK_TASKS`
    before any of it is costed.
    """
    row = raw if isinstance(raw, dict) else {}
    tasks: list[dict] = []
    for item in (row.get("tasks") or [])[:MAX_WORK_TASKS]:
        if not isinstance(item, dict):
            continue
        goal = _clip(item.get("goal"), 600)
        if not goal:
            # ⚠ A TASK WITH NO BRIEF IS NOT A TASK. The goal is the ONLY thing
            # the batch pass is told; without it the call would be a paid request
            # to guess what the person wanted.
            continue
        first = item.get("first_shot")
        last = item.get("last_shot")
        try:
            first = max(1, int(first)) if first is not None else 1
        except (TypeError, ValueError):
            first = 1
        try:
            last = min(shot_count, int(last)) if last is not None else shot_count
        except (TypeError, ValueError):
            last = shot_count
        if last < first:
            first, last = 1, shot_count
        verbs = [
            v for v in (item.get("verbs") or [])
            if isinstance(v, str) and v.strip()
        ][:12]
        tasks.append({
            "goal": goal,
            "verbs": [v.strip() for v in verbs],
            "sound": bool(item.get("sound")),
            "first_shot": first,
            "last_shot": last,
        })
    if not tasks:
        return None
    return {"tasks": tasks}


def work_batches(work: dict, shot_count: int) -> list[dict]:
    """The brief, cut into the units that will really be called. Pure — no model.

    ⚠ **SPLIT HERE AND NOWHERE ELSE**, because this is the list the progress bar
    counts, the ceiling is enforced against, and the tests can check without
    spending a penny. A fan-out whose size is only known once it is running is a
    fan-out nobody can put a number in front of.

    ⚠ **A SOUND TASK IS ONE UNIT, NOT A DOZEN.** Its answer is a short cue per
    shot — two or three words each — so the length that forces the split
    everywhere else simply is not there, and one call keeps the music bed a
    single decision about the whole film instead of five batches each inventing
    their own.
    """
    out: list[dict] = []
    for task in (work or {}).get("tasks") or []:
        first = max(1, int(task.get("first_shot") or 1))
        last = min(shot_count, int(task.get("last_shot") or shot_count))
        if last < first:
            continue
        if task.get("sound"):
            out.append({"task": task, "first": first, "last": last})
            continue
        start = first
        while start <= last:
            stop = min(last, start + BATCH_SHOTS - 1)
            out.append({"task": task, "first": start, "last": stop})
            start = stop + 1
    return out


def _pin_kind(args: dict, rows: list[dict], vocabulary: dict,
              narrowed: bool = True) -> None:
    """Turn the batch's `kind` from a bare string into the real list of ids.

    ⚠ **THIS IS THE THIRD ATTEMPT AT ONE BUG, AND THE FIRST THAT CANNOT LOSE.**
    The two before it were a prompt sentence and an argument filter, and both
    failed for the same structural reason: `args` is a FLAT union of every verb's
    argument names (there are no unions in this schema — see `director.plan_schema`),
    and `add_effect` and `add_transition` BOTH call theirs `kind`. So narrowing
    the arguments to the batch's own verbs removes nothing for exactly the pair it
    needed to separate, and the model — asked for transitions AND effects in one
    pass — filled `kind` on the transitions and left it off every effect.
    Fourteen effects on a live Ganesh Chaturthi reel came back as fourteen rows of
    *"add_effect: the step named no effect to add"* on 2026-09-06, after the same
    thing had already been reported and "fixed" twice (see `director._ARG_ALIASES`).

    An **enum** is what a sentence could not be: a model cannot leave an enumerated
    field blank and cannot invent a value for it. And it is `required` too,
    whenever every verb in the batch takes one — a property a model is not asked
    for is a property it may simply not write, which is the same lesson
    `plan_schema` records about `args` itself.

    ⚠ **THE IDS COME OFF THE MANIFEST THE CLIENT SENT, NEVER OFF A TABLE HERE.**
    Each verb names its own family (`verbVocab()` in `actions.js` — "effects",
    "transitions", "shapes", "motions") and the family is a key on the same
    manifest. A list typed here would go stale the first time an effect is added,
    and it would go stale in the direction that hurts: the model would stop being
    allowed to name the new one. Nothing happens at all if the manifest is older
    than this code and carries no `family` — the schema is simply left as it was.

    ⚠ **A MIXED BATCH GETS THE UNION, AND THAT IS DELIBERATE.** Transitions and
    effects in one pass share the field, so the enum has to hold both; what stops
    a transition id landing on an `add_effect` is the client's own validator,
    which has always been the right place for "that value is wrong for that verb".
    What the enum removes is the failure that had no owner — an EMPTY field.
    """
    props = args.get("properties")
    if not isinstance(props, dict) or "kind" not in props:
        return
    families: list[str] = []
    for row in rows:
        family = str(row.get("family") or "").strip()
        if family and family not in families:
            families.append(family)
    if not families:
        return
    ids: list[str] = []
    per_verb: list[str] = []
    for family in families:
        found = [
            str(item.get("id")).strip()
            for item in (vocabulary.get(family) or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        if not found:
            continue
        for one_id in found:
            if one_id not in ids:
                ids.append(one_id)
        verbs = sorted(r["id"] for r in rows if r.get("family") == family and r.get("id"))
        per_verb.append(f"{'/'.join(verbs)} takes one of: {', '.join(found)}")
    if not ids:
        return
    # The verbs in this batch that take no `kind` at all, named so the model is
    # not left guessing what to do with a field its step has no use for.
    spare = sorted(row["id"] for row in rows if row.get("id") and not row.get("family"))
    props["kind"] = {
        "type": "string",
        "enum": ids,
        "description": "Which one, exactly. " + ". ".join(per_verb) + "."
        + ((" %s take%s no kind — put any value there, it is ignored."
            % ("/".join(spare), "" if len(spare) > 1 else "s")) if spare else ""),
    }
    # ⚠ **REQUIRED WHEN *ANY* VERB IN THIS BATCH TAKES ONE — NOT WHEN EVERY ONE
    # DOES, WHICH IS THE HOLE THE SAME BUG CAME BACK THROUGH.** This line read
    # `all(...)` on the reasoning that a schema demanding a `kind` from a `note`
    # is one the batch "cannot answer". It can, and cheaply: `args` is a FLAT
    # union and `director.fold_steps` filters every step's arguments BY VERB, so
    # a `kind` written on a verb that does not take one is thrown away before it
    # reaches the client and costs nothing. What `all(...)` really did was let
    # ONE companion verb in the brief — a `set_effect_param`, a `note`, anything
    # the planning model listed beside `add_effect` — silently switch the
    # requirement off for the effects as well.
    #
    # Live on 2026-09-06, the THIRD repeat of one bug and the second through its
    # own fix: *"add effects and transition in my story images"* on a 14-shot
    # reel, ten transitions perfect and FOURTEEN rows of *"add_effect: the step
    # named no effect to add"*.
    #
    # ⚠ AND NEVER REQUIRED OF A BATCH THAT WAS NOT NARROWED (`narrowed=False`).
    # A task naming no verbs is choosing from the WHOLE editor, where most verbs
    # have no `kind`; demanding one there would be demanding it of `delete_shot`.
    # Those batches still get the ENUM — see `batch_schema`.
    if narrowed and any(row.get("family") for row in rows):
        required = [name for name in (args.get("required") or []) if name != "kind"]
        args["required"] = [*required, "kind"]


def batch_schema(vocabulary: dict, task: dict) -> dict:
    """What ONE batch may return: steps, or sound cues. Never a conversation.

    ⚠ **THE VERBS ARE NARROWED TO THE TASK'S OWN.** The manifest is the whole
    editor; a batch asked for transitions has no business deleting a clip, and
    the cheapest place to make that true is the schema — a verb that is not in
    the enum is a verb the model cannot decode. `fold_steps` still runs after
    this, so a verb that slips through a provider without enum support is still
    dropped rather than trusted.
    """
    from director import plan_schema

    plan = plan_schema(vocabulary)
    allowed = [v for v in (task.get("verbs") or []) if v]
    steps = ((plan.get("properties") or {}).get("steps") or {})
    item_props = (steps.get("items") or {}).get("properties") or {}
    args = item_props.get("args")
    all_rows = [
        v for v in (vocabulary.get("verbs") or [])
        if isinstance(v, dict) and v.get("id")
    ]
    # Which verbs this batch's `kind` may come out of, and whether the batch was
    # narrowed at all — both read at the bottom, for every batch. See there.
    rows, narrowed = all_rows, False
    if allowed:
        verb = item_props.get("verb")
        known = set(verb.get("enum") or []) if isinstance(verb, dict) else set()
        keep = [v for v in allowed if not known or v in known]
        if keep and isinstance(verb, dict):
            verb["enum"] = keep
        # ⚠ **AND THE ARGUMENTS ARE NARROWED WITH THE VERBS — THIS IS THE HALF THAT
        # WAS MISSING, AND IT COST A WHOLE JOB.** Seen live on the first real run:
        # *"transition and effects ke saath"* on a 14-shot reel came back with the
        # transitions perfect and **every single effect dropped** — eight rows of
        # *"add_effect: the step named no effect to add"*. `args` is a FLAT UNION
        # of every verb's argument names (there are no unions in this schema), so
        # a batch writing transitions AND effects is offered every other verb's
        # argument names beside its own and reaches for the wrong one. Narrowed to
        # what THIS batch's verbs really take, most of those names are not in its
        # schema at all.
        #
        # ⚠ **AND NARROWING IS NOT ENOUGH ON ITS OWN — READ THIS BEFORE YOU TRUST
        # IT.** `add_effect` takes **`kind`**, the very same name `add_transition`
        # takes, so for THESE two verbs narrowing removes nothing and the fix has
        # to live elsewhere. It cost the bug a second time: the batch prompt was
        # written saying *"an `add_effect` needs `effect`"*, which is not a field
        # this editor has, and sixteen effects on a live reel were thrown away
        # again. **Never type an argument name into a prompt or a test** — read it
        # off `verbVocab()` in `client/src/animatic/agent/actions.js`, which is
        # the only place it is real. `director._ARG_ALIASES` is the floor under
        # all of it: a synonym is now renamed rather than dropped.
        if keep:
            rows, narrowed = [v for v in all_rows if v.get("id") in set(keep)], True
        wanted = {name for v in rows for name in (v.get("args") or [])}
        if wanted and isinstance(args, dict) and isinstance(args.get("properties"), dict):
            args["properties"] = {
                name: spec for name, spec in args["properties"].items() if name in wanted
            }
    # ⚠ **PINNED FOR EVERY BATCH, NARROWED OR NOT.** This used to run only
    # inside the narrowing branch above, so a task whose brief listed NO verbs —
    # legal, and what the planning model writes when the goal is broad — was
    # handed `kind` as a bare `{"type": "string"}`: no enum, nothing required,
    # the exact field that has now lost a reel's effects three times. An
    # un-narrowed batch gets the enum and never the requirement.
    if isinstance(args, dict) and isinstance(args.get("properties"), dict):
        _pin_kind(args, rows, vocabulary, narrowed)
    return {
        "type": "object",
        "properties": {
            "steps": plan.get("properties", {}).get("steps", {"type": "array", "items": {}}),
            "sound": sound_schema(),
            "note": {
                "type": "string",
                "description": "At most one short line, ONLY if you did something the "
                               "person would be surprised by — a shot you deliberately "
                               "left alone, say. Usually empty.",
            },
        },
        "required": ["steps"],
    }


def _verb_card(vocabulary: dict, task: dict, shot_count: int = 0) -> str:
    """The batch's OWN verbs, with their exact argument names, on their own line.

    ⚠ **THE MANIFEST ALREADY CARRIES THIS AND THAT WAS NOT ENOUGH.** `<<VOCABULARY>>`
    is the whole editor — every verb in the app — and a batch that has been told
    to do one job has to find its two verbs in fifty. What it reached for instead
    was a name from another verb, or the English word for the thing, and either
    one is dropped and shows up as "the step named no effect to add". Seen live
    on the first real run, eight times in one job, and again on the next build.

    ⚠ **THIS CARD IS THE ONE TRUE COPY, AND IT IS GENERATED.** Every name on it
    comes from the manifest the client sent, so it cannot disagree with the
    validator that will read the step. The prompt around it must therefore never
    name an argument itself — the last time it did it said `add_effect` needs
    `effect`, and it does not; it needs `kind`.

    ⚠ The SCHEMA is what makes that impossible (see `batch_schema`); this is what
    makes it obvious. Both, because a model that is only constrained writes valid
    steps that do the wrong thing, and a model that is only told writes the wrong
    field.
    """
    allowed = {v for v in (task.get("verbs") or []) if v}
    rows = [
        v for v in (vocabulary.get("verbs") or [])
        if isinstance(v, dict) and v.get("id") and (not allowed or v["id"] in allowed)
    ]
    if not rows:
        return "Any verb in the list above."
    lines = []
    for v in rows[:12]:
        args = ", ".join(v.get("args") or []) or "no arguments"
        label = _clip(v.get("label"), 90)
        line = f"· {v['id']}({args})" + (f" — {label}" if label else "")
        # ⚠ **THE LEGAL VALUES FOR `kind`, ON THE VERB'S OWN LINE.** The manifest
        # above carries them, and a batch told to do one job still has to find
        # its family in a document describing the whole editor — which is how
        # fourteen effects were written with no `kind` at all while the
        # transitions in the same pass were perfect (2026-09-06). `_pin_kind`
        # makes that impossible in the SCHEMA; this makes it obvious in the
        # PROSE. Both, and both GENERATED — never type an id or an argument name
        # into this file. See `verbVocab()` in `actions.js`.
        family = str(v.get("family") or "").strip()
        if family:
            ids = [
                str(item.get("id")).strip()
                for item in (vocabulary.get(family) or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            if ids:
                line += "\n    kind must be exactly one of: " + ", ".join(ids)
        # ⚠ A CUT IS NOT A SHOT, AND THE LAST SHOT HAS NO CUT AFTER IT. Live on
        # 2026-09-06: a 14-shot reel asked for a transition on "cut 14", which is
        # past the end of the film, and it was dropped. The batch is told its
        # range in SHOTS, so counting the cuts the same way is the obvious
        # mistake — and here is the only place the real number is known.
        if "cut" in (v.get("args") or []) and shot_count > 1:
            line += (
                f"\n    cut is the JOIN between two shots, so on this {shot_count}-shot "
                f"film it runs 1 to {shot_count - 1} — there is no cut after the last shot"
            )
        lines.append(line)
    return "\n".join(lines)


def _batch_call(*, unit: dict, board: dict, vocabulary: dict, settings: dict,
                language: str) -> dict:
    """One batch: one model call, its own clock, its own slice of the film."""
    from llm_json import JsonRequest, LLMJsonError, complete_json

    task = unit["task"]
    first, last = unit["first"], unit["last"]
    total = len((board or {}).get("shots") or [])
    detail = int(settings.get("shot_detail_limit") or 60)
    # ⚠ THE OVERLAP IS ON THE DIGEST, NOT ON THE RANGE IT IS TOLD TO WRITE FOR.
    # A cut is a decision about two shots, so the batch has to SEE one past each
    # end — and must not write for it, or the same cut is written twice by two
    # batches running at the same time. `board_digest`'s window states both.
    window = None if task.get("sound") else (
        max(1, first - BATCH_OVERLAP), min(total, last + BATCH_OVERLAP)
    )
    # ⚠ SEE ONE PAST EACH END, WRITE FOR NEITHER. See `board_digest`.
    writing = None if window is None else (first, last)
    block = prompts()
    prompt = _fill(
        block["batch"],
        {
            "BOARD": board_digest(board or {}, detail, window, writing),
            "VOCABULARY": json.dumps(
                _vocabulary_for_prompt(vocabulary or {}), ensure_ascii=False,
                sort_keys=True, indent=1,
            ),
            "GOAL": task.get("goal") or "",
            "VERBS": _verb_card(vocabulary or {}, task, total),
            "RANGE": (
                "every shot in this film" if task.get("sound")
                else f"shots {first} to {last}"
            ),
            "LANGUAGE": language.strip() or "the language they wrote in",
        },
    )
    request = JsonRequest(
        system=block["system"],
        prompt=prompt,
        schema=batch_schema(vocabulary or {}, task),
        purpose="editor chat batch",
        capability=CAPABILITY,
        # Each batch gets the operator's full per-message clock. They run at the
        # same time, so the JOB is still about as long as one of them — see E142
        # for why that number is a setting and not a constant.
        budget_seconds=float(settings.get("turn_seconds") or 0),
        budget_source="the admin panel",
    )
    try:
        raw = complete_json(request)
    except LLMJsonError as e:
        raise EditorChatError(str(e)) from None
    return raw if isinstance(raw, dict) else {}


def run_work(*, work: dict, board: dict, vocabulary: dict, settings: dict | None = None,
             language: str = "", on_progress=None, cancelled=None) -> dict:
    """Run a work brief and return ONE turn, exactly shaped like an ordinary one.

    ⚠ **THE ANSWER IS AN ORDINARY PLAN AND THAT IS THE WHOLE POINT.** Everything
    downstream — `normaliseTurn`, `validatePlan`, `applyGuardrails`, the preview,
    Apply, Undo — is untouched and does not know a fan-out happened. A big job is
    not a second kind of edit with a second set of rules; it is the same plan,
    written faster.

    Args:
        on_progress: `f(done, total, message)` after each batch lands. The route
            writes it onto the job record; the panel draws it.
        cancelled: `f() -> bool`, asked BEFORE each batch is started. Stop cannot
            un-send a call already in flight — it stops the SPEND on everything
            after it, which is what the person is actually asking for.

    Raises:
        EditorChatError: only when nothing usable came back at all.
    """
    from concurrent.futures import ThreadPoolExecutor
    from director import fold_steps

    settings = settings or {}
    shots = (board or {}).get("shots") or []
    units = work_batches(work or {}, len(shots))
    if not units:
        raise EditorChatError("There was nothing to do in that.")
    if len(units) > MAX_WORK_BATCHES:
        raise EditorChatError(
            f"That is {len(units)} passes over this film in one message, which is more "
            "than one request should cost. Ask for one kind of change at a time, or "
            "name a range of shots."
        )

    total = len(units)
    done = 0
    lock = threading.Lock()
    steps: list[dict] = []
    dropped: list[dict] = []
    sfx: list[dict] = []
    music: dict | None = None
    failures: list[str] = []
    # ⚠ THE UNITS THAT FAILED, NOT JUST THE COUNT — they are tried once more when
    # the parallel wave is over. See the retry block below.
    lost_units: list[tuple[dict, str]] = []
    stopped = False

    def one(index: int, unit: dict, retry: bool = False):
        nonlocal done, music, stopped
        if cancelled and cancelled():
            with lock:
                stopped = True
            return
        label = _clip(unit["task"].get("goal"), 60)
        try:
            raw = _batch_call(
                unit=unit, board=board, vocabulary=vocabulary,
                settings=settings, language=language,
            )
        except EditorChatError as e:
            # ⚠ ONE BATCH FAILING IS NOT THE JOB FAILING. Fifty-eight good steps
            # and one timed-out batch is a real, useful, honest plan; throwing it
            # all away would charge the person for every call and hand them
            # nothing. What was missed is named in the reply instead.
            with lock:
                if retry:
                    failures.append(f"shots {unit['first']}–{unit['last']}: {e}")
                else:
                    lost_units.append((unit, str(e)))
                    done += 1
                    if on_progress:
                        on_progress(done, total, f"{label} — retrying that part")
            return
        got, lost = fold_steps(raw.get("steps"), vocabulary or {})
        # ⚠ THE BATCH IS TOLD ITS RANGE AND THE MERGE ENFORCES IT. Every prompt in
        # this file is a request; a model that writes one step past its edge — the
        # overlap shot it can see — would have that cut written twice, once here
        # and once by the pass that owns it, and the person would get two
        # transitions on one edit. Dropped rather than trusted, and named in
        # `dropped` so it is visible rather than silent.
        if not unit["task"].get("sound"):
            inside, outside = [], []
            for step in got:
                at = _step_shot(step)
                (inside if at is None or unit["first"] <= at <= unit["last"] else outside).append(step)
            for step in outside:
                lost.append({
                    "index": 0,
                    "verb": step.get("verb") or "?",
                    "why": (
                        f"shot {_step_shot(step)} is outside this pass's range "
                        f"({unit['first']}–{unit['last']}) — another pass owns it"
                    ),
                })
            got = inside
        sound = _coerce_sound(raw.get("sound"))
        with lock:
            steps.extend(got)
            dropped.extend(lost)
            if sound:
                sfx.extend(sound.get("sfx") or [])
                # ⚠ ONE BED FOR THE FILM — the FIRST one wins. Music is a single
                # decision about the whole thing; two batches each choosing a
                # different one would lay two beds over each other.
                if music is None and sound.get("music"):
                    music = sound["music"]
            done += 1
            if on_progress:
                on_progress(done, total, label)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS,
                            thread_name_prefix="chatwork") as pool:
        list(pool.map(lambda pair: one(*pair), list(enumerate(units))))

    # ⚠ **THE PARTS THAT FELL OVER GET ONE MORE GO, ONE AT A TIME.** Seen live on
    # the second real run: *"2 parts did not come back, so those shots were left
    # alone"* — on a fourteen-shot film, which is not a hard job. `llm_json`
    # already retries a call three times, so a batch that still failed did not
    # fail for its own reasons: it failed because THREE OTHER CALLS WERE IN THE
    # AIR AT THE SAME MOMENT. That is the one failure a fan-out creates and a
    # single request never had, and the fix is the shape of the retry, not the
    # number of them — **serially**, after the wave, when the burst is over and
    # the provider is answering again.
    #
    # ⚠ AND ONLY ONE PASS OF IT, so a genuinely broken job cannot double its own
    # bill trying to talk itself better. Anything still failing after this is
    # named in the reply and left alone.
    if lost_units and not (cancelled and cancelled()):
        retrying = list(lost_units)
        lost_units.clear()
        logger.info("[editor-chat] work — retrying %d part(s) serially", len(retrying))
        if on_progress:
            on_progress(done, total, f"retrying {len(retrying)} part(s)")
        for unit, _why in retrying:
            one(0, unit, retry=True)

    if stopped and not steps and not sfx:
        raise EditorChatError("Stopped before anything was written.")
    if not steps and not sfx and failures:
        raise EditorChatError(failures[0])

    # ⚠ SORTED BY SHOT, BECAUSE THE BATCHES FINISHED IN WHATEVER ORDER THEY
    # FINISHED IN. The preview is read top to bottom against a timeline that runs
    # left to right, and a plan whose steps jump about is one nobody can check.
    steps.sort(key=lambda s: _step_shot(s) if _step_shot(s) is not None else 10_000)
    # ⚠ ONE CUE PER SHOT. A sound job is one call, so this cannot come from two
    # batches disagreeing — it is one model listing the same shot twice, which it
    # does on a film where two shots are alike. The client refuses the second
    # ("shot 14 already has a sound cued") and reports it as something it could
    # not use, which reads to the person as the app failing rather than as the
    # duplicate it is. Cheaper and quieter to keep the first here.
    seen_shots: set[int] = set()
    unique_sfx = []
    for cue in sorted(sfx, key=lambda c: int(c.get("shot") or 0)):
        at = int(cue.get("shot") or 0)
        if at in seen_shots:
            continue
        seen_shots.add(at)
        unique_sfx.append(cue)
    sfx = unique_sfx

    sound = None
    if sfx or music:
        sound = {"sfx": sfx[:MAX_SFX_CUES]}
        if music:
            sound["music"] = music

    # ⚠ **WHY IT FAILED REACHES THE SCREEN, NOT ONLY THE LOG.** The first version
    # said "2 parts did not come back" and stopped there — which tells the person
    # something went wrong and gives them nothing to do about it, and tells
    # whoever has to fix it nothing at all. The reason rides on `dropped`, which
    # the panel already draws under "things I couldn't use", so a rate limit and a
    # broken key stop looking like the same event.
    for why in failures:
        dropped.append({"index": 0, "verb": "part", "why": why})

    if failures:
        logger.warning("[editor-chat] work — %d part(s) failed: %s",
                       len(failures), " | ".join(failures[:4]))
    logger.info(
        "[editor-chat] work DONE — %d batch(es), %d step(s), %d cue(s), %d failed",
        total, len(steps), len(sfx), len(failures),
    )
    return {
        "kind": "plan" if (steps or sound) else "answer",
        # ⚠ COUNTED THE WAY THE PANEL COUNTS, or the two disagree in front of the
        # person. A `note` is not an edit — the plan preview has always excluded
        # it from its chips — and the first version counted it, so the reply said
        # "13 edits" above a button that said "Apply 27 edits". Two numbers for
        # one thing, and neither of them obviously wrong, is worse than either.
        "reply": _work_reply(
            total,
            len([s for s in steps if (s.get("verb") or "") != "note"]),
            len(sfx), bool(music), failures, stopped,
        ),
        "ask": None,
        "look": None,
        "passes": [],
        "plan": {
            "version": 1,
            "summary": "",
            "mood": "",
            "steps": steps,
            # ⚠ FALSE, DELIBERATELY. `asked_for_all` tells the client's guardrails
            # to EXPAND a plan across the film; these steps were already written
            # shot by shot, so expanding them again would double every one.
            "asked_for_all": False,
        } if steps else None,
        "sound": sound,
        "dropped": dropped,
        "stopped": stopped,
    }


def _work_reply(batches: int, steps: int, cues: int, music: bool,
                failures: list[str], stopped: bool) -> str:
    """What the person reads when the job lands. ⚠ COUNTS, NOT ADJECTIVES.

    A fan-out is the one turn where the person cannot see what happened by
    reading the answer, so the answer says what is really there — including the
    parts that failed, which is the half a summary is always tempted to drop.
    """
    bits = []
    if steps:
        bits.append(f"{steps} edit{'s' if steps != 1 else ''}")
    if cues:
        bits.append(f"{cues} sound effect{'s' if cues != 1 else ''}")
    if music:
        bits.append("a music bed")
    made = ", ".join(bits) if bits else "nothing"
    if stopped:
        return f"Stopped. {made.capitalize()} had been written by then — apply it or discard it."
    line = f"Done — {made}, written in {batches} pass{'es' if batches != 1 else ''}."
    if failures:
        # ⚠ AND IT POINTS AT WHERE THE REASON IS. "2 parts did not come back" with
        # no reason anywhere reads as the app shrugging; the reasons are on the
        # panel under "things I couldn't use", and saying so is the difference
        # between a dead end and a next step.
        line += (
            f" {len(failures)} part{'s' if len(failures) != 1 else ''} still did not come "
            "back after a retry, so those shots were left alone — the reason is under "
            "the plan. Ask again for just those."
        )
    return line

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

    lines = [
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

    def row(i: int, shot: dict) -> str:
        bits = [f"{i}. [{_ms(shot.get('ms'))}]"]
        label = _clip(shot.get("label"), SHOT_LABEL_CHARS)
        if label:
            bits.append(label)
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
            "sound": {
                "type": "object",
                "description": "Sound to fetch and lay down. Search TERMS, not prose.",
                "properties": {
                    "sfx": {
                        "type": "array",
                        "description": "One per shot that needs a sound. Sparingly.",
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

    request = JsonRequest(
        system=system,
        prompt=prompt,
        schema=reply_schema(vocabulary or {}),
        purpose="editor chat",
        # ⚠ THIS IS WHAT PUTS THE CHAT ON ITS OWN KEY. Everything else about the
        # call is shared with the Director; the one word here is what decides
        # whose credentials and whose bill answer it. See `CAPABILITIES` in
        # `llm_json` — the string must be a key of that table or it silently
        # falls back to the shared text settings.
        capability=CAPABILITY,
    )

    try:
        raw = complete_json(request)
    except LLMJsonError as e:
        raise EditorChatError(str(e)) from None

    return _read_turn(raw, vocabulary or {})


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
        "transitions": ids(vocabulary.get("transitions")),
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
        "house_caps": vocabulary.get("caps") or {},
    }


def _read_turn(raw: dict, vocabulary: dict) -> dict:
    """What came back, read into a turn. Never raises on a shape problem.

    ⚠ **THE KIND IS DECIDED BY WHAT IS THERE.** A reply labelled `plan` with no
    usable steps is an answer; a reply labelled `answer` that came with a real
    question and options is an ask. The label is what the model INTENDED and the
    content is what it managed, and the second is the one a panel can draw. The
    client applies the same rule again for the same reason — see `normaliseTurn`.
    """
    from director import fold_steps

    row = raw if isinstance(raw, dict) else {}
    reply = _clip(row.get("reply"), MAX_REPLY_CHARS)
    ask = _coerce_ask(row.get("ask"))

    sound = _coerce_sound(row.get("sound"))

    steps, dropped = [], []
    plan_row = row.get("plan")
    if isinstance(plan_row, dict):
        steps, dropped = fold_steps(plan_row.get("steps"), vocabulary)

    # ⚠ SOUND ALONE IS STILL A PLAN. "Put some music under it" is a request that
    # produces no steps at all — every edit it makes is a clip the sound pass lays
    # down — and reading that as an `answer` would draw a chat bubble where an
    # Apply button belongs, so the user would have been told what would happen and
    # given no way to make it happen.
    if steps or sound:
        kind = "plan"
        summary = plan_row.get("summary") if isinstance(plan_row, dict) else ""
        mood = plan_row.get("mood") if isinstance(plan_row, dict) else ""
        plan = {
            "version": 1,
            "summary": _clip(summary, 200),
            "mood": _clip(mood, 60),
            "steps": steps,
        }
    elif ask:
        kind, plan = "ask", None
    else:
        kind, plan = "answer", None

    if not reply and kind == "answer" and not ask:
        raise EditorChatError("The model returned an empty reply. Try rephrasing.")

    logger.info(
        "[editor-chat] %s — reply %d chars, %d step(s), %d dropped, %s, %s",
        kind, len(reply), len(steps), len(dropped),
        f"{len(ask['options'])} option(s)" if ask else "no options",
        f"{len(sound['sfx'])} sfx{' + music' if sound.get('music') else ''}"
        if sound else "no sound",
    )
    return {
        "kind": kind,
        "reply": reply,
        "ask": ask,
        "plan": plan,
        "sound": sound,
        "dropped": dropped,
    }

"""
script_agent.py — the assistant that sits IN the Script → Storyboard form.

Why it exists
-------------
The script box on that page took typing and file uploads only, so a user who did
not already have a script had to leave the workflow, write one somewhere else,
and come back. This is the third way in: a normal chat, in the same box, that can
answer anything — and that is specifically good at producing a script the
breakdown can read.

What makes it different from `plan_agent.chat`
----------------------------------------------
`plan_agent` is a content STRATEGIST: it interrogates the creator about their
channel and cadence so it can build a publishing calendar, and its replies are
deliberately question-heavy. That is wrong here. On this page the user has
already decided what they want to make; they want a writing partner, and often
just a plain answer to a plain question. So this agent:

  - answers ANYTHING conversationally (it is not gated to scripts),
  - writes a script only when a script is what was asked for,
  - and when it does, returns it in its OWN field rather than buried in prose.

⚠ **THE `script` FIELD IS THE WHOLE POINT.** The reply is chat — it scrolls away.
The script has to land in the text box that the breakdown reads, so it comes back
separately and the browser can offer one button that fills that box. Asking the
user to select-and-copy a script out of a chat bubble is exactly the friction
this feature was added to remove.

⚠ **THE SCRIPT'S LAYOUT IS NOT A STYLE CHOICE.** `script_breakdown.py` reads
scene boundaries off slug lines and speech off `NAME: line`. The format demanded
below is the same one `plan_agent.script_to_text` emits, for the same reason —
what comes out of here has to parse like what comes out of there, or the same
story breaks into a different board depending on which door it came through.

Backend, retries and token accounting are `plan_agent`'s, imported rather than
re-implemented: a second copy of the retry policy is a second thing to keep in
step, and the Work Log already has one bug from exactly that.
"""

import json
import logging

from google.genai import types

from ai_usage import describe, merge

# Retry/backoff, provider resolution and the "our messages → SDK Content"
# translation are already solved next door. Private names, deliberately: this
# module is a sibling of plan_agent, not a client of it.
from plan_agent import (
    MAX_MESSAGE_CHARS,
    PlanError,
    _call,
    _to_contents,
)
from script_breakdown import _sampling_kwargs

logger = logging.getLogger(__name__)


class ScriptChatError(PlanError):
    """Raised when a reply can't be produced. Carries a readable reason."""


# How much of the script currently in the box we send as context. A user can
# paste a feature film in there; the assistant only needs enough to answer
# "shorten scene 2" without the request costing more than the answer.
MAX_CONTEXT_SCRIPT_CHARS = 12000


# ---------------------------------------------------------------------------
# The assistant's brief
# ---------------------------------------------------------------------------
_SYSTEM_INSTRUCTION = (
    "You are the writing assistant built into Aniwala AI Studio, on the "
    "'Script to Storyboard' page. The person you are talking to is about to "
    "turn a script into a shot-by-shot storyboard.\n\n"

    "HOW TO BEHAVE\n"
    "- You are a normal, helpful assistant first. Answer whatever is asked - a "
    "question about story structure, a title, a character name, camera "
    "language, a fact, an opinion, or just conversation. Never refuse a topic "
    "because it 'isn't about scripts'.\n"
    "- Your speciality is writing and fixing SCRIPTS for short films, ads, "
    "explainers, music videos, reels and animation. Be genuinely good at it: "
    "concrete visuals, a clear want for the main character, a hook in the first "
    "few seconds, and an ending that lands.\n"
    "- Reply in the SAME language and the same script the user writes in. If "
    "they write Hinglish (Hindi in Latin letters), answer in Hinglish in Latin "
    "letters - do NOT switch to Devanagari, and do not switch to English.\n"
    "- Keep chat replies short: a few sentences, or a short list. This is a "
    "chat box beside a form, not an essay page.\n"
    "- Ask a question only when you genuinely cannot proceed without the "
    "answer. If a reasonable assumption exists, make it, say which one you "
    "made, and write the thing. Do not interrogate.\n"
    "- Plain text only. No markdown headings, no bold markers, no code fences.\n\n"

    "WHEN TO FILL IN `script`\n"
    "- Put a script in `script` ONLY when the user asked you to write, rewrite, "
    "extend, shorten or fix a script, and you are handing over a COMPLETE "
    "script they could shoot. Never a fragment, never an outline.\n"
    "- When you do, `reply` is a SHORT note about it ('Here is a 60-second "
    "version - Meera now wants the job, not just the money.'). Do NOT repeat "
    "the script in `reply`; it is already on screen as a script.\n"
    "- For anything else - questions, ideas, feedback, brainstorming, small "
    "talk - leave `script` EMPTY and put your answer in `reply`.\n"
    "- `title` is a few words naming the script, only when you filled in "
    "`script`.\n\n"

    "THE SCRIPT FORMAT - FOLLOW IT EXACTLY\n"
    "The text you put in `script` is fed to a shot-breakdown that reads scene "
    "boundaries and dialogue off its layout. Write it like this, plain text:\n\n"
    "TITLE IN CAPITALS\n\n"
    "LOGLINE: one sentence.\n\n"
    "CAST\n"
    "MEERA - 30s, courier, permanently in a hurry, scarred left hand.\n"
    "ARJUN - 60s, watchmaker, slow and precise.\n\n"
    "SCENE 1. INT. WATCH SHOP - NIGHT\n"
    "One action beat per line, present tense, only what the CAMERA can see.\n"
    "Meera puts the parcel on the counter and does not let go of it.\n"
    "MEERA: You said it would be ready.\n"
    "ARJUN (V.O.): Some things are not worth hurrying.\n"
    "ON SCREEN: Three years earlier.\n\n"
    "SCENE 2. EXT. STREET - DAWN\n"
    "...\n\n"
    "Rules for that layout:\n"
    "- Every scene starts with 'SCENE n. INT./EXT. PLACE - TIME' alone on its "
    "line, with a blank line above and below.\n"
    "- ONE beat per line. A line is one thing that happens, so it can become "
    "one panel.\n"
    "- Speech is 'NAME: line'. A speaker who is not in frame is "
    "'NAME (V.O.): line'. On-screen text is 'ON SCREEN: text'.\n"
    "- Action lines describe what is SEEN. Never write a character's thoughts, "
    "backstory or feelings in an action line - an artist cannot draw them.\n"
    "- Name every character in CAST with an appearance, because the storyboard "
    "uses those descriptions to keep faces consistent across panels."
)


def _schema() -> types.Schema:
    """`{reply, script, title}` — see the module docstring on why `script` is
    its own field rather than something the browser has to find in the prose."""
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reply": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The chat answer, in the user's own language. Short. Never "
                    "contains the script itself."
                ),
            ),
            "script": types.Schema(
                type=types.Type.STRING,
                description=(
                    "A COMPLETE script in the required plain-text layout, or an "
                    "empty string when the user did not ask for a script."
                ),
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="A few words naming the script. Empty if no script.",
            ),
        },
        required=["reply"],
    )


def build_context(
    genre: str = "",
    style: str = "",
    aspect_ratio: str = "",
    title: str = "",
    current_script: str = "",
) -> str:
    """What the FORM already says, as a context block for the system prompt.

    The assistant sits inside a form the user has usually half-filled — a genre,
    a visual style, an aspect ratio, and often a script already in the box. Not
    passing that along is what produces "which genre would you like?" one second
    after the user clicked Mythology, and a 16:9 script for a board that is 9:16.

    Returns "" when there is nothing to say, so the caller can test it plainly.
    """
    lines: list[str] = []
    if (genre or "").strip() and genre.strip().lower() != "default":
        lines.append(f"- Genre picked on the form: {genre.strip()}")
    if (style or "").strip():
        lines.append(f"- Visual style picked on the form: {style.strip()}")
    if (aspect_ratio or "").strip():
        vertical = aspect_ratio.strip() in ("9:16", "4:5")
        lines.append(
            f"- Frame: {aspect_ratio.strip()}"
            + (
                " — a vertical, phone-first film. Keep the staging tight: one or "
                "two people in frame, close, no wide crowd shots."
                if vertical
                else ""
            )
        )
    if (title or "").strip():
        lines.append(f"- Storyboard title typed by the user: {title.strip()}")

    script = (current_script or "").strip()
    if script:
        clipped = script[:MAX_CONTEXT_SCRIPT_CHARS]
        lines.append(
            "- The script box currently contains the text below. When the user "
            "says 'it', 'the script', 'scene 2' or 'make it shorter', they mean "
            "THIS. Rewrite it rather than inventing a new story.\n"
            "--- CURRENT SCRIPT ---\n"
            + clipped
            + ("\n… (truncated)" if len(script) > MAX_CONTEXT_SCRIPT_CHARS else "")
            + "\n--- END CURRENT SCRIPT ---"
        )

    if not lines:
        return ""
    return "WHAT THE FORM ALREADY SAYS:\n" + "\n".join(lines)


def chat(messages: list[dict], context: str = "") -> dict:
    """One conversational turn.

    Args:
        messages: [{role: "user"|"agent", text: str}, …], oldest first, ending
            with the user's newest message. The browser owns the transcript and
            sends the whole thing — this call keeps no state.
        context: the form's current state, from `build_context`.

    Returns:
        {"reply": str, "script": str, "title": str, "usage": {…}}

        `script` is "" on every turn that wasn't a request for a script, which
        is most of them. `usage` is this turn's token count (ai_usage.Usage).
    """
    convo = [m for m in (messages or []) if str(m.get("text", "") or "").strip()]
    if not convo:
        raise ScriptChatError("Type a message to get started.")

    system = _SYSTEM_INSTRUCTION
    if context.strip():
        system += "\n\n" + context.strip()

    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_schema(),
        **_sampling_kwargs(),
    )
    spent: list = []
    payload = _call(_to_contents(convo), config, "answering the script chat", spent)
    usage = merge(*spent)

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        # Structured output failed but the model still said something useful —
        # keep the words rather than losing the turn over its wrapper. Same
        # call as plan_agent.chat makes, for the same reason.
        logger.warning("[script-chat] reply wasn't valid JSON; using it as plain text")
        return {
            "reply": payload.strip()[:MAX_MESSAGE_CHARS],
            "script": "",
            "title": "",
            "usage": usage.as_dict(),
        }

    reply = str((raw or {}).get("reply", "")).strip()
    script = str((raw or {}).get("script", "")).strip()
    title = str((raw or {}).get("title", "")).strip()

    if not reply and not script:
        raise ScriptChatError("The model returned an empty reply. Try rephrasing.")
    if script and not reply:
        # A script with no covering note is a legal answer from the model but a
        # blank chat bubble on screen. Say the obvious thing rather than nothing.
        reply = "Here's a draft — put it in the script box and edit anything."

    logger.info(
        "[script-chat] reply: %d chars, script: %d chars, over %d message(s) — %s",
        len(reply), len(script), len(convo), describe(usage),
    )
    return {
        "reply": reply,
        "script": script,
        "title": title,
        "usage": usage.as_dict(),
    }

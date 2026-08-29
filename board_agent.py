"""
board_agent.py — "Ask AI" on a board that already exists.

The last stage of the intake redesign, and the only one where the AI is an
EDITOR rather than an author.

Why it lives here and not on the first screen
---------------------------------------------
The Script → Storyboard form used to carry a chat. It was moved off (Phase 1)
because on that screen the user is not talking to anything — they are handing
over material. Once a board exists the situation reverses: there is a specific
thing on screen, the user wants a specific change to it, and "add a close-up
before shot 5" is a sentence no form field can take.

⚠ **THIS MODULE PLANS. IT DOES NOT ACT.** It returns a list of intended edits
and a sentence about them; the browser shows that list, the user presses Apply,
and the edits go through the SAME endpoints the board's own buttons use —
`/regenerate-panel`, `/panels/insert`, `/panels/{i}`. Two reasons, and both
matter:

  1. **Money.** Redrawing a panel is an image. One sentence must never be able
     to spend forty of them behind the user's back, so the spend stays behind
     the buttons that already have `cap.image-generate` on them, and the count
     is on screen before anything is charged.
  2. **The board is already editable by hand.** An agent with its own private
     write path would be a second implementation of insert/delete/redraw to
     keep in step with the first — the Work Log already has one bug from
     exactly that kind of duplication.

⚠ **THREE VERBS, AND IT SAYS NO TO EVERYTHING ELSE.** `edit`, `insert`,
`delete`. Not reordering (no endpoint exists), not restyling (that is a
whole-board spend with its own button), not dialogue (edited on the shot list),
not export. An assistant that accepts every request and quietly does the nearest
thing it can is worse than one that answers "I can't do that here" — the first
loses trust in a day.

⚠ **SHOT NUMBERS ARE 1-BASED ON THE WIRE, BECAUSE THAT IS WHAT IS ON SCREEN.**
The model reads "Shot 7" on the board and the user types "shot 7"; asking it to
answer in 0-based indices is inviting an off-by-one that silently edits the
wrong picture. `plan()` converts to indices at the boundary.

Backend, retries and token accounting are `plan_agent`'s, imported rather than
re-implemented — same as `script_agent`, `script_intake` and `script_concept`.

Spends TEXT quota only. It cannot draw; nothing in this file can.
"""

import json
import logging

from google.genai import types

from ai_usage import describe, merge
from plan_agent import MAX_MESSAGE_CHARS, PlanError, _call, _to_contents
from script_breakdown import _sampling_kwargs

logger = logging.getLogger(__name__)


class BoardChatError(PlanError):
    """Raised when a turn can't be answered. Carries a readable reason."""


# ⚠ THE CEILING ON ONE SENTENCE. Eight edits is already a big ask of a board;
# past that the user is describing a different film and should say so on the
# shot list, where changes are free. This is the hard half of the money guard —
# the prompt asks for restraint, this enforces it.
MAX_ACTIONS = 8

# Per-shot description sent as context. Enough to recognise the shot by, not the
# whole prompt: a 120-panel board would otherwise be a novel on every turn.
MAX_DESC_CHARS = 160
# And a ceiling on the lot, for the same reason.
MAX_BOARD_CHARS = 24000
# How much conversation rides along. The browser owns the transcript.
MAX_HISTORY = 20

ACTIONS = ("edit", "insert", "delete")


_SYSTEM_INSTRUCTION = (
    "You are the assistant beside a FINISHED storyboard in Aniwala AI Studio. "
    "The user is looking at a board of numbered shots and wants to change it. "
    "Your job is to turn what they say into a short list of edits.\n\n"

    "⚠ YOU ARE AN EDITOR, NOT A WRITER. The film already exists. You do not "
    "invent new stories, retitle it, or improve scenes nobody asked about.\n\n"

    "THE ONLY THREE THINGS YOU CAN DO\n"
    "- edit: rewrite one shot's description (and optionally its camera or "
    "location), which re-draws that panel.\n"
    "- insert: add ONE new shot before a given shot number, with a description "
    "you write. Use a shot number one past the last shot to add at the end.\n"
    "- delete: remove one shot.\n\n"

    "⚠ SAY NO TO EVERYTHING ELSE, PLAINLY AND IN ONE SENTENCE, AND RETURN "
    "NO ACTIONS. Below is WHAT IS TRUE for each; say it as a whole sentence "
    "and add nothing to it:\n"
    "- Reorder or move a shot: not possible once a board is drawn. Say they "
    "can delete the shot and add it back where they want it, and that you "
    "can do that if they ask.\n"
    "- Restyle, change the look, change the medium: the 'Restyle all' "
    "button above the board.\n"
    "- Dialogue, or what someone says: edited on the shot list, not here.\n"
    "- Rename the board: the title at the top of this page.\n"
    "- Download, export, PDF: the 'Download PDF' button.\n"
    "- Aspect ratio or frame size: fixed once a board is drawn; it is "
    "chosen when the storyboard is created.\n"
    "⚠ NEVER INVENT A WAY TO DO SOMETHING. There is no drag-and-drop on "
    "this board, no right-click menu, no settings panel, no shot properties "
    "dialog and no 'Export' button. If the answer is not in the list above, "
    "say plainly that it cannot be done from here, and stop. A confident "
    "instruction for a button that does not exist is worse than a flat no.\n\n"

    "⚠ TOUCH THE FEWEST SHOTS THAT ANSWER THE REQUEST. Every edit re-draws a "
    "picture and costs the user money. If they name a shot, change that shot. "
    "If something is SELECTED and they did not name a shot, they mean the "
    "selection. If neither — 'make it more cinematic' on a board of forty — do "
    "NOT rewrite forty shots: return no actions and ask which shots they mean. "
    "Never touch a shot the request does not reach.\n\n"

    "WRITING A DESCRIPTION\n"
    "- A description is what the CAMERA SEES, in one or two plain sentences. No "
    "thoughts, no backstory, no feelings an artist cannot draw.\n"
    "- ⚠ WRITE THE DESCRIPTION YOURSELF. 'Add a close-up before shot 5' is a "
    "COMPLETE instruction: the shots around it already say who is there and "
    "where, so write what that close-up shows. Only ask the user what to put in "
    "it when the surrounding shots genuinely do not say.\n"
    "- When you edit, write the shot's description IN FULL, not a diff and not "
    "an instruction. Keep everything the user did not ask you to change — a "
    "'make it a close-up' that quietly drops the character's coat and the rain "
    "is a redraw of a different shot.\n"
    "- Camera is the shot type or angle ('medium close-up', 'low angle, slow "
    "push-in'). Location is where it happens. Fill them in only when the "
    "request is actually about them.\n\n"

    "SHOT NUMBERS\n"
    "Use the numbers exactly as they are printed on the board and as the user "
    "says them — the first shot is 1. Never use 0.\n\n"

    "THE REPLY\n"
    "One or two short sentences saying what you are about to change, in the "
    "SAME language and script the user wrote in. If they write Hinglish (Hindi "
    "in Latin letters), answer in Hinglish in Latin letters — not Devanagari, "
    "not English. Do not list the shots again; the user can see the list. Plain "
    "text only: no markdown, no bold, no code fences."
)

# ⚠ THE ASSISTANT MUST NOT INTRODUCE ITSELF BY A NAME THE APP NO LONGER USES.
# The product is renameable from the admin panel now (`server/branding.py`), and
# this brief tells the model where it is standing — so an owner who renames the
# app and then asks the assistant "what are you?" would otherwise be told the old
# name, in their own product, by their own product.
#
# ⚠ A REPLACE, NOT A `format()`. The brief is full of literal braces (JSON
# examples, shot templates), so a format string would either blow up or need
# every one of them doubled — one escaping mistake away from a mangled prompt.
#
# ⚠ AND IT STAYS A PLAIN STRING CONSTANT ABOVE. The prompt checks in `tests/`
# assert on phrases inside `_SYSTEM_INSTRUCTION` directly, and a brief that could
# only be read by calling something would put those out of reach.
_BUILT_IN_APP = "Aniwala AI Studio"


def _system_instruction() -> str:
    """The brief, wearing whatever the app is currently CALLED.

    Falls back to the built-in name on any failure — a naming lookup must never
    be the reason a chat turn fails.
    """
    try:
        from server import branding

        name = branding.get_branding().get("name") or _BUILT_IN_APP
    except Exception:  # noqa: BLE001 — cosmetic; see the docstring
        return _SYSTEM_INSTRUCTION
    if name == _BUILT_IN_APP:
        return _SYSTEM_INSTRUCTION
    return _SYSTEM_INSTRUCTION.replace(_BUILT_IN_APP, name)



def _schema() -> types.Schema:
    """`{reply, actions[]}` — a plan, never a result."""
    return types.Schema(
        type=types.Type.OBJECT,
        required=["reply"],
        properties={
            "reply": types.Schema(
                type=types.Type.STRING,
                description=(
                    "One or two sentences in the user's own language. Empty "
                    "actions plus a reply is a legal, and often correct, answer."
                ),
            ),
            "actions": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    required=["action", "shot"],
                    properties={
                        "action": types.Schema(
                            type=types.Type.STRING, enum=list(ACTIONS)
                        ),
                        "shot": types.Schema(
                            type=types.Type.INTEGER,
                            description=(
                                "The shot number PRINTED ON THE BOARD, first "
                                "shot = 1. For insert, the new shot goes BEFORE "
                                "this one."
                            ),
                        ),
                        "description": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "The shot's FULL new description. Required for "
                                "insert; for edit, leave empty only when the "
                                "change is purely camera or location."
                            ),
                        ),
                        "camera": types.Schema(type=types.Type.STRING),
                        "location": types.Schema(type=types.Type.STRING),
                        "why": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "Half a line naming the change, for the list the "
                                "user approves. 'Tighter framing on Anna.'"
                            ),
                        ),
                    },
                ),
            ),
        },
    )


def board_context(
    panels: list[dict],
    title: str = "",
    style: str = "",
    aspect_ratio: str = "",
    selection: dict | None = None,
) -> str:
    """The board as the model needs to see it: numbered shots, one line each.

    ⚠ `selection` IS THE HALF THAT MAKES THIS FEEL LIKE AN ASSISTANT. Without
    it, "make this one wider" has no referent and the only honest answer is
    "which one?". With it, the user clicks a panel and speaks normally — which
    is the whole ask.
    """
    lines: list[str] = ["THE BOARD:"]
    if (title or "").strip():
        lines.append(f"Title: {title.strip()}")
    if (style or "").strip():
        lines.append(f"Visual style: {style.strip()} — do not change it here.")
    if (aspect_ratio or "").strip():
        lines.append(f"Frame: {aspect_ratio.strip()}")
    lines.append(f"{len(panels)} shot(s). Add at the end with shot {len(panels) + 1}.")
    lines.append("")

    for i, p in enumerate(panels):
        desc = str((p or {}).get("description", "") or "").strip()
        if len(desc) > MAX_DESC_CHARS:
            desc = desc[:MAX_DESC_CHARS].rsplit(" ", 1)[0] + "…"
        bits = [f"Shot {i + 1}"]
        scene = (p or {}).get("scene_number")
        if scene:
            bits.append(f"(scene {scene})")
        camera = str((p or {}).get("camera", "") or "").strip()
        if camera:
            bits.append(f"[{camera}]")
        lines.append(" ".join(bits) + f": {desc or '(no description yet)'}")

    block = "\n".join(lines)
    if len(block) > MAX_BOARD_CHARS:
        block = block[:MAX_BOARD_CHARS] + "\n… (board truncated)"

    sel = _selection_line(selection, len(panels))
    if sel:
        block += "\n\n" + sel
    return block


def _selection_line(selection: dict | None, total: int) -> str:
    """What the user has clicked on, as a sentence the model can act on."""
    selection = selection if isinstance(selection, dict) else {}
    kind = str(selection.get("kind", "") or "").strip().lower()

    # ⚠ EVERY UNREADABLE SELECTION FALLS THROUGH TO "nothing", NEVER TO
    # SILENCE. Returning "" here would drop the one line that stops a vague
    # sentence redrawing forty panels — a junk value would be more dangerous
    # than no value, which is exactly backwards.
    if kind == "panel":
        try:
            shot = int(selection.get("shot") or 0)
        except (TypeError, ValueError):
            shot = 0
        if 1 <= shot <= total:
            return (
                f"SELECTED: shot {shot}. If the user does not name a shot, they "
                f"mean this one."
            )

    elif kind == "scene":
        try:
            scene = int(selection.get("scene") or 0)
        except (TypeError, ValueError):
            scene = 0
        if scene > 0:
            return (
                f"SELECTED: every shot in scene {scene}. If the user does not "
                f"name a shot, they mean those — and only those."
            )

    # Nothing selected — and this is information too. Say it out loud.
    return (
        "SELECTED: nothing. A request that names no shot and describes no "
        "specific moment cannot be acted on — ask which shots they mean rather "
        "than guessing."
    )


def _coerce_actions(raw, total: int) -> list[dict]:
    """Validate the model's plan into actions the client can actually run.

    Converts the 1-based shot numbers the model speaks in into the 0-based
    indices the endpoints take, drops anything out of range or empty, and caps
    the list. ⚠ Everything here is a REJECTION, never a repair: an action we had
    to guess at is an edit the user did not ask for, on a picture they will pay
    to redraw.
    """
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "") or "").strip().lower()
        if action not in ACTIONS:
            continue
        try:
            shot = int(item.get("shot") or 0)
        except (TypeError, ValueError):
            continue

        # insert may address one past the end (append); the others may not.
        upper = total + 1 if action == "insert" else total
        if not (1 <= shot <= upper):
            continue

        description = str(item.get("description", "") or "").strip()
        camera = str(item.get("camera", "") or "").strip()
        location = str(item.get("location", "") or "").strip()

        if action == "edit" and not (description or camera or location):
            # An edit that changes nothing still costs an image. Drop it.
            continue
        if action == "insert" and not description:
            # A blank inserted panel is what the board's own ＋ button makes;
            # coming from a sentence it is just a failed instruction.
            continue

        out.append(
            {
                "action": action,
                # What the endpoints take.
                "index": shot - 1,
                # What the user reads, kept so the list on screen matches the
                # numbers under the pictures.
                "shot": shot,
                "description": description,
                "camera": camera,
                "location": location,
                "why": str(item.get("why", "") or "").strip(),
                # Does running this cost an image? The client totals these up
                # and puts the number on the Apply button.
                "draws": action in ("edit", "insert"),
            }
        )
        if len(out) >= MAX_ACTIONS:
            break
    return out


def plan(
    messages: list[dict],
    panels: list[dict],
    selection: dict | None = None,
    title: str = "",
    style: str = "",
    aspect_ratio: str = "",
) -> dict:
    """One turn: what the user said → what should change on the board.

    Args:
        messages: [{role: "user"|"agent", text}, …] oldest first, ending with
            the newest user message. The browser owns the transcript.
        panels: the board's panels, in order, as the job holds them.
        selection: {"kind": "panel", "shot": n} | {"kind": "scene", "scene": n}
            | None. See `_selection_line`.

    Returns:
        {"reply": str, "actions": [...], "usage": {…}}

        An empty `actions` with a reply is a normal answer — it is what "I can't
        do that here" and "which shots do you mean?" both look like.
    """
    convo = [m for m in (messages or []) if str(m.get("text", "") or "").strip()]
    if not convo:
        raise BoardChatError("Type what you'd like to change.")
    convo = convo[-MAX_HISTORY:]
    if not panels:
        raise BoardChatError("There are no shots on this board yet.")

    system = (
        _system_instruction()
        + "\n\n"
        + board_context(panels, title, style, aspect_ratio, selection)
    )
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_schema(),
        **_sampling_kwargs(),
    )
    spent: list = []
    payload = _call(_to_contents(convo), config, "planning the board edit", spent)
    usage = merge(*spent)

    try:
        raw = json.loads(payload) or {}
    except json.JSONDecodeError:
        # ⚠ FALL BACK TO WORDS, NEVER TO ACTIONS. A reply we could not parse
        # tells us nothing about which shots to touch, and inventing edits from
        # a broken wrapper spends the user's money on a guess.
        logger.warning("[board-ask] reply wasn't valid JSON; keeping the words only")
        return {
            "reply": payload.strip()[:MAX_MESSAGE_CHARS],
            "actions": [],
            "usage": usage.as_dict(),
        }

    reply = str(raw.get("reply", "") or "").strip()
    actions = _coerce_actions(raw.get("actions"), len(panels))

    if not reply and not actions:
        raise BoardChatError("The assistant didn't answer. Try rephrasing.")
    if actions and not reply:
        reply = "Here's what I'd change — have a look before applying."

    logger.info(
        "[board-ask] %d action(s) over %d shot(s) — %s",
        len(actions), len(panels), describe(usage),
    )
    return {"reply": reply, "actions": actions, "usage": usage.as_dict()}

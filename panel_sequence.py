"""
panel_sequence.py — One storyboard panel → a flipbook of key poses.

The animator's version of "make this shot move". You pick a length (2/4/6/8/10
seconds) and this produces the KEY DRAWINGS for it — the poses an animator would
block out — not a video, and not every frame.

THE ARITHMETIC, because it is the whole idea:

    4 seconds at 24fps is 96 frames. Nobody draws 96 key poses, and nobody wants
    to look at 96 near-identical pictures. An animator blocks the handful of
    poses that CARRY the motion and lets the inbetweens follow. So the model is
    told the real frame budget (96) and asked for the ~16 poses that describe
    it, each tagged with the frame number it lands on.

    KEY_POSES_PER_SECOND = 4 → 2s=8, 4s=16, 6s=24, 8s=32, 10s=40.

TWO CALLS, TWO BACKENDS:

    1. `plan_beats()` — the TEXT model reads the shot's description and returns
       one short line per key pose, in order. This is the "separate the scene
       into main poses" step. It also returns `hold`, the shot's INVARIANT.
    2. `generate_frame()` — the IMAGE model draws each of those lines.

POSE 1 IS THE PANEL, COPIED — NOT DRAWN. The panel is already on the user's
board and already approved, so the flipbook has to open on that exact picture.
Generating it like any other pose produced a fresh interpretation of an approved
shot — a different first image every time, which is the first thing anyone
notices on opening the zip. No prompt fixes that; a file copy does, exactly and
for free. Every pose after it is drawn, anchored on that same panel.

A SHOT MAY NOT OUTRUN ITS OWN DESCRIPTION. The other half of the same report: an
establishing wide whose description was "the room, Kabir asleep" came back as
eight drawings of him waking and sitting up — while the NEXT panel on the board
still showed him fast asleep. The planner had been handed one sentence and a
hard demand for movement in every drawing, so it went and found some. Two things
stop that now, and both must stay:

    - `story_context` (from `board_panels`) tells the planner what the shots
      either side of this one show. The next shot's description is the wall this
      shot's action stops at.
    - `hold` is one sentence naming what stays true in every drawing — "Kabir
      stays asleep under the quilt, he never wakes, sits up or leaves the bed".
      It is stored with the pose plan and handed to EVERY drawing, because a
      pose line on its own is a fragment: "his shoulder drops an inch" says
      nothing about the man being asleep. In the image prompt it is the last
      word over the "the body must have MOVED" push, which otherwise has no
      upper bound.

WHY EVERY FRAME IS ANCHORED ON THE ORIGINAL PANEL: each image is generated with
the source panel as its `composition_reference_image`, never with the previous
FRAME. Chaining frame→frame looks tempting for continuity and is a trap — small
errors compound, and by frame 12 the character has drifted into someone else in
a different room. Anchoring every frame to the same panel keeps the staging,
the character and the lighting fixed, so only the pose moves.

Nothing here knows about HTTP or jobs; the server drives it and passes
`progress_cb` / `cancel_check`.
"""

import json
import logging
import os

from google.genai import types

logger = logging.getLogger(__name__)


class SequenceError(RuntimeError):
    """Sequence generation failed for a reason worth showing the user."""


# The menu the popup offers. Anything else is refused rather than quietly
# rounded, so the count and the cost are always predictable.
ALLOWED_DURATIONS = (2, 4, 6, 8, 10)
# What an animator would block out per second of screen time. Four is the number
# that put a 4-second shot in the 10–20 range the owner asked for.
KEY_POSES_PER_SECOND = 4
# The frame rate the plan is reasoned in. Not configurable: 24 is what "one
# second of animation" means to the person using this.
FPS = 24
# Hard ceiling on one request, so a mis-click can't queue hundreds of images.
MAX_FRAMES = 40
# How many drawings a PREVIEW buys. Two, because one drawing cannot show
# movement — you need something to compare it against, and the whole question a
# preview answers is "did the character move between drawings?". A 10s shot is
# 40 images; finding out the answer is no should not cost 40.
PREVIEW_POSES = 2


def frame_count_for(duration_seconds: int) -> int:
    """How many key poses a shot of this length gets."""
    return min(MAX_FRAMES, max(1, int(duration_seconds) * KEY_POSES_PER_SECOND))


def validate_duration(duration_seconds: int) -> int:
    if int(duration_seconds) not in ALLOWED_DURATIONS:
        raise SequenceError(
            f"Pick one of {', '.join(f'{d}s' for d in ALLOWED_DURATIONS)}."
        )
    return int(duration_seconds)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def sequence_dir(board_dir: str, index: int) -> str:
    """Where one panel's frames live. Separate per panel, so regenerating one
    shot's sequence never disturbs another's."""
    return os.path.join(board_dir, "seq", f"panel_{int(index):02d}")


def frame_path(board_dir: str, index: int, n: int) -> str:
    return os.path.join(sequence_dir(board_dir, index), f"frame_{int(n):03d}.png")


def frame_version(board_dir: str, index: int, n: int) -> int:
    """A token that CHANGES when this pose is redrawn. 0 if it isn't there.

    A redrawn pose keeps its filename, so its URL used to be byte-identical
    before and after — and the client, which caches one object URL per path and
    never re-fetches a path it already has, went on showing the OLD drawing for
    ever. Pressing "redraw this pose" appeared to do nothing at all; so did a
    whole regenerate. Stamping the file's mtime into the URL makes a redrawn
    pose a different URL, which is the same trick the panel URLs already use
    with `?v=<variant>`.

    Nanoseconds, not seconds: two redraws of one pose inside the same second are
    easy to do by hand and would otherwise collide back into a stale picture.
    """
    try:
        return os.stat(frame_path(board_dir, index, n)).st_mtime_ns
    except OSError:
        return 0


def frames_on_disk(board_dir: str, index: int, planned: int) -> list[int]:
    """Which of the `planned` key poses actually exist as files.

    EVERY index is checked, deliberately. Counting with a `while` loop from zero
    — which is what the API used to do — stops at the first hole, so a single
    refused frame in the middle made every drawing after it invisible AND made
    a resume redraw pictures that were already paid for. A gap is a gap, not the
    end of the sequence.
    """
    return [n for n in range(max(0, int(planned))) if os.path.isfile(frame_path(board_dir, index, n))]


# ---------------------------------------------------------------------------
# Step 1 — plan the key poses (TEXT model)
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You are a 2D animation supervisor blocking out a shot. You are given ONE "
    "storyboard panel's description and a shot length. You return the KEY "
    "DRAWINGS for that shot — the specific poses an animator would draw first, "
    "which together describe the whole motion.\n"
    "Rules:\n"
    # POSE 1 IS THE PANEL, NOT A DRAWING OF IT. The panel is already on the
    # user's board and already approved; the sequence has to START from that
    # exact picture. Asked to "match the panel as described", the model wrote a
    # fresh opening pose, the image model drew it, and pose 1 came back as a
    # different picture from the one on the board — reported, and the first
    # thing anyone notices when they open the zip. The runner now COPIES the
    # panel in as pose 1; this line keeps the plan honest about that.
    "- Pose 1 IS the panel itself, unchanged — the exact drawing already on the "
    "board, not your description of it and not a new opening pose. Write pose 1 "
    "as 'the panel exactly as drawn — the shot starts here' and nothing else. "
    "The motion begins at pose 2.\n"
    "- Each later pose moves the action on. No two poses may be the same.\n"
    # THE SHOT MAY NOT OUTRUN ITS OWN DESCRIPTION. This is the bug that made a
    # sleeping man wake up: told only "a wide shot establishes the bedroom,
    # Kabir asleep" and pushed hard for movement in every drawing, the model
    # went looking for an action and invented the only one available — he wakes,
    # sits up, puts his feet on the floor. Eight drawings later the shot has
    # played the whole next scene, and the NEXT panel on the board still shows
    # him fast asleep. A shot animates the moment it was written for; anything
    # further along the story is a different shot.
    "- STAY INSIDE THE SHOT AS WRITTEN. Animate ONLY the action the description "
    "names. Never invent an action it does not mention and never carry the story "
    "forward: nobody wakes, sits up, stands, sits down, enters, leaves, arrives, "
    "picks anything up, speaks or starts a new activity unless the description "
    "says so. If the description does not say the action finishes, it does not "
    "finish inside this shot.\n"
    "- WHATEVER STATE THE DESCRIPTION PUTS THE CHARACTER IN, THEY ARE STILL IN "
    "IT AT THE LAST DRAWING. Asleep stays asleep. Seated stays seated. Walking "
    "is still walking. The shot after this one has to be able to pick up exactly "
    "where your last pose leaves off.\n"
    # THE HELD SHOT IS A REAL SHOT. Films are full of them and the pressure to
    # make every drawing different is exactly what turns one into an invented
    # action. Give the model somewhere legitimate to put the motion.
    "- A SHOT WHERE NOTHING HAPPENS IS STILL A SHOT. If the description is a "
    "held or still moment — someone sleeping, waiting, watching, listening, a "
    "room being established — the poses are the SMALL involuntary motion of that "
    "state, and nothing more: the chest rising and falling with a breath, a "
    "shoulder settling deeper, the quilt sliding a fraction, fingers curling, "
    "the head easing over on the pillow, hair shifting. That IS the animation. "
    "Do not manufacture an event to fill the time.\n"
    # THE CAMERA IS NAILED DOWN. This used to invite the model to say where the
    # camera had moved to, and it took the invitation: half way through a
    # close-up of a sleeping man it would call for a wide of the whole bedroom,
    # the image model would draw exactly that, and the "flipbook" jumped to a
    # different picture mid-flip (reported, and visible in the strip). A CUT IS
    # A NEW SHOT. Inside one shot the camera holds, and anything that reframes
    # belongs in the next panel on the board, not in this shot's key poses.
    "- THE CAMERA DOES NOT MOVE OR CUT. This is ONE continuous shot from ONE "
    "camera position: the same framing, the same distance, the same angle from "
    "the first drawing to the last. Never widen, never push in, never cut to "
    "another angle, never change what is in frame. Only what is IN FRONT of the "
    "camera changes.\n"
    "- Describe the POSE only: which part of the body moved, in which "
    "direction, how far. Never re-describe the room, the art style, the framing "
    "or the character's identity — those are fixed and supplied separately.\n"
    # Asked to block out a reaction close-up, the model returns eight beats of
    # pure facial expression — "his brow furrows", "his eyes narrow" — and the
    # image model dutifully redraws the same head with different eyebrows. That
    # is not animation, and it is what the user got: "there are no head
    # movements". A drawing has to change SHAPE, not just mood.
    "- EVERY pose must change the body's SILHOUETTE — WITHOUT leaving the state "
    "the description put the character in. The head turns, tilts, lifts, drops "
    "or pushes forward; the neck and shoulders follow it; the weight shifts. Say "
    "which way and how far — 'his head turns fifteen degrees to the left and "
    "dips, chin toward his shoulder'. A sleeping man has a silhouette that "
    "changes while he stays asleep; find the movement inside the state rather "
    "than breaking out of it.\n"
    "- A pose that changes ONLY the face — eyebrows, eyes, mouth — is not a key "
    "drawing and must never be returned on its own. Expression rides ON TOP of "
    "a physical movement, never instead of one. Even a held reaction has the "
    "head drifting, settling or recoiling.\n"
    "- In a CLOSE-UP the head IS the body: it must move in every drawing, "
    "because there is nothing else in frame to carry the motion.\n"
    # A 15° head turn on a figure forty pixels tall is not a key drawing, it is
    # noise — and the model reaches for the same head-and-shoulders vocabulary
    # whatever the framing, because that is what the rules above describe.
    "- MATCH THE MOVEMENT TO THE FRAMING. In a WIDE or establishing shot the "
    "figure is small, so a flick of the eyes reads as nothing: use the whole "
    "body — the mass shifting under a quilt, an arm sliding across, the torso "
    "rolling — and let the room stay exactly as it is. In a close-up the "
    "smallest movement fills the frame, so keep it small.\n"
    "- Consecutive drawings are a fraction of a second apart. Each is a SMALL "
    "step on from the one before — a hand a few inches further, a head turned "
    "slightly more. Never jump to a new action; if the pose could not be "
    "reached from the previous one in a quarter of a second, it is wrong.\n"
    "- Keep each line under 30 words, concrete and physical: 'his shoulder "
    "drops and the quilt slides an inch', not 'he feels uneasy'.\n"
    "- Motion is rarely even. Cluster poses where the action happens and let "
    "the held moments breathe.\n"
    # THE INVARIANT, written by the planner and handed to every drawing. The
    # pose lines are deliberately short and physical, which means each one, read
    # on its own by the image model, says nothing about the state it happens
    # inside — "his shoulder drops an inch" does not say the man is asleep. This
    # one sentence travels with all of them.
    "\nAlso return `hold`: ONE sentence naming what is true in every single "
    "drawing of this shot and must never change — the state the character stays "
    "in, where they stay, and the actions that must NOT appear. Write it as a "
    "rule for the artist: 'Kabir stays lying down, fast asleep under the quilt, "
    "for the whole shot — he never wakes, never opens his eyes, never sits up "
    "and never leaves the bed.'"
)

_PROMPT = """Shot description: {description}
{camera_line}{location_line}{flow}Shot length: {seconds} seconds at {fps}fps = {total} frames in total.

Give me exactly {count} key drawings spanning those {total} frames, in order.
For each, give the frame number it lands on (0 to {last}) and the pose.
Remember: drawing 1 is the panel itself, the motion starts at drawing 2, and the
shot may not go one step further through the story than its description says."""

# THE SAME SHOT, RUN LONGER. Making a shot two seconds longer must not re-plan
# the drawings already on disk: they are paid for, they are what the user
# approved, and a fresh plan for all of them would leave every later drawing
# continuing a motion its predecessors never made. So the existing poses go in
# VERBATIM and the model is asked only for the tail.
_EXTEND_PROMPT = """Shot description: {description}
{camera_line}{location_line}{flow}This shot has been LENGTHENED to {seconds} seconds at {fps}fps = {total} frames in total.

Its first {have} key drawings are ALREADY DRAWN and cannot change. In order:
{drawn}

Give me the {count} key drawings that come AFTER those, continuing the same
motion from where drawing {have} leaves off, and filling the extra time.
For each, give the frame number it lands on (0 to {last}) and the pose.
The shot is now longer, not different: the camera still does not move, and the
shot still may not go one step further through the story than its description
says. If the extra time has no action to fill it, the new drawings are the small
involuntary motion of the state the shot is already in — that IS the animation."""


def _flow_lines(story_context: dict | None) -> str:
    """What runs either side of this shot, for the PLANNER.

    Deliberately blunter than the image model's version of the same facts
    (gemini_client.build_flow_context): the planner's failure mode is not
    drawing the neighbouring shots, it is ANIMATING ITS WAY INTO THEM. The next
    shot's description is the wall this shot's action stops at.
    """
    if not isinstance(story_context, dict):
        return ""
    previous = str(story_context.get("previous") or "").strip()
    following = str(story_context.get("next") or "").strip()
    shot_no = story_context.get("shot_number")
    of = story_context.get("of")

    bits: list[str] = []
    if shot_no and of:
        bits.append(f"This is shot {shot_no} of {of} in the film.")
    if previous:
        bits.append(
            f"The shot BEFORE this one showed: {previous} Your first drawing "
            f"continues from where that left off."
        )
    if following:
        bits.append(
            f"The shot AFTER this one shows: {following} That is where the story "
            f"goes NEXT, in a different shot — it must not happen in yours. Your "
            f"last drawing has to leave the character in a state that shot can "
            f"open on, so do not play any part of it here."
        )
    if not bits:
        return ""
    return "WHERE THIS SHOT SITS (context — do not animate it): " + " ".join(bits) + "\n"


def _beats_schema(count: int):
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "poses": types.Schema(
                type=types.Type.ARRAY,
                min_items=count,
                max_items=count,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "frame": types.Schema(type=types.Type.INTEGER),
                        "pose": types.Schema(type=types.Type.STRING),
                    },
                    required=["frame", "pose"],
                ),
            ),
            "hold": types.Schema(type=types.Type.STRING),
        },
        required=["poses", "hold"],
    )


# What pose 1 always is. The runner copies the panel in rather than drawing it,
# so this is a label for the strip, not an instruction to any model.
OPENING_POSE = "the panel exactly as drawn — the shot starts here"


def respace(poses: list[str], total_frames: int) -> list[dict]:
    """Pose LINES laid evenly across `total_frames`, as a beat plan.

    Where the frame numbers come from when a shot's length changes. The poses
    are the same drawings in the same order — what changed is how much time they
    have to happen in — so they are simply spread over the new span. Pure, and
    the one arithmetic both the fresh and the extended plan finish with, so a
    4s plan re-spaced to 6s is spaced exactly as a 6s plan would have been.
    """
    n = len(poses)
    last = max(0, int(total_frames) - 1)
    return [
        {"frame": round(i * last / max(1, n - 1)), "pose": str(poses[i])}
        for i in range(n)
    ]


def plan_beats(
    description: str,
    duration_seconds: int,
    count: int,
    camera: str = "",
    provider: str | None = None,
    location: str = "",
    story_context: dict | None = None,
    existing_poses: list | None = None,
) -> tuple[list[dict], str]:
    """Break one shot into `count` key poses.

    Returns `(beats, hold)` — the [{frame, pose}, …] plan, and ONE sentence
    naming what stays true across the whole shot (see `_SYSTEM`). Every drawing
    is given that sentence, because a pose line on its own — "his shoulder drops
    an inch" — does not say the man it belongs to is asleep.

    `story_context` is what runs either side of this shot. Without it the
    planner only knows the shot's own sentence, and a shot with no written
    action gets one invented for it: an establishing wide of a sleeping man came
    back as eight drawings of him waking up and sitting on the edge of the bed,
    immediately before a close-up of him still asleep.

    ⚠ `existing_poses` MAKES THIS AN EXTENSION, NOT A RE-PLAN. It is the plan a
    shorter version of this shot was already drawn from, and it is preserved
    WORD FOR WORD: those drawings exist on disk, they were paid for, and the
    user approved them. Only the tail is asked for, and only the FRAME NUMBERS
    of the existing poses move — they have to, because the same drawings now
    span a longer shot. Re-planning the lot (which is what a plain call with a
    bigger `count` does) leaves drawing 17 continuing a motion drawings 1–16
    never made, and there is nothing in the pictures to reveal that until you
    play it. See `respace`.

    Falls back to an evenly-spaced generic plan if the model can't be reached —
    a rough sequence beats refusing to draw anything, and the images are what
    the user is actually here for. On an extension the fallback still keeps
    every existing pose: what is lost is the quality of the new lines, never the
    drawings already on disk.
    """
    from script_breakdown import _model_id, _resolve_provider, _sampling_kwargs, get_client

    total = duration_seconds * FPS
    kept = [str(p).strip() for p in (existing_poses or []) if str(p or "").strip()][:count]
    wanted = count - len(kept)

    if kept and wanted <= 0:
        # The shot got SHORTER (or stayed the same). Nothing to ask for and
        # nothing to pay for: the plan is the first `count` poses it already
        # had, re-spaced over the new span. The drawings past the new end stay
        # on disk untouched, so lengthening it again later is free.
        return respace(kept[:count], total), ""

    if kept:
        prompt = _EXTEND_PROMPT.format(
            description=(description or "").strip() or "the scene as drawn",
            camera_line=f"Camera: {camera.strip()}\n" if (camera or "").strip() else "",
            location_line=f"Location: {location.strip()}\n" if (location or "").strip() else "",
            flow=_flow_lines(story_context),
            seconds=duration_seconds,
            fps=FPS,
            total=total,
            have=len(kept),
            drawn="\n".join(f"{i + 1}. {p}" for i, p in enumerate(kept)),
            count=wanted,
            last=total - 1,
        )
    else:
        prompt = _PROMPT.format(
            description=(description or "").strip() or "the scene as drawn",
            camera_line=f"Camera: {camera.strip()}\n" if (camera or "").strip() else "",
            location_line=f"Location: {location.strip()}\n" if (location or "").strip() else "",
            flow=_flow_lines(story_context),
            seconds=duration_seconds,
            fps=FPS,
            total=total,
            count=count,
            last=total - 1,
        )

    try:
        provider = _resolve_provider(provider)
        client = get_client(provider)
        response = client.models.generate_content(
            model=_model_id(provider),
            contents=[prompt],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                # Only the NEW drawings are asked for on an extension, so the
                # schema counts those — asking for `count` again would refuse
                # the model's answer for being the wrong length.
                response_schema=_beats_schema(wanted),
                response_mime_type="application/json",
                **_sampling_kwargs(),
            ),
        )
        plan = json.loads(response.text or "{}") or {}
        poses = plan.get("poses") or []
        hold = str(plan.get("hold") or "").strip()
    except Exception as e:  # noqa: BLE001 — planning must not sink the whole run
        logger.warning("[sequence] beat planning failed (%s); using even spacing", e)
        poses = []
        hold = ""

    if kept:
        # THE EXISTING DRAWINGS COME FIRST, WHATEVER CAME BACK. The model was
        # asked for the tail; anything it returned for the head is discarded
        # rather than merged, because a pose line that disagrees with the
        # picture already on disk is worse than no pose line at all.
        tail = [
            str((item or {}).get("pose") or "").strip()
            for item in poses[:wanted]
        ]
        tail = [p for p in tail if p]
        while len(tail) < wanted:
            tail.append(
                "the same moment, a fraction later — the motion described in the "
                "shot carried a little further"
            )
        return respace(kept + tail, total), hold

    if not hold:
        # No invariant from the model is not a reason to draw without one. This
        # says the only thing that is always true and is exactly the licence the
        # unplanned run was taking: the shot is the description, and nothing
        # after it.
        hold = (
            "Everything the shot description states stays true in every drawing. "
            "Nothing happens in this shot that the description does not name — "
            "no new action starts, and the story does not move on."
        )

    cleaned: list[dict] = []
    for i, item in enumerate(poses[:count]):
        pose = str((item or {}).get("pose") or "").strip()
        if not pose:
            continue
        try:
            frame = int((item or {}).get("frame"))
        except (TypeError, ValueError):
            frame = round(i * (total - 1) / max(1, count - 1))
        cleaned.append({"frame": max(0, min(total - 1, frame)), "pose": pose})

    # Top up (or wholly replace) with even spacing if the model came up short.
    while len(cleaned) < count:
        i = len(cleaned)
        cleaned.append({
            "frame": round(i * (total - 1) / max(1, count - 1)),
            "pose": (
                "the same moment, a fraction later — the motion described in the "
                "shot carried a little further"
            ),
        })

    cleaned.sort(key=lambda b: b["frame"])
    cleaned = cleaned[:count]
    # POSE 1 IS THE PANEL, whatever the planner wrote for it. It is copied in
    # rather than drawn, so its line is a label — and pinning it here means a
    # planner that ignored the rule cannot leave the strip describing pose 1 as
    # something the picture is not.
    if cleaned:
        cleaned[0] = {"frame": 0, "pose": OPENING_POSE}
    return cleaned, hold


# ---------------------------------------------------------------------------
# Step 2 — draw one key pose (IMAGE model)
# ---------------------------------------------------------------------------
def generate_frame(
    beat: dict,
    n: int,
    total_poses: int,
    total_frames: int,
    *,
    panel: dict,
    panel_image,
    style: str,
    aspect_ratio: str,
    character_refs=None,
    asset_refs=None,
    provider: str | None = None,
    world: dict | None = None,
    character_bible: dict | None = None,
    asset_bible: dict | None = None,
    hold: str = "",
    attempt: int = 1,
):
    """Draw ONE key pose. Returns a PIL image, or None if the model refused.

    `hold` is the shot's invariant from `plan_beats` — the sentence that says
    what is true in every drawing. It matters because the pose line alone is a
    fragment: "his shoulder drops and the quilt slides an inch" never mentions
    that the man is asleep, so nothing stops the drawing from opening his eyes.
    """
    from gemini_client import generate_storyboard_panel

    # The pose is the whole instruction. The panel carries the room, the
    # character and the style in as a composition reference, so repeating any of
    # that here would only fight with the picture.
    # The planner writes sentence fragments and rarely punctuates them, so the
    # pose would otherwise run straight into the sentence after it.
    pose = str(beat["pose"]).strip()
    if pose and pose[-1] not in ".!?":
        pose += "."
    description = (
        f"Key drawing {n + 1} of {total_poses} in one continuous shot, landing "
        f"on frame {beat['frame']} of {total_frames} at {FPS}fps. "
        f"The camera has NOT moved: identical framing, identical distance, "
        f"identical angle, identical background and identical characters as the "
        f"reference image. What HAS changed is the body: {pose} "
        # Was: "the head and shoulders must sit in a visibly different
        # position". True for a close-up and wrong for everything else — in a
        # wide establishing shot of a figure asleep across the room it asks for
        # the one movement that cannot read at that size, and invites the model
        # to enlarge the action until it does. The pose line already says which
        # body part moves and how far; this asks for THAT, visibly.
        f"Draw exactly that movement and make it read at this framing: the part "
        f"of the body the pose names must sit in a visibly different position "
        f"from the reference image — not the same position with a different "
        f"expression. Change NOTHING else. Do not add any action the pose line "
        f"does not name, and do not carry the moment further along than it says."
    )
    return generate_storyboard_panel(
        description=description,
        style=style,
        aspect_ratio=aspect_ratio,
        characters=panel.get("characters", []) or [],
        location=panel.get("location", "") or "",
        camera=panel.get("camera", "") or "",
        reference_images=character_refs or None,
        asset_reference_images=asset_refs or None,
        # THE anchor. See the module docstring — never the previous frame.
        composition_reference_image=panel_image,
        # AND WHAT TO DO WITH IT. Without this the panel arrived carrying the
        # re-style instruction — "keep the positions of everything in frame the
        # same… do not change what is happening" — which is the exact opposite
        # of what a key pose needs, and it won: eight poses came back with the
        # head frozen to within 3 pixels and only the shading moving. This one
        # word is the difference between a flipbook and eight copies.
        composition_purpose="repose",
        # …AND THE FENCE AROUND IT. "repose" pushes for a body that has visibly
        # moved, which on a shot with no written action is an open invitation to
        # invent one. This is the sentence that says how far the movement is
        # allowed to go.
        shot_invariant=hold,
        provider=provider,
        world=world,
        # The same written bible the board panels get, so a face doesn't drift
        # over sixteen drawings of it.
        character_bible=character_bible,
        asset_bible=asset_bible,
        # Distinct per frame, so two key poses can't come back identical, and
        # stable so a resumed run continues the same sequence. A retry of a
        # refused frame shifts the seed off that pose's own number.
        variation=(n + 1) if attempt == 1 else (n + 1) + total_poses * attempt,
    )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def run_panel_sequence(
    job_id: str,
    panel: dict,
    duration_seconds: int,
    *,
    style: str = "custom",
    aspect_ratio: str = "16:9",
    output_dir: str = "output",
    character_ref_paths: dict | None = None,
    asset_ref_paths: dict | None = None,
    provider: str | None = None,
    world: dict | None = None,
    variant: int = 0,
    resume: bool = False,
    limit: int | None = None,
    redraw: list | None = None,
    beats: list | None = None,
    existing_poses: list | None = None,
    hold: str = "",
    cast: list | dict | None = None,
    assets: list | dict | None = None,
    board_panels: list | None = None,
    progress_cb=None,
    cancel_check=None,
) -> dict:
    """Generate the key-pose sequence for ONE panel.

    Args:
        panel: the board panel dict (needs `index`, `description`, and its
            characters/assets/location/camera for consistency).
        resume: keep the drawings already on disk and fill in only the ones that
            are MISSING — including holes in the middle, not just the tail. So
            stopping half way and pressing Generate again costs only the frames
            that were never drawn, and a frame the model refused gets picked up
            by the next run instead of hiding everything after it.
        limit: draw at most this many of the missing poses, then stop. This is
            the PREVIEW: two drawings are enough to see whether the character is
            moving, and finding that out for two images instead of forty is the
            whole point. The rest are drawn later by an ordinary resume.
        redraw: pose numbers to draw AGAIN even though they already exist —
            the per-pose "redo this one" button. Overrides `resume`/`limit`:
            these exact poses are drawn and nothing else is touched, so fixing
            the one drawing that came out wrong costs one image, not sixteen.
        beats: a pose plan to use instead of asking the text model for a new
            one. A redraw passes the plan the sequence was built from, so pose 7
            is redrawn as the SAME pose 7 rather than whatever a fresh planning
            call happens to invent.
        existing_poses: the plan a SHORTER version of this shot was drawn from,
            when `duration_seconds` has since grown. Unlike `beats` it does not
            cover the new length, so it does not skip the planning call — it
            FENCES it: those lines are preserved verbatim and only the tail is
            asked for. This is what makes "make this shot 2s longer" cost the
            new drawings and nothing else. See plan_beats.
        hold: the shot's invariant that goes with those `beats`. Travels with
            them for the same reason: a pose redrawn without it is redrawn
            without the rule that kept it inside the shot. Kept when a planning
            call returns none of its own, so a lengthened shot stays fenced by
            the sentence its first drawings were made under.
        cast / assets: the written continuity bible (see storyboard_pipeline),
            so sixteen drawings of a face stay the same face.
        board_panels: the whole board, so the planner can be told which shots
            run either side of this one. Without it a shot is planned from its
            own sentence alone and animates straight through into the next one —
            see plan_beats.
        progress_cb: {percent, stage, message, done, total}.
        cancel_check: True abandons after the frame in flight.

    Returns:
        {"frames": n, "planned": n, "duration_seconds": s, "fps": 24,
         "stopped": bool, "failed": [i, …], "missing": [i, …],
         "poses": [str, …], "hold": str}
    """
    from PIL import Image

    from storyboard_pipeline import (
        _assets_in,
        _bible_for,
        _gather_refs,
        _load_character_refs,
        _load_refs,
        _variant_dir,
        conform_to_reference,
        conform_to_style,
        lost_the_colour,
        normalise_panel,
        story_context_for,
    )

    duration_seconds = validate_duration(duration_seconds)
    total = frame_count_for(duration_seconds)
    index = int(panel["index"])

    board_dir = os.path.join(output_dir, "_storyboards", job_id)
    out_dir = sequence_dir(board_dir, index)
    os.makedirs(out_dir, exist_ok=True)

    # The panel being animated, as the anchor for every frame.
    source_path = os.path.join(_variant_dir(board_dir, variant), f"panel_{index:02d}.png")
    if not os.path.isfile(source_path):
        raise SequenceError(
            "This shot has no drawn panel yet — draw the panel first, then "
            "generate its images."
        )
    panel_image = Image.open(source_path).convert("RGB")
    # THE ANCHOR HAS TO BE ON-STYLE ITSELF. Boards drawn before palette
    # conformance existed can hold a panel the model rendered in full colour on
    # a greyscale style — the shot this was traced from is exactly that. Feeding
    # that panel in as the look reference teaches every pose the wrong medium,
    # so it is conformed here first. In memory only: the panel on disk is the
    # user's picture and this is not the place to rewrite it.
    panel_image = conform_to_style(panel_image, style)

    char_refs = _gather_refs(
        panel.get("characters", []) or [], _load_character_refs(character_ref_paths), 3
    )
    asset_refs = _gather_refs(
        panel.get("assets", []) or [], _load_refs(asset_ref_paths, "asset"), 3
    )

    def _report(done: int, message: str, stage: str = "drawing"):
        if progress_cb:
            try:
                progress_cb({
                    "percent": int(100 * done / max(1, total)),
                    "stage": stage,
                    "message": message,
                    "done": done,
                    "total": total,
                    "panel": index,
                })
            except Exception:  # noqa: BLE001 — progress must not kill the run
                logger.debug("[sequence] progress callback failed", exc_info=True)

    character_bible = _bible_for(cast)
    asset_bible = _assets_in(panel.get("assets", []) or [], _bible_for(assets))

    # WHAT IS ACTUALLY MISSING. Resuming used to mean "start after the last
    # frame", which quietly skipped holes in the middle and redrew everything
    # past the first one. The job is to end up with all `total` drawings, so
    # work out which of them are not on disk and draw exactly those.
    have = set(frames_on_disk(board_dir, index, total)) if resume else set()
    if redraw:
        # REDO THESE, whatever is on disk. One bad drawing in sixteen should
        # cost one image to fix, not a whole new sequence.
        todo = [n for n in sorted({int(n) for n in redraw}) if 0 <= n < total]
    else:
        todo = [n for n in range(total) if n not in have]
        # PREVIEW. `limit` stops the run after a couple of drawings so the user
        # can see whether the shot is actually moving before buying the other
        # fourteen. It is just a slice of the same `todo` list, so "Continue" is
        # the ordinary resume path and nothing already drawn is paid for twice.
        if limit is not None and limit > 0:
            # `limit` counts DRAWINGS, and pose 1 is a copy of the panel rather
            # than a drawing — so it rides along free instead of eating half the
            # preview budget. A preview still buys two real pictures, and now
            # the first thing to compare them against is the board's own panel.
            budget = limit
            sliced: list[int] = []
            for n in todo:
                if n != 0:
                    if budget <= 0:
                        break
                    budget -= 1
                sliced.append(n)
            todo = sliced

    def _result(stopped: bool, failed: list[int], plan: list | None = None) -> dict:
        """The honest state of the sequence: counted off DISK, never inferred."""
        on_disk = frames_on_disk(board_dir, index, total)
        out = {
            "frames": len(on_disk),
            "planned": total,
            "duration_seconds": duration_seconds,
            "fps": FPS,
            "stopped": stopped,
            "failed": sorted(failed),
            # The holes that remain. The strip shows these as "not drawn" and
            # pressing Generate again fills exactly them.
            "missing": [n for n in range(total) if n not in set(on_disk)],
        }
        # THE POSE PLAN, kept. Two reasons: redrawing pose 7 on its own has to
        # redraw the SAME pose 7 rather than whatever a fresh planning call
        # invents, and the strip can tell the user what a drawing was meant to
        # show — without which "is this one wrong?" is unanswerable.
        #
        # OMITTED, never blanked, when this run had no plan of its own (the
        # "nothing to do" early return). The worker merges, so an empty list
        # here would erase the plan the sequence was actually built from.
        poses = [str(b.get("pose") or "") for b in (plan or beats or [])]
        if poses:
            out["poses"] = poses
        # The invariant is stored with them and for the same reason: a later
        # single-pose redraw has to be fenced by the same sentence the rest of
        # the shot was drawn under, or the one drawing that gets fixed is the
        # one drawing free to wander.
        if str(hold or "").strip():
            out["hold"] = str(hold).strip()
        return out

    if not todo:
        logger.info("[sequence %s/%s] already complete (%d frames)", job_id, index, total)
        return _result(False, [])

    # Re-use the plan this sequence was built from when the caller has it — a
    # redraw of one pose must be the same pose, and it saves a text call.
    if beats and len(beats) >= total:
        _report(len(have), "Re-drawing from the existing pose plan…", "planning")
    else:
        if existing_poses:
            _report(
                len(have),
                f"Carrying the shot on to {duration_seconds}s — "
                f"planning {total - len(existing_poses)} more key pose(s)…",
                "planning",
            )
        else:
            _report(len(have), f"Planning {total} key poses for {duration_seconds}s…", "planning")
        beats, planned_hold = plan_beats(
            description=str(panel.get("description") or ""),
            duration_seconds=duration_seconds,
            count=total,
            camera=str(panel.get("camera") or ""),
            provider=None,  # TEXT_PROVIDER, independent of the image backend
            location=str(panel.get("location") or ""),
            # WHERE THIS SHOT SITS. The planner reached for the next shot's
            # action when it had nothing else to animate; this is what tells it
            # that action already belongs to someone else.
            story_context=story_context_for(board_panels, panel),
            # THE DRAWINGS ALREADY ON DISK, when this is a lengthened shot. They
            # are kept word for word and only the tail is planned.
            existing_poses=existing_poses,
        )
        # ⚠ `or hold` — a lengthened shot keeps the invariant its first drawings
        # were fenced by when this call brings back none of its own. Without it,
        # extending a shot is the one path that draws with the fence down.
        hold = planned_hold or hold

    failed: list[int] = []
    done = len(have)
    for n in todo:
        if cancel_check and cancel_check():
            logger.info("[sequence %s/%s] STOPPED after %d frames", job_id, index, done)
            return _result(True, failed)

        # POSE 1 IS THE PANEL. Not a drawing of the panel — the panel, copied.
        # It used to be generated like any other pose, which meant the first
        # picture in the zip was a fresh interpretation of a shot the user had
        # already approved on the board: different face, different bedding,
        # different everything, reported as "first image totally different".
        # There is no prompt that fixes that, because asking a model to
        # reproduce a picture exactly is the one thing it cannot promise. Copying
        # is exact, instant and free, and it means the flipbook starts on the
        # frame the board shows.
        if n == 0:
            opening = normalise_panel(panel_image, aspect_ratio)
            opening.save(frame_path(board_dir, index, 0), "PNG")
            done += 1
            _report(done, "Key pose 1 is the panel itself.")
            continue

        _report(done, f"Drawing key pose {n + 1} of {total}…")
        image = None
        # One retry per frame, at a different seed, for either of the two ways a
        # drawing comes back unusable: nothing at all (a refusal), or a picture
        # that dropped the panel's colour. A hole or a grey frame in a flipbook
        # is worse than in a board — the motion visibly jumps — and both are
        # one-off lapses rather than anything about this particular pose.
        for attempt in (1, 2):
            try:
                image = generate_frame(
                    beats[n], n, total, duration_seconds * FPS,
                    panel=panel, panel_image=panel_image, style=style,
                    aspect_ratio=aspect_ratio, character_refs=char_refs,
                    asset_refs=asset_refs, provider=provider, world=world,
                    character_bible=character_bible, asset_bible=asset_bible,
                    hold=hold, attempt=attempt,
                )
            except Exception as e:  # noqa: BLE001 — one refused frame must not end the run
                logger.warning("[sequence %s/%s] frame %d failed: %s", job_id, index, n, e)
                image = None
            if image is None:
                continue
            if attempt == 1 and lost_the_colour(image, panel_image):
                logger.info(
                    "[sequence %s/%s] pose %d came back greyscale under a "
                    "coloured panel — one more try.", job_id, index, n,
                )
                continue  # keep it only if the retry is no better
            break

        if image is None:
            failed.append(n)
            logger.warning("[sequence %s/%s] frame %d gave up after 2 attempts", job_id, index, n)
        else:
            # Same normalisation the panels get, so a frame strip doesn't mix
            # picture sizes — and the SOURCE PANEL's palette, so it doesn't mix
            # media either. One pose in the reported eight came back as a colour
            # illustration among seven greyscale ones; the panel is the
            # authority here rather than the style name, because it is the one
            # picture every frame of this shot has to agree with.
            frame = normalise_panel(image, aspect_ratio)
            frame = conform_to_reference(frame, panel_image)
            frame.save(frame_path(board_dir, index, n), "PNG")
            done += 1
            _report(done, f"Drew key pose {n + 1} of {total}.")

    out = _result(False, failed)
    logger.info(
        "[sequence %s/%s] done: %d/%d frames, %d failed, %d still missing",
        job_id, index, out["frames"], total, len(failed), len(out["missing"]),
    )
    return out

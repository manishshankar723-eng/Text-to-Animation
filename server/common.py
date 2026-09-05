"""
common.py — Helpers shared by more than one route module.

These lived in main.py until the animatics router needed them too; importing
them from main would have made the two modules import each other. Nothing here
knows about a specific workflow.
"""

import logging
import os

from fastapi import HTTPException

from . import config
from .auth import CurrentUser
from .jobs import get_store
from .schemas import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)


def get_owned_job(job_id: str, current: CurrentUser) -> Job:
    """Fetch a job, returning 404 if it doesn't exist OR isn't owned by the caller.

    Using 404 (not 403) for the not-owned case avoids leaking which job ids exist.
    """
    job = get_store().get(job_id)
    if job is None or job.owner != current.email:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def board_dir(job_id: str) -> str:
    """Where a storyboard's generated panel PNGs live."""
    return os.path.join(config.OUTPUT_DIR, "_storyboards", job_id)


# ---------------------------------------------------------------------------
# How much disk a project is using — the library list's Size column
# ---------------------------------------------------------------------------
# ⚠ THIS IS A DIRECTORY WALK AND IT RUNS ONCE PER ROW OF THE LIBRARY LIST. A
# board with 42 panels and three style variants is ~130 files; a page of 100
# boards is therefore ~13,000 stat calls, which is fine on a local disk and is
# NOT fine to repeat every five seconds — which is exactly what the libraries do
# while any job is running.
#
# Hence the cache, and hence its key: `(path, version)` where the caller passes
# the job's `updated_at`. A project whose record has not changed cannot have
# grown, so the walk happens once per project per edit rather than once per
# poll. ⚠ THE VERSION IS A CACHE KEY, NOT AN INPUT TO THE ANSWER — pass a job's
# own `updated_at` and nothing else, or two projects will share an entry.
_SIZE_CACHE: dict[tuple[str, str], int] = {}
# Cleared wholesale rather than evicted one at a time: this is a size readout,
# not a hot path, and an LRU would be more machinery than the problem deserves.
_SIZE_CACHE_MAX = 2048

# ⚠ DERIVED CACHES ARE NOT PART OF A PROJECT'S SIZE, and leaving them in would
# have produced a genuinely confusing number: a proxy is written the first time
# a thumbnail is LOOKED AT, so simply opening your library would have made your
# projects grow. Worse, the cache above is keyed on `updated_at`, so the growth
# would not even appear until the next unrelated edit. These folders can be
# deleted at any moment without losing anything (that is what makes them a
# cache), so what they hold is not the user's work and is not reported as it.
_DERIVED_DIRS = frozenset({"_proxies", "_stills"})


def dir_bytes(path: str, version: str = "") -> int:
    """Total size in bytes of every file under `path`. Missing directory → 0.

    Zero is also what a project that has generated nothing yet returns, and the
    client draws both the same way ("—"): "no files" and "no folder" are the
    same news to someone reading a library.

    Errors are swallowed per DIRECTORY, not for the whole walk — one unreadable
    subfolder should cost that subfolder, not turn a 400 MB project into 0.
    """
    key = (path, version)
    hit = _SIZE_CACHE.get(key)
    if hit is not None:
        return hit

    total = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _DERIVED_DIRS:
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        # A file deleted between the listing and the stat, or a
                        # broken link. Skip it; the rest of the folder is real.
                        continue
        except OSError:
            continue

    if len(_SIZE_CACHE) >= _SIZE_CACHE_MAX:
        _SIZE_CACHE.clear()
    _SIZE_CACHE[key] = total
    return total


def variants_of(result: dict) -> tuple[list[dict], int]:
    """Return (variants, active_index) for a storyboard result.

    Older results (pre-restyle) have no `variants` list — synthesise a single
    variant 0 from the flat panels/style so every code path can treat boards
    uniformly.
    """
    result = result or {}
    variants = result.get("variants")
    if not variants:
        variants = [
            {
                "style": result.get("style"),
                "panels": result.get("panels") or [],
                "ok_count": result.get("ok_count", 0),
            }
        ]
    active = int(result.get("active_variant", 0) or 0)
    if active < 0 or active >= len(variants):
        active = 0
    return variants, active


def panel_path(storyboard_id: str, index: int, variant: int = 0) -> str:
    """Absolute-ish path to one drawn panel of a board's style variant."""
    subdir = "" if not variant else f"v{variant}"
    return os.path.join(board_dir(storyboard_id), subdir, f"panel_{index:02d}.png")


# ---------------------------------------------------------------------------
# Working on ONE panel of a board
#
# ⚠ These are here, rather than in main.py where they were written, because the
# ANIMATIC EDITOR now reaches the same two actions — redraw this shot, re-block
# its key poses — from the Properties pane, and a frame in an animatic is a
# REFERENCE to a board panel rather than a copy of one. Two implementations of
# "redraw panel 7" would be two places for the variant handling, the continuity
# bible and the write-back to drift apart, and the animatic's copy would be the
# one nobody noticed had fallen behind. There is one of each; the routes in
# main.py and the proxies in animatics.py both call it.
# ---------------------------------------------------------------------------
def panel_for_index(job: Job, index: int) -> dict | None:
    """The panel dict at `index` on the ACTIVE variant, or None.

    Three-step lookup — by `index` field, then by position, then rebuilt from
    the stored shots — so a panel that never made it into the streamed result
    can still be worked on.
    """
    variants, active = variants_of(job.result or {})
    panels = list(variants[active].get("panels") or [])
    panel = next((p for p in panels if p.get("index") == index), None)
    if panel is None and 0 <= index < len(panels):
        panel = panels[index]
    if panel is not None:
        return dict(panel)
    shots = (job.params or {}).get("shots") or []
    if 0 <= index < len(shots):
        s = shots[index]
        return {
            "index": index,
            "scene_number": s.get("scene_number", 1),
            "shot_number": s.get("shot_number", index + 1),
            "description": s.get("description", ""),
            "characters": s.get("characters", []) or [],
            "dialogue": s.get("dialogue", []) or [],
            "assets": s.get("assets", []) or [],
            "location": s.get("location", "") or "",
            "camera": s.get("camera", "") or "",
            "movement": s.get("movement", "") or "",
            "duration_seconds": int(s.get("duration_seconds") or 0),
            "url": None,
            "failed": True,
        }
    return None


def regenerate_board_panel(
    job: Job,
    index: int,
    *,
    description: str | None = None,
    camera: str | None = None,
    location: str | None = None,
) -> dict:
    """Re-draw ONE panel of a board and write it back. Synchronous, one call.

    Raises `HTTPException` — this is called straight from two routes and the
    failure the caller wants is the one the user needs to read.

    A redraw gets the same continuity the first draw had: the written bible, the
    world, and where this shot sits on the board. Without them the Regenerate
    button was the easiest way in the app to knock a panel off-model.
    """
    from storyboard_pipeline import regenerate_panel

    from . import config

    result = job.result or {}
    # Redraw within the ACTIVE style variant so its subfolder + style are used.
    variants, active = variants_of(result)
    panels = list(variants[active].get("panels") or [])
    variant_style = variants[active].get("style") or (job.params or {}).get("style", "custom")
    shots = (job.params or {}).get("shots") or []
    count = int((job.params or {}).get("count") or len(shots) or len(panels))

    panel = panel_for_index(job, index)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"Panel {index} not found.")

    # Any edited prompt fields, applied before re-drawing and persisted with it.
    if description is not None:
        panel["description"] = description
    if camera is not None:
        panel["camera"] = camera
    if location is not None:
        panel["location"] = location

    try:
        updated = regenerate_panel(
            job_id=job.job_id,
            panel=panel,
            style=variant_style,
            # The board's genre, so a redrawn panel is lit like its neighbours.
            genre=(job.params or {}).get("genre") or "",
            # ⚠ AND ITS BRAND, or the redrawn panel is the one shot in the film
            # carrying a magenta square instead of the logo.
            brand=(job.params or {}).get("brand") or {},
            aspect_ratio=(job.params or {}).get("aspect_ratio", "16:9"),
            output_dir=config.OUTPUT_DIR,
            character_ref_paths=(job.params or {}).get("character_ref_paths") or {},
            asset_ref_paths=(job.params or {}).get("asset_ref_paths") or {},
            variant=active,
            provider=(job.params or {}).get("provider"),
            world=(job.params or {}).get("world") or {},
            cast=(job.params or {}).get("cast") or [],
            assets=(job.params or {}).get("assets") or [],
            board_panels=panels,
        )
    except Exception as e:  # noqa: BLE001 — report clearly
        logger.exception("[storyboard %s] panel %d regen failed", job.job_id, index)
        raise HTTPException(status_code=502, detail=f"Panel regeneration failed: {e}") from None

    # Write the panel back in place (or insert it, keeping index order).
    replaced = False
    for i, p in enumerate(panels):
        if p.get("index") == index:
            panels[i] = updated
            replaced = True
            break
    if not replaced:
        panels.append(updated)
        panels.sort(key=lambda p: p.get("index", 0))

    ok = sum(1 for p in panels if not p.get("failed"))
    variants[active]["panels"] = panels
    variants[active]["ok_count"] = ok
    result = dict(result)
    result["variants"] = variants
    result["active_variant"] = active
    result["panels"] = panels  # mirror the active variant
    result["ok_count"] = ok
    result.setdefault("count", count)
    result.setdefault("style", variant_style)
    result.setdefault("aspect_ratio", (job.params or {}).get("aspect_ratio"))
    get_store().update(job.job_id, result=result)
    return updated


def sequence_summary(job: Job, index: int) -> dict:
    """One panel's key-pose sequence, counted off DISK.

    Counting files rather than trusting the stored summary matters for RESUME:
    after a stop the job says "8 frames" and the disk agrees, but if a run
    crashed mid-write the disk is the honest answer. EVERY planned index is
    checked, not a `while` loop from zero — one refused frame in the middle used
    to hide the ten good drawings after it and make the next Generate redraw
    pictures that had already been paid for.

    Plain dict rather than a response model: two routers wrap it in two
    different shapes, and the arithmetic is the part they share.
    """
    import panel_sequence

    stored = ((job.result or {}).get("sequences") or {}).get(str(index)) or {}
    folder = board_dir(job.job_id)
    planned = int(stored.get("planned") or 0)
    # A sequence with no stored summary (an older board) is still discoverable:
    # scan up to the largest run this endpoint could ever have produced.
    on_disk = panel_sequence.frames_on_disk(
        folder, index, planned or panel_sequence.MAX_FRAMES
    )
    return {
        "index": index,
        "frames": len(on_disk),
        "planned": planned,
        "duration_seconds": int(stored.get("duration_seconds") or 0),
        "fps": int(stored.get("fps") or panel_sequence.FPS),
        "stopped": bool(stored.get("stopped")),
        "failed": list(stored.get("failed") or []),
        "missing": [n for n in range(planned) if n not in set(on_disk)],
        # `?v=<mtime>` so a REDRAWN pose is a different URL. Without it the
        # client — which caches one object URL per path and never re-fetches a
        # path it already holds — kept showing the old drawing, and both "redraw
        # this pose" and a full regenerate looked like they did nothing.
        "urls": [
            f"/storyboards/{job.job_id}/panels/{index}/frames/{n}"
            f"?v={panel_sequence.frame_version(folder, index, n)}"
            for n in on_disk
        ],
        "frame_numbers": on_disk,
        "poses": [str(p) for p in (stored.get("poses") or [])],
        "hold": str(stored.get("hold") or ""),
    }


def submit_sequence_run(
    job: Job,
    index: int,
    duration_seconds: int,
    *,
    resume: bool = False,
    preview: bool = False,
    redraw: list | None = None,
) -> dict:
    """Queue the key-pose run for one panel. Returns `{wanted, total, have, duration}`.

    Raises `HTTPException` for a busy board, a bad duration or a missing panel.

    ⚠ THREE MUTUALLY EXCLUSIVE JOBS share this call, and the difference between
    them is what the run COSTS:

      * `redraw=[n, …]` — redo those exact poses from the plan the sequence was
        already built from. Costs one image each.
      * `resume=True` — fill the HOLES, wherever they fall. Costs the holes.
      * neither — draw the lot.

    And a fourth that falls out of `resume`: a `duration_seconds` LONGER than
    the one the sequence was planned at. The poses on disk are kept, the stored
    plan is handed to the planner as fixed, and only the new tail is drawn — see
    `panel_sequence.plan_beats`. That is "make this shot 2s longer", and it is a
    resume with a bigger target rather than a mode of its own.
    """
    import panel_sequence

    from . import config, worker

    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="This board is already busy — wait for it to finish, or stop it first.",
        )
    try:
        duration = panel_sequence.validate_duration(duration_seconds)
    except panel_sequence.SequenceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    panel = panel_for_index(job, index)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"Shot {index} not found.")
    panel["index"] = index

    variants, active = variants_of(job.result or {})
    params = job.params or {}
    total = panel_sequence.frame_count_for(duration)
    have = (
        len(panel_sequence.frames_on_disk(board_dir(job.job_id), index, total))
        if resume
        else 0
    )

    stored_seq = ((job.result or {}).get("sequences") or {}).get(str(index)) or {}
    stored_poses = [str(p) for p in (stored_seq.get("poses") or [])]
    redraw = sorted({int(n) for n in (redraw or []) if 0 <= int(n) < total})
    hold = str(stored_seq.get("hold") or "")

    # A redraw reuses the plan this sequence was already built from, so the pose
    # that comes back is the same pose — a fresh planning call would invent a
    # different one and the drawing would no longer fit its neighbours.
    beats = None
    if redraw and len(stored_poses) >= total:
        beats = [
            {"frame": round(i * (duration * panel_sequence.FPS - 1) / max(1, total - 1)),
             "pose": stored_poses[i]}
            for i in range(total)
        ]

    # LENGTHENING. The stored plan is shorter than the new target, so it can't
    # be reused wholesale — but it must not be thrown away either. Handed over
    # as a fence: those lines are kept verbatim and only the tail is planned.
    existing_poses = stored_poses if (stored_poses and len(stored_poses) < total) else None

    worker.submit_panel_sequence(job.job_id, {
        "panel": panel,
        "duration_seconds": duration,
        "style": variants[active].get("style") or params.get("style", "custom"),
        "aspect_ratio": params.get("aspect_ratio", "16:9"),
        "output_dir": config.OUTPUT_DIR,
        "character_ref_paths": params.get("character_ref_paths") or {},
        "asset_ref_paths": params.get("asset_ref_paths") or {},
        "provider": params.get("provider"),
        "world": params.get("world") or {},
        # The written bible, so a face holds still across sixteen drawings.
        "cast": params.get("cast") or [],
        "assets": params.get("assets") or [],
        "variant": active,
        "resume": bool(resume),
        "limit": panel_sequence.PREVIEW_POSES if preview else None,
        "redraw": redraw or None,
        "beats": beats,
        "existing_poses": existing_poses,
        "hold": hold,
        # THE WHOLE BOARD, so the pose planner can see which shots run either
        # side of this one. A shot planned from its own sentence alone animates
        # straight on into the next one — see panel_sequence.plan_beats.
        "board_panels": list(variants[active].get("panels") or []),
    })

    if redraw:
        wanted = len(redraw)
    elif preview:
        wanted = min(total - have, panel_sequence.PREVIEW_POSES)
    else:
        wanted = total - have
    logger.info(
        "[storyboard %s] panel %d sequence queued: %ss → %d poses, drawing %d "
        "(%d already drawn)%s",
        job.job_id, index, duration, total, wanted, have,
        f" [REDRAW {redraw}]" if redraw
        else (" [PREVIEW]" if preview else (" [LONGER]" if existing_poses else "")),
    )
    return {
        "wanted": wanted,
        "total": total,
        "have": have,
        "duration_seconds": duration,
        "lengthened": bool(existing_poses),
    }


def write_director_run(job_id: str, run: dict) -> None:
    """Write the 🎬 Veo pass record onto an animatic. Server-owned state.

    ⚠ IT LIVES IN THE JOB'S `result`, NEVER ITS `params`, for exactly the reason
    `AnimaticVeoClip` gives one file over: the editor's autosave rewrites
    `params` wholesale, so a run recorded there would be erased by a save that
    started before it finished — and with it the only statement of what the user
    agreed to pay for. `_director_run_of` in `animatics.py` reads it back.

    ⚠ AND IT IS HERE RATHER THAN IN `animatics.py` BECAUSE `director.py` IS THE
    ONLY WRITER. Two route modules never import each other in this app; anything
    both need lives in this file. Same rule that put `get_owned_job` here.

    ⚠ THERE IS ONLY EVER ONE. A second 🎬 run replaces the first rather than
    appending, because the record answers exactly one question — "is there a pass
    to resume?" — and a list would need a rule for which one wins that nothing
    else has any use for. The CLIPS are the history; they are kept per render.
    """
    store = get_store()
    job = store.get(job_id)
    if job is None:
        return
    result = dict(job.result or {})
    result["director_run"] = run
    try:
        store.update(job_id, result=result)
    except Exception:  # noqa: BLE001 — a lost state write must not kill the run
        logger.exception("[animatic %s] could not persist the director run", job_id)


# ===========================================================================
# THE FILM'S OWN WORDS — what a project is ABOUT, for the passes that write
# about it (the 🎬 Director's reading and the ✨ AI Editor's turn).
# ===========================================================================
# ⚠ THIS EXISTS BECAUSE THE SOUND CAME OUT OF A DIFFERENT FILM. Asked to score a
# Diwali puja board — marigolds, diyas, a rangoli, a woman at a decorated puja
# table — the model returned "pop bubble", "camera shutter", "digital beep",
# "mouse click", "glitch static", and a bed of "upbeat energetic corporate pop
# vlog". That is not a taste failure. It is the only answer available to
# something that was handed this and nothing else:
#
#     - Title: (untitled)
#     - 14 shot(s), 0:28 total, 9:16 at 24fps
#     1. [2.0s] Shot 1
#     2. [2.0s] Shot 2   … fourteen times
#
# ⚠ THE WORDS EXISTED THE WHOLE TIME — ON THE BOARD, NOT ON THE FRAME.
# `boardFrom` in the browser sends `description: frame.description ||
# frame.prompt`, and `AnimaticFrame` HAS NO SUCH FIELD (`frameForSave` carries
# none either), so that key has been "" on every turn since it was written. What
# a shot is OF lives on the storyboard panel the frame REFERENCES — its `src`
# carries `{storyboard_id, index}` — together with its location, the board's
# genre and world, and the script the whole thing came from.
#
# ⚠ AND ONE FILL FIXES BOTH FEATURES, which is why it is here rather than in
# either route. `board_digest` (chat) and `shot_digest` (Director) both read
# `board["shots"][i]["description"]`; neither needs to know where it came from.
#
# ⚠ OWNER-CHECKED, EXACTLY LIKE `_voice_lines_of` IN `animatics.py`. Frames are
# user-editable JSON, so a crafted `storyboard_id` would otherwise read another
# account's script. Same shape of guard, same reason.

# How much of each is worth spending tokens on. A description is a sentence; a
# logline is the top of the script — enough to say what the film IS, not the
# whole screenplay.
FILM_DESCRIPTION_CHARS = 240
FILM_LOGLINE_CHARS = 400
FILM_WORLD_CHARS = 300


def _film_text(value, limit: int) -> str:
    """One line, whitespace collapsed and clipped. Never raises on a non-string."""
    out = " ".join(str(value or "").split())
    return out[:limit].rstrip()


def _world_text(world) -> str:
    """A board's `world` as one readable line.

    Its shape has changed across versions (a string once, an object since), so
    this reads either and never assumes a key is present.
    """
    if isinstance(world, str):
        return _film_text(world, FILM_WORLD_CHARS)
    if not isinstance(world, dict):
        return ""
    bits = []
    for key in ("setting", "place", "period", "era", "time", "look", "notes", "summary"):
        value = _film_text(world.get(key), 120)
        if value:
            bits.append(value)
    return _film_text(" · ".join(bits), FILM_WORLD_CHARS)


def fill_board_words(board: dict, job: Job) -> dict:
    """Put the film's own words into the board the browser sent.

    Returns a NEW board dict; the caller's is not touched. `director.py` hashes
    the board it was given as part of its determinism claim, and a helper that
    mutated its argument would make that hash depend on call order.

    What it fills, and only where the browser left a blank:

      * ``shots[i].description`` — what the panel is OF, from the board it came
        from. A shot the user described themselves keeps their words.
      * ``shots[i].location``    — where it happens.
      * ``film``                 — one header for the whole project: the board's
        real title, its genre, its world, the top of its script, and the market
        it was written for. This is the part that answers *what KIND of film is
        this*, which is the question every sound cue depends on.

    ⚠ IT NEVER FAILS THE REQUEST. A deleted board, a src pointing at another
    account's job, a result shaped by an older version — all of them mean "no
    extra words", never an error. The turn still happens, and the model is told
    plainly that it is working blind (see `board_digest`).
    """
    if not isinstance(board, dict):
        return {}
    shots = [dict(s) if isinstance(s, dict) else {} for s in (board.get("shots") or [])]
    out = dict(board)

    store = get_store()
    boards: dict[str, Job | None] = {}

    def board_job(board_id: str) -> Job | None:
        """The storyboard behind a src — owner-checked, and fetched once."""
        if board_id in boards:
            return boards[board_id]
        found = None
        try:
            candidate = store.get(board_id)
            # ⚠ THE OWNER CHECK IS THE POINT, not a formality.
            if (
                candidate is not None
                and candidate.owner == job.owner
                and candidate.kind == JobKind.STORYBOARD
            ):
                found = candidate
        except Exception:  # noqa: BLE001 — a store miss is "no words", never a 500
            logger.warning("[film-words] could not read board %s", board_id)
        boards[board_id] = found
        return found

    # ⚠ THE PROJECT'S OWN SOURCE FIRST, so a film header exists even for a
    # project whose frames have since been replaced by uploads. The animatic
    # records it when it is made from a board.
    source_id = str((job.params or {}).get("source_storyboard_id") or "")
    header_job = board_job(source_id) if source_id else None

    filled = 0
    for shot in shots:
        src = shot.get("src")
        if not isinstance(src, dict) or src.get("kind") not in ("panel", "pose"):
            continue
        board_id = str(src.get("storyboard_id") or "")
        index = src.get("index")
        if not board_id or not isinstance(index, int):
            continue
        found = board_job(board_id)
        if found is None:
            continue
        header_job = header_job or found
        panel = panel_for_index(found, index)
        if not panel:
            continue
        # ⚠ ONLY WHERE THE USER LEFT A BLANK. A description they typed on the
        # timeline is what they mean this shot to be; the panel's is only what it
        # started as.
        if not _film_text(shot.get("description"), 1):
            described = _film_text(panel.get("description"), FILM_DESCRIPTION_CHARS)
            if described:
                shot["description"] = described
                filled += 1
        if not _film_text(shot.get("location"), 1):
            where = _film_text(panel.get("location"), 80)
            if where:
                shot["location"] = where

    out["shots"] = shots

    if header_job is not None:
        params = header_job.params or {}
        market = params.get("market") if isinstance(params.get("market"), dict) else {}
        film = {
            # ⚠ THE BOARD'S TITLE, NOT THE PROJECT'S. A film exported from a
            # board is called "Untitled Project" until somebody renames it, and
            # the board it came from has had the real name all along.
            "title": _film_text(header_job.character_name, 120),
            "genre": _film_text(params.get("genre"), 60),
            "world": _world_text(params.get("world")),
            # The top of the script. Not the whole thing: what the film IS, said
            # once, is what a sound or a treatment decision needs.
            "logline": _film_text(params.get("script"), FILM_LOGLINE_CHARS),
            "market": _film_text(
                (market or {}).get("label") or (market or {}).get("country"), 60
            ),
            "language": _film_text((market or {}).get("language"), 40),
        }
        if any(film.values()):
            out["film"] = film

    if filled:
        logger.info(
            "[film-words] %s: filled %d of %d shot description(s) from the board.",
            job.job_id, filled, len(shots),
        )
    return out

"""
video_assemble.py — Step 3 of Animatics → Final Video: the clips become a cut.

Takes the per-shot MP4s that Veo rendered and joins them, in order, into one
file. Nothing here calls an AI backend: assembly is free, so a user can re-cut,
re-order and re-assemble as often as they like without spending anything. Only
the RENDER step (video_client.py) costs money.

Two join modes, and the difference matters:

    "cut"       — stream copy via the concat demuxer. No re-encode, so it is
                  near-instant and lossless. Requires every clip to share a
                  codec/size/framerate, which Veo's output does when the shots
                  share settings. Falls back to re-encoding if that fails.

    "crossfade" — xfade + acrossfade between neighbours. Re-encodes, so it costs
                  time, and it SHORTENS the video: each transition overlaps two
                  clips by `transition_ms`.

ffmpeg discovery, the progress/cancel runner and the user-facing error type are
imported from animatic.py rather than reimplemented — there is one ffmpeg
integration in this codebase and this module is a second caller of it, not a
second implementation.
"""

import json
import logging
import os
import shutil
import subprocess

from animatic import AnimaticError, ffmpeg_exe, run_ffmpeg

logger = logging.getLogger(__name__)


class AssembleError(AnimaticError):
    """Assembly failed for a reason worth showing the user verbatim.

    Subclasses AnimaticError so the worker's existing "show this one verbatim"
    branch catches it without a second except clause.
    """


# x264 CRF per quality label — same ladder the animatic exporter uses, so a
# final video and its animatic look consistent at the same setting.
_CRF = {"high": 18, "medium": 23, "low": 28}


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def _ffprobe_exe() -> str | None:
    """ffprobe sits next to ffmpeg when it exists at all; None if it doesn't.

    Returns None rather than raising: ffprobe is a NICE-TO-HAVE here (durations
    come from the caller), and the common install has none at all —
    `imageio-ffmpeg` ships the ffmpeg binary only.
    """
    explicit = os.environ.get("FFPROBE_BINARY", "").strip()
    if explicit:
        return explicit
    found = shutil.which("ffprobe")
    if found:
        return found
    try:
        base = os.path.dirname(ffmpeg_exe())
    except AnimaticError:
        return None  # no ffmpeg either; the assembly itself will say so
    sibling = os.path.join(base, "ffprobe.exe" if os.name == "nt" else "ffprobe")
    return sibling if os.path.isfile(sibling) else None


def clip_duration_ms(path: str) -> int:
    """Length of one rendered clip, or 0 if it can't be determined.

    Used to report the assembled length and to lay out the timeline. Returns 0
    rather than raising: a missing ffprobe must not stop an assembly, it only
    makes the reported duration less exact.
    """
    probe = _ffprobe_exe()
    if not probe or not os.path.isfile(path):
        return 0
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        seconds = float(json.loads(out.stdout)["format"]["duration"])
        return int(round(seconds * 1000))
    except Exception:  # noqa: BLE001 — a failed probe is not a failed assembly
        logger.debug("ffprobe failed for %s (ignored)", path, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Concat list
# ---------------------------------------------------------------------------
def _write_concat_list(path: str, names: list[str]) -> None:
    """Write the concat demuxer's list file.

    Paths are written relative to the list file's own directory and single
    quotes are escaped, because the demuxer's parser treats them specially.
    """
    with open(path, "w", encoding="utf-8") as f:
        for name in names:
            safe = name.replace("'", r"'\''")
            f.write(f"file '{safe}'\n")


def _copy_concat(
    clips: list[str],
    out_path: str,
    work_dir: str,
    total_ms: int,
    with_audio: bool,
    progress_cb=None,
    cancel_check=None,
) -> bool:
    """Join by stream copy. Fast and lossless; returns False if it was cancelled.

    Raises AnimaticError when ffmpeg rejects the clips — the caller then retries
    with a re-encode, which tolerates mismatched inputs.
    """
    list_path = os.path.join(work_dir, "concat.txt")
    _write_concat_list(list_path, [os.path.relpath(c, work_dir) for c in clips])

    cmd = [
        ffmpeg_exe(), "-hide_banner", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy",
    ]
    if not with_audio:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", out_path]
    return run_ffmpeg(cmd, total_ms, progress_cb, cancel_check)


def _reencode_concat(
    clips: list[str],
    out_path: str,
    total_ms: int,
    *,
    width: int,
    height: int,
    fps: int,
    quality: str,
    with_audio: bool,
    progress_cb=None,
    cancel_check=None,
) -> bool:
    """Join by re-encoding through the concat FILTER.

    Slower than a stream copy but indifferent to clips that disagree about size,
    frame rate or codec — which is what happens the moment a user renders one
    hero shot at 1080p and the rest at 720p.
    """
    cmd = [ffmpeg_exe(), "-hide_banner", "-y"]
    for clip in clips:
        cmd += ["-i", clip]

    # Normalise every input to the same size/fps/sample rate before concat —
    # the filter requires it, and silently mismatched inputs produce a garbled
    # output rather than an error.
    parts = []
    for i in range(len(clips)):
        parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        if with_audio:
            # A clip rendered with audio off has no audio stream at all, so a
            # silent track is substituted; without it concat drops out of sync.
            parts.append(f"[{i}:a]aresample=48000,asetpts=N/SR/TB[a{i}]")

    if with_audio:
        streams = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        parts.append(f"{streams}concat=n={len(clips)}:v=1:a=1[vout][aout]")
    else:
        streams = "".join(f"[v{i}]" for i in range(len(clips)))
        parts.append(f"{streams}concat=n={len(clips)}:v=1:a=0[vout]")

    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if with_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium",
        "-crf", str(_CRF.get(quality, 18)),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-movflags", "+faststart", "-progress", "pipe:1", out_path,
    ]
    return run_ffmpeg(cmd, total_ms, progress_cb, cancel_check)


def _crossfade_concat(
    clips: list[str],
    durations_ms: list[int],
    out_path: str,
    total_ms: int,
    *,
    width: int,
    height: int,
    fps: int,
    quality: str,
    transition_ms: int,
    with_audio: bool,
    progress_cb=None,
    cancel_check=None,
) -> bool:
    """Join with a crossfade between every neighbouring pair.

    xfade takes an `offset` measured on the OUTPUT timeline, which shrinks by
    one transition per join — so the offsets are accumulated rather than summed
    from the input durations. Getting this wrong is what makes the last clip
    play over black.
    """
    fade_s = transition_ms / 1000.0
    cmd = [ffmpeg_exe(), "-hide_banner", "-y"]
    for clip in clips:
        cmd += ["-i", clip]

    parts = []
    for i in range(len(clips)):
        parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        if with_audio:
            parts.append(f"[{i}:a]aresample=48000[a{i}]")

    # Chain the fades: v0⊕v1 → x1, x1⊕v2 → x2, …
    prev_v = "v0"
    prev_a = "a0"
    running_ms = durations_ms[0]
    for i in range(1, len(clips)):
        offset_s = max(0.0, (running_ms - transition_ms) / 1000.0)
        out_v = f"xv{i}"
        parts.append(
            f"[{prev_v}][v{i}]xfade=transition=fade:duration={fade_s:.3f}:"
            f"offset={offset_s:.3f}[{out_v}]"
        )
        prev_v = out_v
        if with_audio:
            out_a = f"xa{i}"
            parts.append(f"[{prev_a}][a{i}]acrossfade=d={fade_s:.3f}[{out_a}]")
            prev_a = out_a
        # The output grew by this clip MINUS the overlap it was faded into.
        running_ms += durations_ms[i] - transition_ms

    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{prev_v}]"]
    if with_audio:
        cmd += ["-map", f"[{prev_a}]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium",
        "-crf", str(_CRF.get(quality, 18)),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-movflags", "+faststart", "-progress", "pipe:1", out_path,
    ]
    return run_ffmpeg(cmd, max(1, running_ms), progress_cb, cancel_check)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
_FRAME_SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def assemble_final_video(
    job_id: str,
    clips: list[str],
    *,
    durations_ms: list[int] | None = None,
    aspect_ratio: str = "16:9",
    fps: int = 24,
    quality: str = "high",
    transition: str = "cut",
    transition_ms: int = 400,
    include_clip_audio: bool = True,
    output_dir: str = "output",
    progress_cb=None,
    cancel_check=None,
) -> dict:
    """Join `clips` (absolute paths, in play order) into one MP4.

    Writes to output/_final_videos/{job_id}/final.mp4.

    Args:
        clips: rendered shot MP4s in order. A clip whose file is missing is
            SKIPPED and named in the result, so one deleted file can't fail the
            whole cut — the same forgiveness build_animatic gives a lost panel.
        durations_ms: how long each clip is, positionally matching `clips`. Pass
            it whenever you know (the render settings say so) — see the note in
            the body about why probing is not a reliable substitute.
        transition: "cut" (stream copy, instant) or "crossfade" (re-encode).
        progress_cb: called with {percent, stage, message}.
        cancel_check: called during encoding; True abandons the assembly.

    Returns:
        {"video_path", "duration_ms", "clip_count", "skipped", "size_bytes",
         "width", "height", "fps", "stopped"}

    Raises:
        AssembleError: with a message written for the user.
    """
    # Keep each clip with the length the CALLER knows it to be, so dropping a
    # missing file can't shift the remaining clips onto the wrong durations.
    paired = list(zip(clips, durations_ms or [0] * len(clips)))
    present = [c for c, _ in paired if c and os.path.isfile(c)]
    known = [d for c, d in paired if c and os.path.isfile(c)]
    skipped = [os.path.basename(c) for c, _ in paired if not (c and os.path.isfile(c))]
    if not present:
        raise AssembleError(
            "None of this project's shots have a rendered clip yet. Render at "
            "least one shot on step 2 before assembling."
        )

    out_dir = os.path.join(output_dir, "_final_videos", job_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "final.mp4")

    def _report(percent: int, stage: str, message: str = ""):
        if progress_cb:
            try:
                progress_cb({"percent": percent, "stage": stage, "message": message})
            except Exception:  # noqa: BLE001 — progress must not kill an assembly
                logger.debug("[%s] progress callback failed (ignored)", job_id, exc_info=True)

    # Durations come from the CALLER first, which knows what it asked Veo for.
    # ffprobe is only a refinement: `imageio-ffmpeg` ships ffmpeg WITHOUT it, so
    # on a bundled-binary install there is no probe at all. Depending on one
    # made an assembled 6s cut report itself as 12s — and, far worse, fed
    # crossfade the wrong offsets, which puts black frames in the output.
    _report(2, "measuring", f"Measuring {len(present)} clip(s)…")
    durations = []
    for path, claimed in zip(present, known):
        if claimed and claimed > 0:
            durations.append(int(claimed))
            continue
        probed = clip_duration_ms(path)
        # Last resort: Veo's shortest clip length. Only reached when the caller
        # didn't say and there is no ffprobe.
        durations.append(probed if probed > 0 else 4000)
    total_ms = sum(durations)

    width, height = _FRAME_SIZES.get(aspect_ratio, _FRAME_SIZES["16:9"])

    def _enc(fraction: float):
        _report(5 + int(90 * fraction), "encoding", "Assembling the sequence…")

    crossfading = transition == "crossfade" and len(present) > 1
    if crossfading:
        # Every join overlaps two clips, so the cut is shorter than the sum.
        total_ms = max(1, total_ms - transition_ms * (len(present) - 1))

    try:
        if crossfading:
            _report(5, "encoding", "Crossfading between shots…")
            finished = _crossfade_concat(
                present, durations, out_path, total_ms,
                width=width, height=height, fps=fps, quality=quality,
                transition_ms=transition_ms, with_audio=include_clip_audio,
                progress_cb=_enc, cancel_check=cancel_check,
            )
        else:
            _report(5, "encoding", "Joining the shots…")
            try:
                finished = _copy_concat(
                    present, out_path, out_dir, total_ms,
                    include_clip_audio, _enc, cancel_check,
                )
            except AnimaticError as e:
                # Mismatched clips (a hero shot at 1080p among 720p ones) make
                # the demuxer refuse. Re-encoding always works, so take the
                # slower road rather than telling the user to re-render.
                logger.info(
                    "[%s] stream-copy concat rejected (%s); re-encoding instead.",
                    job_id, e,
                )
                _report(5, "encoding", "Clips differ — re-encoding to match…")
                finished = _reencode_concat(
                    present, out_path, total_ms,
                    width=width, height=height, fps=fps, quality=quality,
                    with_audio=include_clip_audio,
                    progress_cb=_enc, cancel_check=cancel_check,
                )
    except AnimaticError:
        raise
    except Exception as e:  # noqa: BLE001 — anything else becomes user-facing
        raise AssembleError(f"The sequence couldn't be assembled: {e}") from e

    if not finished:
        return {"stopped": True, "clip_count": 0, "duration_ms": 0, "skipped": skipped}

    if not os.path.isfile(out_path):
        raise AssembleError("ffmpeg reported success but wrote no file.")

    _report(100, "done", "")
    size = os.path.getsize(out_path)
    logger.info(
        "[%s] assembled %d clip(s) → %.1f MB (%.1fs)",
        job_id, len(present), size / 1_048_576, total_ms / 1000,
    )
    return {
        "video_path": out_path,
        "duration_ms": total_ms,
        "clip_count": len(present),
        "skipped": skipped,
        "size_bytes": size,
        "width": width,
        "height": height,
        "fps": fps,
        "stopped": False,
    }

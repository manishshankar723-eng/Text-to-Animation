"""
export_presets.py — "make me a file for X", as a named set of export settings.

Everything here is settings the export dialog already has: an aspect ratio, a
short edge, a frame rate, a quality and a container. A preset is nothing more
than a NAME for one combination of them, so choosing one and then changing a
field by hand is not a special case — it just stops matching (`match()` returns
"" and the dialog says "Custom").

Three rules the table follows, and they are the whole design:

1. **A preset states only what it means.** GIF and Still deliberately do NOT
   state an aspect ratio: a GIF of a 16:9 project is a 16:9 GIF, and having the
   Export dialog silently reshape the whole film because you wanted an animated
   thumbnail would be absurd. The platform presets DO state one, because a
   9:16 file is the entire point of choosing TikTok.

2. **Applying a preset is a plain dict update**, never a rebuild. A field the
   preset doesn't state keeps whatever the project had — which is what makes
   `apply()` safe to run over a project saved by any earlier version.

3. **⚠ TWIN FILE: `client/src/animatic/export_presets.js`.** The dialog has to
   show the same sizes and the same frame rates the encoder will use, before
   anything is encoded — the same reason `BASE_SIZES` in `AnimaticEditor.jsx`
   mirrors `resolve_size()`. The two tables are compared field for field by
   `tests/export_perf_check.py`, which runs the JS half through node.

`container` is the one field that isn't just a number, and it is honoured in
`animatic.py`:

    "mp4"  H.264 + AAC, the path everything took before presets existed.
    "gif"  palette + paletteuse, silent, looping. No audio, ever — a GIF has
           no audio track, so `include_audio` is forced off rather than being
           set and quietly ignored.
    "png"  ONE frame, written straight out of Pillow at `still_ms`. No ffmpeg
           and no encode: the still an export would have shown at that moment
           IS the file, so a poster frame cannot disagree with the video.
"""

from __future__ import annotations

# The short edge a GIF is capped at. GIFs are 256 colours and enormous per
# pixel; 480 is the size everything that eats them (chat, docs, issue threads)
# shows them at anyway.
GIF_SHORT_EDGE = 480
# And its frame rate. 12 is the usual animation-on-twos rate and halves the file
# against 24 for something nobody watches full-screen.
GIF_FPS = 12

# ⚠ ORDER IS THE DIALOG'S ORDER. Keep it in step with the JS twin.
PRESETS: tuple[dict, ...] = (
    {
        "id": "youtube",
        "label": "YouTube",
        "hint": "1080p · 16:9 · 30 fps · MP4",
        "aspect_ratio": "16:9",
        "resolution": 1080,
        "fps": 30,
        "quality": "high",
        "container": "mp4",
        "audio": True,
    },
    {
        "id": "tiktok",
        "label": "TikTok",
        "hint": "1080×1920 · 9:16 · 30 fps · MP4",
        "aspect_ratio": "9:16",
        "resolution": 1080,
        "fps": 30,
        "quality": "high",
        "container": "mp4",
        "audio": True,
    },
    {
        # ⚠ TECHNICALLY IDENTICAL TO TIKTOK, and that is not an oversight worth
        # "fixing" by inventing a difference. Both want a 1080×1920 30fps H.264
        # file; they are two entries because the person exporting is thinking
        # about a destination, not about a codec, and a dialog that answers
        # "where is this going" is worth one duplicated row.
        "id": "reels",
        "label": "Instagram Reels",
        "hint": "1080×1920 · 9:16 · 30 fps · MP4",
        "aspect_ratio": "9:16",
        "resolution": 1080,
        "fps": 30,
        "quality": "high",
        "container": "mp4",
        "audio": True,
    },
    {
        "id": "gif",
        "label": "Animated GIF",
        "hint": f"{GIF_SHORT_EDGE}p · {GIF_FPS} fps · silent · keeps your shape",
        # No aspect_ratio on purpose — see rule 1.
        "resolution": GIF_SHORT_EDGE,
        "fps": GIF_FPS,
        "container": "gif",
        "audio": False,
    },
    {
        "id": "still",
        "label": "Still image (PNG)",
        "hint": "one frame, at the playhead · keeps your shape",
        "resolution": 1080,
        "container": "png",
        "audio": False,
    },
)

# The fields a preset is allowed to state. Anything else in a row is labelling.
SETTING_FIELDS = ("aspect_ratio", "resolution", "fps", "quality", "container")

CONTAINERS = ("mp4", "gif", "png")
# What each container is written as. `build_animatic` names the output file from
# this, and the download route serves whichever of them exists.
CONTAINER_EXT = {"mp4": "mp4", "gif": "gif", "png": "png"}
CONTAINER_MIME = {"mp4": "video/mp4", "gif": "image/gif", "png": "image/png"}
# Containers that can carry sound. A GIF and a PNG cannot, so `apply()` turns
# `include_audio` off rather than leaving a checkbox on that means nothing.
SILENT_CONTAINERS = ("gif", "png")


def preset(preset_id: str) -> dict | None:
    """One row of the table, or None for an id we don't know.

    Unknown ids are not an error anywhere: a project saved by a newer client can
    name a preset this build has never heard of, and the right answer is to fall
    back to the settings already on it — same rule an unrecognised transition
    `kind` or keyframe `ease` follows.
    """
    key = (preset_id or "").strip().lower()
    return next((p for p in PRESETS if p["id"] == key), None)


def apply(preset_id: str, settings: dict | None = None) -> dict:
    """`settings` with the named preset written over it. A COPY, never in place.

    A preset that doesn't state a field leaves it alone, so the project's own
    aspect ratio survives a GIF export and its background colour, fit, labels
    and end_at survive all of them.

    An unknown id returns the settings unchanged apart from the `preset` name
    itself, which is deliberate: the dialog can then show "Custom" without the
    project having been mangled on the way through.
    """
    out = dict(settings or {})
    row = preset(preset_id)
    out["preset"] = row["id"] if row else ""
    if not row:
        return out
    for field in SETTING_FIELDS:
        if field in row:
            out[field] = row[field]
    # A silent container has no audio track to include, so the flag is settled
    # here rather than being left on for the encoder to ignore.
    if out.get("container") in SILENT_CONTAINERS:
        out["include_audio"] = False
    return out


def match(settings: dict | None) -> str:
    """Which preset these settings ARE, or "" for none of them.

    Compared on the fields the preset states and nothing else, which is what
    makes this the exact inverse of `apply()`: change a stated field by hand and
    the dialog drops to Custom; change the background colour and it does not.
    """
    s = settings or {}
    for row in PRESETS:
        if all(_same(s.get(f), row[f]) for f in SETTING_FIELDS if f in row):
            return row["id"]
    return ""


def _same(a, b) -> bool:
    """Field equality that survives a JSON round trip (30 vs "30", 1080 vs 1080.0)."""
    if isinstance(b, int) and not isinstance(b, bool):
        try:
            return int(a) == b
        except (TypeError, ValueError):
            return False
    return (a or "") == b


def normalise_container(value: str | None) -> str:
    """A container name we will actually encode, defaulting to mp4.

    Anything unrecognised becomes "mp4" rather than failing the export: it is
    the format every animatic has ever been written in, so falling back to it
    can only ever produce the file the user was already expecting.
    """
    key = (value or "").strip().lower()
    return key if key in CONTAINERS else "mp4"


def output_name(container: str | None) -> str:
    """The file one export is written as, inside its own job directory."""
    return f"animatic.{CONTAINER_EXT[normalise_container(container)]}"

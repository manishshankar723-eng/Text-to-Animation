"""
youtube_research.py — read a real YouTube channel so the planner isn't guessing.

The user pastes their channel link and the agent goes and looks at it. TWO ways,
tried best-first, because they are good at different things:

  1. YouTube Data API v3  (needs YOUTUBE_API_KEY)
     EXACT numbers: subscriber count, per-video view counts, upload dates, so
     publishing rhythm and best-performers are measured rather than estimated.

  2. Gemini URL context  (no key — uses the Gemini/Vertex credentials already
     configured for everything else)
     The model FETCHES the channel page and reads it. Verified working against a
     real channel: it returns the channel name, what the channel is about and
     recent video titles. What it does NOT reliably give is precise subscriber
     or view counts, so those are never claimed from this source.

THE RULE, whichever path ran: never invent channel data. Each result carries a
`source`, and `as_context()` tells the agent exactly what it is allowed to state
from that source — exact figures only when they came from the Data API, topics
and titles when they came from the page, and "ask the user" when neither worked.
A plan built on a made-up subscriber count is worse than no plan, because the
user cannot tell.

The API key is optional. Getting one: Google Cloud console → enable "YouTube
Data API v3" → create an API key → put YOUTUBE_API_KEY in .env. Free quota is
10,000 units/day; a full research pass costs ~103 units (~95 channels a day).
"""

import logging
import os
import re
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_ROOT = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 15
# How many recent uploads to read. Enough to see a pattern in titles and
# cadence; small enough to stay cheap and quick.
RECENT_VIDEOS = 15


class ChannelLookupError(Exception):
    """Raised when a channel link can't be resolved. Message is user-facing."""


def api_key() -> str:
    return (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def is_configured() -> bool:
    """True when EXACT-figure research (the Data API) is available."""
    return bool(api_key())


def can_research() -> bool:
    """True when the channel can be read at all.

    Almost always true: the Gemini fallback needs no extra credentials beyond
    the ones the app already uses to generate everything else.
    """
    return True


# ---------------------------------------------------------------------------
# Path 2 — read the channel page with Gemini's URL-context tool
# ---------------------------------------------------------------------------
# Asked for in a fixed plain-text layout rather than JSON: structured output and
# tool use don't reliably combine, and a lenient parse of three labelled lines is
# sturdier than a schema that might not be honoured.
_READ_PROMPT = (
    "Open this YouTube channel and read it: {url}\n\n"
    "Reply in EXACTLY this layout, nothing else:\n"
    "NAME: <the channel's name>\n"
    "ABOUT: <2-3 sentences on what this channel posts, its topics and tone>\n"
    "TITLES:\n"
    "- <a recent video title>\n"
    "- <another>\n"
    "(up to 12 titles, copied exactly as they appear)\n\n"
    "Only report what you can actually see on the page. Do NOT state subscriber "
    "counts or view counts. If the page cannot be opened, reply with the single "
    "line: CANNOT ACCESS"
)


def _read_with_gemini(url: str) -> dict | None:
    """Fetch and read the channel page via Gemini. None if it couldn't."""
    try:
        from google.genai import types

        from script_breakdown import _model_id, _resolve_provider, get_client

        provider = _resolve_provider(None)
        client = get_client(provider)
        response = client.models.generate_content(
            model=_model_id(provider),
            contents=[_READ_PROMPT.format(url=url)],
            config=types.GenerateContentConfig(
                # url_context ONLY. Adding google_search alongside it blew past
                # the tool-output size limit on a real channel page.
                tools=[types.Tool(url_context=types.UrlContext())],
                temperature=0.0,
            ),
        )
        text = (getattr(response, "text", "") or "").strip()
    except Exception as e:  # noqa: BLE001 — degrade, never break the workflow
        logger.warning("[youtube] Gemini channel read failed: %s", e)
        return None

    if not text or "CANNOT ACCESS" in text.upper()[:200]:
        return None

    name, about, titles = "", "", []
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        upper = s.upper()
        if upper.startswith("NAME:"):
            name, section = s[5:].strip(), None
        elif upper.startswith("ABOUT:"):
            about, section = s[6:].strip(), "about"
        elif upper.startswith("TITLES:"):
            section = "titles"
        elif s.startswith(("-", "*", "•")):
            titles.append(s.lstrip("-*• ").strip())
        elif section == "about":
            about = f"{about} {s}".strip()

    if not (name or about or titles):
        # Layout wasn't followed — keep the prose rather than throwing away a
        # good read over formatting.
        about = text[:1500]

    return {
        "title": name,
        "about": about[:1500],
        "recent_titles": [t for t in titles if t][:12],
    }


# ---------------------------------------------------------------------------
# Parsing what the user pasted
# ---------------------------------------------------------------------------
def parse_channel_ref(url_or_handle: str) -> dict | None:
    """Work out what channel the user means, from anything they paste.

    Handles every shape YouTube uses:
        https://youtube.com/@handle          → {"handle": "handle"}
        https://youtube.com/channel/UC…      → {"channel_id": "UC…"}
        https://youtube.com/c/CustomName     → {"search": "CustomName"}
        https://youtube.com/user/LegacyName  → {"search": "LegacyName"}
        @handle  /  handle  /  UC…           → as above
    Returns None when there's nothing usable.
    """
    s = (url_or_handle or "").strip()
    if not s:
        return None

    # A bare channel id or @handle, with no URL around it.
    if re.fullmatch(r"UC[\w-]{20,}", s):
        return {"channel_id": s}
    if s.startswith("@") and "/" not in s:
        return {"handle": s[1:]}

    if "://" not in s:
        s = "https://" + s.lstrip("/")

    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", s, re.I)
    if m:
        return {"channel_id": m.group(1)}
    m = re.search(r"youtube\.com/@([\w.\-]+)", s, re.I)
    if m:
        return {"handle": m.group(1)}
    m = re.search(r"youtube\.com/(?:c|user)/([\w.\-]+)", s, re.I)
    if m:
        return {"search": m.group(1)}
    # A plain word the user typed as their channel name.
    if re.fullmatch(r"[\w .\-]{2,60}", url_or_handle.strip()):
        return {"search": url_or_handle.strip()}
    return None


def _get(path: str, **params) -> dict:
    params["key"] = api_key()
    try:
        r = requests.get(f"{API_ROOT}/{path}", params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ChannelLookupError(f"Couldn't reach YouTube ({e}).")
    if r.status_code == 403:
        raise ChannelLookupError(
            "YouTube refused the request (403). The API key may be restricted, "
            "or the daily quota is used up."
        )
    if r.status_code >= 400:
        raise ChannelLookupError(f"YouTube returned HTTP {r.status_code}.")
    return r.json()


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------
def _resolve_channel(ref: dict) -> dict | None:
    """Ref → the channel resource, or None if nothing matched."""
    if ref.get("channel_id"):
        data = _get("channels", part="snippet,statistics,contentDetails", id=ref["channel_id"])
    elif ref.get("handle"):
        data = _get(
            "channels", part="snippet,statistics,contentDetails", forHandle=ref["handle"]
        )
    else:
        # No direct lookup for legacy /c/ names — search, then read the hit.
        found = _get("search", part="snippet", q=ref.get("search", ""), type="channel", maxResults=1)
        hits = found.get("items") or []
        if not hits:
            return None
        cid = (hits[0].get("id") or {}).get("channelId")
        if not cid:
            return None
        data = _get("channels", part="snippet,statistics,contentDetails", id=cid)

    items = data.get("items") or []
    return items[0] if items else None


def _recent_uploads(uploads_playlist: str) -> list[dict]:
    """The most recent uploads, newest first."""
    if not uploads_playlist:
        return []
    data = _get(
        "playlistItems",
        part="snippet,contentDetails",
        playlistId=uploads_playlist,
        maxResults=RECENT_VIDEOS,
    )
    out = []
    for it in data.get("items") or []:
        snip = it.get("snippet") or {}
        out.append(
            {
                "title": (snip.get("title") or "").strip(),
                "published_at": (it.get("contentDetails") or {}).get("videoPublishedAt", ""),
                "video_id": (it.get("contentDetails") or {}).get("videoId", ""),
            }
        )
    return out


def _add_stats(videos: list[dict]) -> list[dict]:
    """Attach view counts so the planner can see what actually worked."""
    ids = [v["video_id"] for v in videos if v.get("video_id")]
    if not ids:
        return videos
    data = _get("videos", part="statistics,contentDetails", id=",".join(ids[:50]))
    by_id = {v.get("id"): v for v in data.get("items") or []}
    for v in videos:
        hit = by_id.get(v.get("video_id"))
        if not hit:
            continue
        stats = hit.get("statistics") or {}
        v["views"] = int(stats.get("viewCount") or 0)
        v["likes"] = int(stats.get("likeCount") or 0)
        v["duration"] = (hit.get("contentDetails") or {}).get("duration", "")
    return videos


def _cadence(videos: list[dict]) -> str:
    """How often they actually publish, read off the upload dates."""
    dates = []
    for v in videos:
        raw = v.get("published_at")
        if not raw:
            continue
        try:
            dates.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(dates) < 2:
        return ""
    dates.sort(reverse=True)
    span_days = (dates[0] - dates[-1]).days or 1
    per_week = len(dates) / (span_days / 7)
    if per_week >= 6:
        return "roughly daily"
    if per_week >= 1.5:
        return f"about {round(per_week)} per week"
    if per_week >= 0.8:
        return "about weekly"
    return "less than weekly"


def research_channel(url_or_handle: str) -> dict:
    """Everything we can honestly say about a channel.

    Always returns a dict — never raises for "no key" or "not found", because
    those are normal states the UI has to show. `available` says whether there
    is real data; `reason` says why not when there isn't.
    """
    ref = parse_channel_ref(url_or_handle)
    if not ref:
        return {
            "available": False,
            "reason": "That doesn't look like a YouTube channel link or handle.",
            "input": url_or_handle,
        }

    # Canonical URL to hand the page reader, whatever the user pasted.
    if ref.get("handle"):
        page_url = f"https://www.youtube.com/@{ref['handle']}"
    elif ref.get("channel_id"):
        page_url = f"https://www.youtube.com/channel/{ref['channel_id']}"
    else:
        page_url = url_or_handle if "://" in url_or_handle else f"https://www.youtube.com/{url_or_handle}"

    def _gemini_result(reason_if_none: str) -> dict:
        """Path 2 — read the page. Used when the Data API isn't available."""
        read = _read_with_gemini(page_url)
        if not read:
            return {
                "available": False,
                "reason": reason_if_none,
                "input": url_or_handle,
                "ref": ref,
            }
        logger.info("[youtube] read '%s' via Gemini URL context", read.get("title") or page_url)
        return {
            "available": True,
            # The agent is told what this source can and cannot support — see
            # as_context(). Exact figures are NOT claimed from a page read.
            "source": "gemini_url_context",
            "title": read.get("title", ""),
            "description": read.get("about", ""),
            "recent_videos": [{"title": t} for t in read.get("recent_titles", [])],
            "top_videos": [],
            "url": page_url,
            "input": url_or_handle,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    if not is_configured():
        return _gemini_result(
            "Couldn't open that channel page. Check the link, or set "
            "YOUTUBE_API_KEY for exact figures."
        )

    try:
        channel = _resolve_channel(ref)
    except ChannelLookupError as e:
        # The Data API failed (bad key, quota, network) — the page read is a
        # genuine second chance rather than giving up on the user's link.
        logger.warning("[youtube] Data API failed (%s); falling back to page read", e)
        return _gemini_result(str(e))

    if not channel:
        return _gemini_result("No channel matched that link.")

    snip = channel.get("snippet") or {}
    stats = channel.get("statistics") or {}
    uploads = (
        ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    )

    try:
        videos = _add_stats(_recent_uploads(uploads))
    except ChannelLookupError as e:
        logger.warning("[youtube] channel found but uploads failed: %s", e)
        videos = []

    top = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)[:5]
    return {
        "available": True,
        "source": "youtube_api",
        "input": url_or_handle,
        "channel_id": channel.get("id", ""),
        "title": (snip.get("title") or "").strip(),
        "description": (snip.get("description") or "").strip()[:1500],
        "country": snip.get("country", ""),
        "published_at": snip.get("publishedAt", ""),
        "subscribers": int(stats.get("subscriberCount") or 0),
        "total_views": int(stats.get("viewCount") or 0),
        "video_count": int(stats.get("videoCount") or 0),
        "cadence": _cadence(videos),
        "recent_videos": videos,
        "top_videos": top,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def as_context(research: dict) -> str:
    """Research → the plain-text block handed to the agent.

    When there is no data this says so EXPLICITLY and tells the agent to ask.
    That instruction is the whole reason this function exists: left to itself a
    model will happily invent a subscriber count.
    """
    if not research:
        return ""
    if not research.get("available"):
        return (
            "NO CHANNEL DATA IS AVAILABLE. "
            f"({research.get('reason', 'not fetched')}) "
            "You must NOT state or estimate any subscriber counts, view counts "
            "or past video titles for this creator. Ask them about their "
            "channel instead, and build the plan from what they tell you."
        )

    # Read from the channel PAGE. Topics and titles are real; numbers are not
    # available from this source, so the agent is told not to produce any.
    if research.get("source") == "gemini_url_context":
        lines = [f"Channel: {research.get('title', '')}"]
        if research.get("description"):
            lines.append(f"What this channel posts: {research['description']}")
        titles = [v.get("title", "") for v in (research.get("recent_videos") or [])]
        titles = [t for t in titles if t]
        if titles:
            lines.append("\nRecent video titles on the channel:")
            lines.extend(f"  - {t}" for t in titles)
        lines.append(
            "\nThis was read directly from the channel page, so the name, topics "
            "and titles above are real — use them. Subscriber and view counts "
            "were NOT available: do not state, estimate or imply any. If the "
            "size of the channel matters to your advice, ask the creator."
        )
        return "\n".join(lines)

    lines = [
        f"Channel: {research.get('title', '')}",
        f"Subscribers: {research.get('subscribers', 0):,}",
        f"Total views: {research.get('total_views', 0):,}",
        f"Videos published: {research.get('video_count', 0):,}",
    ]
    if research.get("cadence"):
        lines.append(f"Current publishing rhythm: {research['cadence']}")
    if research.get("country"):
        lines.append(f"Country: {research['country']}")
    if research.get("description"):
        lines.append(f"Channel description: {research['description'][:600]}")

    recent = research.get("recent_videos") or []
    if recent:
        lines.append("\nRecent uploads (newest first):")
        for v in recent[:10]:
            views = f" — {v['views']:,} views" if v.get("views") else ""
            lines.append(f"  - {v.get('title', '')}{views}")

    top = research.get("top_videos") or []
    if top:
        lines.append("\nBest performing of those:")
        for v in top:
            lines.append(f"  - {v.get('title', '')} — {v.get('views', 0):,} views")

    lines.append(
        "\nThese figures are real, fetched from the YouTube Data API. Use them. "
        "Do not state any other numbers about this channel."
    )
    return "\n".join(lines)

"""
freesound.py — the SOUND LIBRARY: search Freesound, bring one sound back.

A provider client, like `meshy.py` and `tripo.py`: it talks to somebody else's
HTTP API and knows nothing about FastAPI, jobs or the timeline. The router that
exposes it is `server/sounds.py`; the route that files a sound into a project is
in `server/animatics.py`, because that is where an audio upload already lives.

⚠ TWO DIFFERENT LICENCES ARE IN PLAY HERE AND THEY ARE NOT THE SAME DOCUMENT.
Getting this wrong is the expensive kind of wrong, so both are written down:

  1. THE API LICENCE — https://freesound.org/help/tos_api/ — is what lets OUR
     SERVER talk to THEIRS. §3, verbatim: "Terms for commercial use of the API
     will be negotiated on a case by case basis with UPF." So the free key from
     https://freesound.org/apiv2/apply/ is a NON-COMMERCIAL key. A paid product
     needs an agreement with UPF (mtg@upf.edu) BEFORE it ships. Nothing in this
     file can check that for you — it is a contract, not a flag — which is
     exactly why it is stated here rather than left in somebody's memory.
     The same terms cap us at 60 requests/minute and 2000/day (§3), forbid
     copying the database (§1b: "limited intermediate copies … deleted when they
     are no longer required"), and require the key to stay secret (§B.4) — which
     is why it is read from the environment on the SERVER and never sent to the
     browser.

  2. THE CONTENT LICENCE — per SOUND, and it travels with the file into the
     customer's finished video. Freesound has exactly three, and one of them,
     "Attribution NonCommercial", can NEVER go into a commercial export. That is
     what `LICENCES` is: a whitelist of the two that can, with the NC one absent
     rather than filtered, so there is no code path that could stop excluding
     it. `_filter` builds the Solr filter from that whitelist and the search
     route sends nothing else.

⚠ WE FETCH THE PREVIEW, NOT THE ORIGINAL. The original file is behind
`/apiv2/sounds/<id>/download/`, which needs OAuth2 — i.e. every user of this app
would have to own a Freesound account and sign in to it. The HQ mp3 preview
(~128 kbps) needs only our token, is the same recording, and carries the same
Content Licence, so it is what `fetch_preview` brings back. Swapping to the
original later is an auth change, not a change here.
"""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_BASE = "https://freesound.org/apiv2"
# ⚠ `/apiv2/search/`, not `/apiv2/search/text/` — the old path was deprecated in
# November 2025 and the docs now document this one.
SEARCH_PATH = "/search/"
TIMEOUT_SECONDS = 20


class FreesoundError(RuntimeError):
    """Anything that stops us answering — no key, a refusal, a timeout."""


# --- What we are allowed to offer -------------------------------------------
# code -> (the name Freesound's own `license` field uses,
#          what we print,
#          the deed to link,
#          does using it oblige the customer to credit somebody?)
#
# ⚠ "Attribution NonCommercial" IS DELIBERATELY ABSENT AND MUST STAY ABSENT. It
# is not filtered out further down; it simply has no code, so no request can ask
# for it and no result can be labelled with it. A sound under it in a customer's
# advert is a licence breach by our customer, caused by us.
LICENCES = {
    "cc0": (
        "Creative Commons 0",
        "CC0 - public domain",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        False,
    ),
    "by": (
        "Attribution",
        "CC BY - credit required",
        "https://creativecommons.org/licenses/by/4.0/",
        True,
    ),
}

# What the search box offers. "safe" is CC0 only and is the DEFAULT, because it
# is the one answer that needs no paperwork from the person exporting the video.
LICENCE_CHOICES = {
    "safe": ["cc0"],
    "credit": ["by"],
    "both": ["cc0", "by"],
}
DEFAULT_LICENCE = "safe"

SORTS = {
    "relevance": "score",
    "downloads": "downloads_desc",
    "rating": "rating_desc",
    "newest": "created_desc",
    "shortest": "duration_asc",
    "longest": "duration_desc",
}

# Only what a card actually draws. `fields` is the documented way to keep a
# response small, and a search that omits it pulls a few hundred audio
# descriptors per sound that nothing on screen reads.
FIELDS = "id,name,username,license,duration,previews,images,url,tags,filesize"


def api_key() -> str:
    """The token, or "" when nobody has set one.

    ⚠ NEVER RETURNED TO THE BROWSER. §B.4 of the API terms: "Freesound API User
    IDs … must be kept secret and confidential and under no circumstances be
    exposed to the public." The client asks `GET /sounds/status` whether one
    EXISTS; it never learns what it is.
    """
    return (os.environ.get("FREESOUND_API_KEY") or "").strip()


def configured() -> bool:
    """Is the sound library switched on? Drives whether the tab is shown."""
    return bool(api_key())


def _licence_codes(choice: str) -> list[str]:
    return LICENCE_CHOICES.get((choice or "").lower(), LICENCE_CHOICES[DEFAULT_LICENCE])


def _filter(choice: str, min_seconds: float, max_seconds: float) -> str:
    """The Solr `filter` string — the licence fence, plus a length window.

    ⚠ THE LICENCE CLAUSE IS ALWAYS PRESENT. Even "both" names its two licences
    explicitly rather than being left off, so a bug that loses the length window
    cannot also quietly widen the search to NonCommercial sounds.
    """
    names = " OR ".join('"%s"' % LICENCES[c][0] for c in _licence_codes(choice))
    parts = ["license:(%s)" % names]
    lo = max(0.0, float(min_seconds or 0))
    hi = float(max_seconds or 0)
    if hi > 0 and hi >= lo:
        parts.append("duration:[%g TO %g]" % (lo, hi))
    elif lo > 0:
        parts.append("duration:[%g TO *]" % lo)
    return " ".join(parts)


def _code_for(license_value: str) -> str:
    """Freesound's `license` field -> our code, or "" for one we do not offer.

    ⚠ THE FIELD COMES IN TWO SHAPES AND THE DOCS ONLY DESCRIBE ONE. The API
    reference says it is prose ("Creative Commons 0"); the LIVE API answers with
    a deed URL ("http://creativecommons.org/publicdomain/zero/1.0/"). Reading
    only the documented shape made every single result unrecognised, so every
    single result was dropped and the Sounds tab came up empty against a working
    key. (That is the fence failing SAFE, which is the design working — but an
    empty library is still a broken library.) Both shapes are read here.

    ⚠ THE URL IS PARSED BY PATH SEGMENT, NEVER BY `in`. NonCommercial's deed is
    ".../licenses/by-nc/4.0/", and `"by" in url` is true of it. A substring test
    here is how a NonCommercial sound ends up in a customer's advert — so the
    slug is compared whole, and anything that is not exactly "by" falls through
    to "" and is dropped.

    ⚠ AND THE VERSION IS DELIBERATELY IGNORED FOR THE CODE. CC BY 3.0 and 4.0
    are the same obligation (name the author); the version matters to the CREDIT,
    which is why `_licence_info` keeps Freesound's own URL rather than ours.
    """
    raw = (license_value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        # Strip the scheme and host, then read the path as segments.
        path = raw.split("//", 1)[-1]
        parts = [seg for seg in path.split("/")[1:] if seg]
        if len(parts) >= 2 and parts[0] == "publicdomain" and parts[1] == "zero":
            return "cc0"
        if len(parts) >= 2 and parts[0] == "licenses" and parts[1] == "by":
            return "by"
        return ""
    for code, (name, _label, _url, _credit) in LICENCES.items():
        if raw == name.lower():
            return code
    return ""


# The licence's NAME, as it belongs in a credit. ⚠ SEPARATE FROM THE BADGE ON
# THE CARD, and the split is not cosmetic: the badge says the OBLIGATION
# ("credit required") because that is what a person choosing a sound needs to
# see, and a credit line that repeated it would read
#     "Piano chord 3" by mistakeless - CC BY 4.0 - credit required - https://…
# in the description of somebody's published video. What CC BY asks for is the
# title, the author, the licence and a link — not an instruction to the reader.
SHORT_NAMES = {"cc0": "CC0", "by": "CC BY"}


def _version_of(license_value: str) -> str:
    """"4.0" out of a deed URL, or "" when there is no version to read."""
    raw = (license_value or "").strip().lower()
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return ""
    for seg in reversed([s for s in raw.split("/") if s]):
        head, _dot, tail = seg.partition(".")
        if head.isdigit() and tail.isdigit():
            return seg
    return ""


def _licence_info(license_value: str) -> dict | None:
    """Everything a card needs to say about its licence, or `None` to drop it.

    ⚠ IT PREFERS FREESOUND'S OWN URL TO OUR CANONICAL ONE. A sound published
    under CC BY **3.0** must not be credited with a link to the 4.0 deed — the
    credit is supposed to point at the terms that actually apply to that file.
    Our deed in `LICENCES` is the fallback for the prose form, which carries no
    version at all.
    """
    code = _code_for(license_value)
    if not code:
        return None
    _prose, _label, deed, needs_credit = LICENCES[code]
    raw = (license_value or "").strip()
    is_url = raw.lower().startswith("http://") or raw.lower().startswith("https://")
    # ⚠ THE VERSION IS PART OF THE NAME WHEN WE KNOW IT. "CC BY" and "CC BY 4.0"
    # are not the same statement, and the prose form of the field carries no
    # version at all — so it is appended when present and simply left off when
    # it is not, rather than guessed at.
    version = _version_of(raw)
    name = SHORT_NAMES[code] + (" " + version if version else "")
    return {
        "code": code,
        # For a credit line: the licence, and nothing about what to do about it.
        "name": name,
        # For the badge on the card: what the user is taking on by choosing this.
        "label": name + (" - credit required" if needs_credit else " - public domain"),
        "url": raw if is_url else deed,
        "needs_credit": needs_credit,
    }


def _get(path: str, params: dict) -> dict:
    """One authenticated GET. Every Freesound API call in the app comes here."""
    key = api_key()
    if not key:
        raise FreesoundError(
            "The sound library is switched off - FREESOUND_API_KEY is not set. "
            "Get a key at https://freesound.org/apiv2/apply/ and put it in .env."
        )
    try:
        res = requests.get(
            API_BASE + path,
            params=params,
            # The documented header form. ⚠ Not `?token=` in the query string: a
            # token in a URL is a token in every proxy and access log it passes
            # through, and §B.4 asks us to keep it confidential.
            headers={"Authorization": "Token " + key},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise FreesoundError("Could not reach Freesound: %s" % exc) from exc

    if res.status_code in (401, 403):
        raise FreesoundError(
            "Freesound refused our key. Check FREESOUND_API_KEY, and that the key "
            "has not been revoked at https://freesound.org/apiv2/apply/."
        )
    if res.status_code == 429:
        # §3 of the terms: 60/minute and 2000/day on a free key. Said plainly,
        # because the fix is to wait rather than to press the button again.
        raise FreesoundError(
            "Freesound's rate limit is reached (60 searches a minute, 2000 a day "
            "on a free key). Try again in a minute."
        )
    if res.status_code >= 400:
        raise FreesoundError(
            "Freesound answered %d: %s" % (res.status_code, res.text[:200])
        )
    try:
        return res.json()
    except ValueError as exc:
        raise FreesoundError("Freesound sent something that is not JSON.") from exc


def credit_line(item: dict) -> str:
    """THE ONE LINE THE CUSTOMER PUTS IN THEIR CREDITS, ready to print.

    ⚠ BUILT ONCE, HERE, AND STORED ON THE LIBRARY CARD — not rebuilt wherever a
    credits list is drawn. CC BY asks for the title, the author, the licence and
    a link to the material; a second implementation of that sentence somewhere
    else is a second chance to leave one of the four out.

    A CC0 sound legally needs no credit at all, so it gets a shorter line that
    says where it came from and nothing more — printing "you must credit" over a
    public-domain file would be telling the customer to do work they do not owe.
    """
    title = (item.get("name") or "sound").strip()
    who = (item.get("username") or "unknown").strip()
    page = (item.get("page_url") or "").strip()
    # ⚠ `license_name`, NEVER `license_label`. The label ends in "credit
    # required", which is an instruction to the person CHOOSING the sound, and
    # pasting it into a video's description publishes that instruction to the
    # audience. See `SHORT_NAMES`.
    licence = (item.get("license_name") or "").strip()
    if item.get("needs_credit"):
        return '"%s" by %s (%s) - %s' % (title, who, licence, page)
    return '"%s" by %s (%s, no credit required) - %s' % (title, who, licence, page)


def _normalise(raw: dict) -> dict | None:
    """One Freesound result -> the card the editor draws. `None` = do not show.

    ⚠ A RESULT WHOSE LICENCE WE DO NOT RECOGNISE IS DROPPED, not shown with a
    blank licence. The filter should already have excluded it, so reaching here
    means either Freesound added a fourth licence or our filter stopped working
    — and in both cases showing the sound anyway is the failure this whole file
    exists to avoid. Losing a row is cheap; shipping a NonCommercial sound is
    not.
    """
    info = _licence_info(raw.get("license", ""))
    if not info:
        logger.warning(
            "freesound: dropping sound %s, licence %r", raw.get("id"), raw.get("license")
        )
        return None
    code, label, deed, needs_credit = info["code"], info["label"], info["url"], info["needs_credit"]
    licence_name = info["name"]
    previews = raw.get("previews") or {}
    images = raw.get("images") or {}
    sound_id = str(raw.get("id") or "")
    item = {
        "id": sound_id,
        "name": raw.get("name") or "Untitled",
        "username": raw.get("username") or "",
        "duration_ms": int(round(float(raw.get("duration") or 0) * 1000)),
        "license": code,
        # Two strings, two jobs — see `SHORT_NAMES`. `license_name` goes in the
        # credit, `license_label` goes on the badge.
        "license_name": licence_name,
        "license_label": label,
        "license_url": deed,
        "needs_credit": needs_credit,
        # Where the sound lives. This is the link a CC BY credit must carry, and
        # it is also the "more info" a user clicks before committing to a file.
        "page_url": raw.get("url") or ("https://freesound.org/s/%s/" % sound_id),
        # Straight off Freesound's CDN, played by the browser's own <audio>. ⚠ We
        # do NOT proxy these: a proxy would have to re-ask the API for the URL on
        # every play, and 60 requests a minute is a budget a list of previews can
        # burn through in seconds. IMPORTING does go through us — `fetch_preview`
        # — because that is once per sound and has to be checked.
        "preview_url": previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or "",
        "waveform_url": images.get("waveform_m") or "",
        "tags": list(raw.get("tags") or [])[:8],
    }
    item["attribution"] = credit_line(item)
    return item


def search(
    query: str = "",
    licence: str = DEFAULT_LICENCE,
    min_seconds: float = 0,
    max_seconds: float = 0,
    page: int = 1,
    page_size: int = 24,
    sort: str = "relevance",
) -> dict:
    """Text search, fenced to the licences a commercial export may use.

    Returns `{items, page, page_size, total, has_next, licence}`. `total` is what
    Freesound reports BEFORE `_normalise` drops anything, so it is the size of
    the SEARCH rather than of this page's list — which is what a "page 1 of 40"
    line wants and what paging has to be driven by.
    """
    page = max(1, int(page or 1))
    # 150 is the API's documented ceiling; ours is lower because a pane this
    # narrow cannot show more than a screenful before the user pages anyway.
    page_size = max(1, min(60, int(page_size or 24)))
    data = _get(
        SEARCH_PATH,
        {
            "query": (query or "").strip(),
            "filter": _filter(licence, min_seconds, max_seconds),
            "sort": SORTS.get((sort or "").lower(), SORTS["relevance"]),
            "page": page,
            "page_size": page_size,
            "fields": FIELDS,
            # One row per pack rather than forty takes of the same rain. A sound
            # library is BROWSED, and forty near-identical cards is a browse that
            # has failed even though the search worked.
            "group_by_pack": 1,
        },
    )
    items = [c for c in (_normalise(r) for r in (data.get("results") or [])) if c]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(data.get("count") or 0),
        "has_next": bool(data.get("next")),
        "licence": (licence or DEFAULT_LICENCE).lower(),
    }


def sound(sound_id: str) -> dict:
    """One sound by id, normalised the same way a search result is.

    ⚠ THE IMPORT ROUTE CALLS THIS RATHER THAN TRUSTING THE BROWSER. The card on
    screen already holds a preview URL, and taking it from there would save a
    request — and would also mean our server fetching whatever URL a crafted
    request handed it. The id is the only thing we accept, and the URL we then
    download is the one Freesound has just told us.
    """
    sid = str(sound_id or "").strip()
    if not sid.isdigit():
        raise FreesoundError("That is not a Freesound sound id.")
    item = _normalise(_get("/sounds/%s/" % sid, {"fields": FIELDS}))
    if not item:
        # Reached only for the NonCommercial licence, since search never offers
        # one — i.e. somebody asked by id for a sound they may not use.
        raise FreesoundError(
            "That sound is Attribution-NonCommercial, so it cannot be used in a "
            "commercial export. Pick a CC0 or CC BY sound instead."
        )
    return item


def fetch_preview(sound_id: str, max_bytes: int) -> tuple[bytes, str, dict]:
    """Bring one sound back as mp3 bytes, with the card that describes it.

    Returns `(data, filename, item)`. The caller writes the bytes wherever its
    own uploads live — this function owns no storage, exactly as `meshy.py` owns
    none. `max_bytes` is the caller's cap (`config.MAX_AUDIO_BYTES`), enforced
    WHILE streaming so an unexpectedly huge file is refused rather than read
    into memory first and measured afterwards.

    ⚠ IT COSTS TWO STEPS: an API call to re-ask where the file is, then a plain
    CDN download. Use `download()` instead when you are holding an item this
    module itself just produced — see the note on `download`.
    """
    item = sound(sound_id)
    data, filename = download(item, max_bytes)
    return data, filename, item


def download(item: dict, max_bytes: int) -> tuple[bytes, str]:
    """The mp3 for an item THIS MODULE ALREADY NORMALISED. Returns `(data, name)`.

    ⚠ THE DIFFERENCE FROM `fetch_preview` IS ONE API REQUEST, AND THAT REQUEST IS
    SCARCE. A free Freesound key allows 60 a MINUTE for the whole deployment, so a
    pass that files eleven sounds costs eleven searches plus eleven metadata reads
    — twenty-two — where it could cost eleven. This takes the item a `search()`
    already returned and skips straight to the CDN.
    """
    url = (item or {}).get("preview_url") or ""
    if not url:
        raise FreesoundError("Freesound has no mp3 preview for that sound.")
    try:
        res = requests.get(url, timeout=TIMEOUT_SECONDS, stream=True)
        res.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for chunk in res.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise FreesoundError(
                    "That sound is larger than the %d MB limit." % (max_bytes // 1_048_576)
                )
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise FreesoundError("Could not download that sound: %s" % exc) from exc

    # A name a human recognises in the Media pane, with the id kept so that two
    # sounds both called "rain" are still two cards.
    stem = _safe_name(item.get("name")) or "sound"
    return b"".join(chunks), "%s-freesound-%s.mp3" % (stem, item.get("id"))


def _safe_name(name: str) -> str:
    """A sound's title, reduced to something safe to put in a filename."""
    out = "".join(c if (c.isalnum() or c in " -_") else " " for c in (name or ""))
    return " ".join(out.split())[:60].strip()


__all__ = [
    "FreesoundError",
    "LICENCES",
    "LICENCE_CHOICES",
    "DEFAULT_LICENCE",
    "SORTS",
    "api_key",
    "configured",
    "credit_line",
    "download",
    "fetch_preview",
    "search",
    "sound",
]

"""
sounds.py — BROWSING the sound library (/sounds/…). Searching, not importing.

Two routes and nothing else:

    GET /sounds/status   is the library switched on, and what may it offer?
    GET /sounds/search   text search, fenced to commercially-usable licences

⚠ IMPORTING A SOUND IS NOT HERE. Filing one into a project writes into that
project's media directory, which is `server/animatics.py`'s business and already
has the ownership check, the size cap and the `audio_<id>` naming an upload uses.
So the import route lives there (`POST /animatics/{id}/sounds`) and calls the
same `freesound.py` this module calls. Two routers, one provider client, and
neither router imports the other — the rule the whole `server/` package follows.

⚠ THE KEY NEVER LEAVES THE SERVER. `GET /sounds/status` answers `configured:
true/false`; it does not answer with the token. §B.4 of the Freesound API terms
requires that, and it is also why the browser talks to us instead of talking to
Freesound directly.

⚠ AND THESE ROUTES SPEND NO AI QUOTA — they are a search box over somebody
else's catalogue — but they DO spend the Freesound rate limit, which on a free
key is 60 requests a minute and 2000 a day for the WHOLE deployment, not per
user. That is why the editor debounces its search box rather than searching per
keystroke: the budget is shared, and one impatient user can exhaust everybody's.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

import freesound

from .auth import CurrentUser, get_current_user
from .schemas import SoundSearchResponse, SoundStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sounds", tags=["sounds"])

# Said once, under the search box, and stored nowhere else. It is the short
# version of the long note at the top of `freesound.py`.
LICENCE_NOTICE = (
    "Sounds come from Freesound. Only CC0 and CC BY sounds are listed here — "
    "NonCommercial sounds are never shown. A CC BY sound must be credited in "
    "your finished video; the credit line is saved on the media card."
)


@router.get("/status", response_model=SoundStatus)
def sound_status(current: CurrentUser = Depends(get_current_user)) -> SoundStatus:
    """Whether the Sounds tab should exist at all, and what it may offer.

    ⚠ SIGNED IN, THOUGH IT REVEALS ALMOST NOTHING. The tab is part of the editor
    and the editor is behind a login, so leaving this open would only make the
    deployment's configuration readable by anyone who asked.
    """
    return SoundStatus(
        configured=freesound.configured(),
        # ⚠ ORDERED SAFEST FIRST, and the client takes the first as its default.
        # "safe" is CC0 only: the one answer that puts no obligation on whoever
        # exports the video.
        licences=["safe", "credit", "both"],
        sorts=list(freesound.SORTS),
        notice=LICENCE_NOTICE,
    )


@router.get("/search", response_model=SoundSearchResponse)
def search_sounds(
    q: str = Query("", description="What to search for. Empty browses by `sort`."),
    licence: str = Query(
        freesound.DEFAULT_LICENCE,
        description="'safe' = CC0 only (default) · 'credit' = CC BY · 'both'.",
    ),
    min_seconds: float = Query(0, ge=0, le=3600),
    max_seconds: float = Query(0, ge=0, le=3600),
    page: int = Query(1, ge=1, le=200),
    page_size: int = Query(24, ge=1, le=60),
    sort: str = Query("relevance"),
    current: CurrentUser = Depends(get_current_user),
) -> SoundSearchResponse:
    """Search Freesound for sounds this app's users are allowed to publish.

    ⚠ THE LICENCE FENCE IS APPLIED ON THE SERVER, in `freesound._filter`, and an
    unknown `licence` value falls back to the safe bucket rather than to "all".
    A fence a query parameter can open is not a fence.
    """
    try:
        data = freesound.search(
            query=q,
            licence=licence,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            page=page,
            page_size=page_size,
            sort=sort,
        )
    except freesound.FreesoundError as exc:
        # 502, not 500: the failure is somebody else's service or our key for it,
        # and the message is already written for a human to act on.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SoundSearchResponse(**data)

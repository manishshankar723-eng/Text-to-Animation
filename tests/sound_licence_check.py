"""THE LICENCE FENCE — the one thing in the sound library that has a right answer.

Everything else about the Sounds tab fails loudly: a broken search shows no
results, a broken preview does not play, a broken import throws. The licence
fence is the opposite. If it stops working, the tab keeps looking perfect and
starts listing Attribution-NonCommercial sounds, a customer drops one into an
advert they sell, and nothing anywhere reports a problem — until somebody's
lawyer does. No UI can notice this, so a test has to.

⚠ THE THREAT IS NOT "SOMEBODY DELETES THE FILTER". It is the three quieter ways
the fence rots, and there is a section for each:

1. **The whitelist grows a third entry.** `freesound.LICENCES` is the ONLY list
   of licences this app will show. NonCommercial is absent from it rather than
   filtered out of it, which is what makes the fence structural — so the first
   check is simply that it is still absent, under any spelling.

2. **The query stops naming the licences.** `_filter` builds the Solr string
   that Freesound actually applies. A refactor that makes the licence clause
   conditional — on a "both" bucket, on an empty query, on a duration window —
   is a fence with a hole in it, and the string is the only place that shows.
   Every bucket, including an unknown one, must produce a licence clause.

3. **An unknown bucket falls open instead of shut.** `licence=whatever` from a
   hand-made request must land on the SAFE bucket, never on "everything".

4. **The field changes shape.** ⚠ **THE LIVE API DOES NOT SEND WHAT THE DOCS
   DESCRIBE.** The API reference says `license` is prose ("Creative Commons 0");
   every real response carries a deed URL
   ("http://creativecommons.org/publicdomain/zero/1.0/"). The first build read
   only the documented shape, so against a perfectly good key EVERY result was
   unrecognised and therefore dropped, and the tab came up empty. The fence had
   failed safe — which is the design working — but an empty library is still a
   broken library, and the test that "passed" had only ever fed it prose. **Both
   shapes are exercised below, and the URL forms are the ones that matter.**

   ⚠ **AND THE URL IS PARSED BY SEGMENT, NEVER BY `in`.** NonCommercial's deed
   is ".../licenses/by-nc/4.0/", of which `"by" in url` is true. A substring test
   is how an NC sound reaches a customer's advert, so `by-nc`, `by-nc-sa` and
   `by-sa` are all fed in below and all must drop.

Then the reverse direction: `_normalise` is what turns Freesound's answer into a
card, and it must DROP a sound whose licence we do not offer rather than draw it
with a blank badge — because reaching that line means the fence upstream has
already failed, and drawing the sound anyway is the exact outcome all of the
above exists to prevent.

Finally the two things that ride along with the licence into the customer's
project: `credit_line` (a CC BY sound names its author; a CC0 one does not claim
to need it) and `AnimaticAsset.attribution` (the credit is SAVED, so it outlives
the search that found it).

    python tests/sound_licence_check.py

Needs no key and makes no network call — everything here is pure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import freesound
from server.schemas import AnimaticAsset, SoundSearchItem

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"   {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(f"{label} {detail}".strip())


# The three licences Freesound documents, verbatim. The third must never be
# reachable through this app.
CC0 = "Creative Commons 0"
BY = "Attribution"
NC = "Attribution NonCommercial"


print("\nNonCommercial is ABSENT from the whitelist, not filtered out of it")
names = {name.lower() for name, _l, _u, _c in freesound.LICENCES.values()}
check("the whitelist holds exactly two licences", len(freesound.LICENCES) == 2, str(sorted(names)))
check("…CC0 is one of them", CC0.lower() in names)
check("…Attribution is the other", BY.lower() in names)
check(
    "…and NonCommercial is not in it under any spelling",
    not any("noncommercial" in n or "non-commercial" in n for n in names),
    str(sorted(names)),
)
check(
    "no licence bucket names a licence the whitelist does not hold",
    all(c in freesound.LICENCES for codes in freesound.LICENCE_CHOICES.values() for c in codes),
    str(freesound.LICENCE_CHOICES),
)
# ⚠ CC0 IS THE DEFAULT ON PURPOSE. It is the only bucket that puts no obligation
# on whoever exports the video, so it is what a user who never touches the
# picker gets.
check(
    "the default bucket is CC0 only",
    freesound.LICENCE_CHOICES[freesound.DEFAULT_LICENCE] == ["cc0"],
    str(freesound.LICENCE_CHOICES.get(freesound.DEFAULT_LICENCE)),
)


print("\nEvery query Freesound receives names the licences it may return")
BUCKETS = ["safe", "credit", "both", "", "all", "nonsense", None]
for bucket in BUCKETS:
    got = freesound._filter(bucket, 0, 0)
    check(
        f"licence={bucket!r} still carries a licence clause",
        got.startswith("license:("),
        got,
    )
    check(
        f"…and never asks for NonCommercial",
        "NonCommercial" not in got,
        got,
    )

# ⚠ THE DURATION WINDOW MUST NOT DISPLACE THE LICENCE CLAUSE. They are two
# clauses of one filter, and the bug worth pinning is a refactor that returns
# the length window INSTEAD of the fence when a length is given.
windowed = freesound._filter("both", 5, 120)
check("a length window is ADDED to the fence, not swapped for it", windowed.startswith("license:("), windowed)
check("…and the window itself is there", "duration:[5 TO 120]" in windowed, windowed)
open_ended = freesound._filter("safe", 30, 0)
check("a minimum with no maximum is open-ended", "duration:[30 TO *]" in open_ended, open_ended)
check("…with the fence still first", open_ended.startswith("license:("), open_ended)


print("\nAn unknown bucket falls SHUT, not open")
unknown = freesound._filter("everything-please", 0, 0)
safe = freesound._filter("safe", 0, 0)
check("an unrecognised licence value is treated as the safe bucket", unknown == safe, unknown)
check(
    "…so it asks for CC0 and nothing else",
    BY not in unknown and NC not in unknown,
    unknown,
)


print("\nThe licence field is read in BOTH shapes — prose AND deed URL")
# ⚠ EVERY URL BELOW IS ONE THE LIVE API HAS ACTUALLY SENT. They were collected
# from a real search on 2026-08-25 — the day the prose-only reader was found to
# be dropping 100% of results against a perfectly good key. Note both schemes:
# Freesound serves `http://` on older sounds and `https://` on newer ones.
LIVE_CC0 = [
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "https://creativecommons.org/publicdomain/zero/1.0/",
]
LIVE_BY = [
    "https://creativecommons.org/licenses/by/4.0/",
    "http://creativecommons.org/licenses/by/3.0/",
]
for url in LIVE_CC0:
    check(f"{url} reads as CC0", freesound._code_for(url) == "cc0", freesound._code_for(url))
for url in LIVE_BY:
    check(f"{url} reads as CC BY", freesound._code_for(url) == "by", freesound._code_for(url))
check("the documented prose form still works too", freesound._code_for(CC0) == "cc0")
check("…for Attribution as well", freesound._code_for(BY) == "by")

# ⚠ THE SUBSTRING TRAP. Every one of these CONTAINS "by".
print("\n…and a URL that merely CONTAINS 'by' is not CC BY")
for url in [
    "http://creativecommons.org/licenses/by-nc/3.0/",
    "https://creativecommons.org/licenses/by-nc/4.0/",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "https://creativecommons.org/licenses/by-sa/4.0/",
    "https://creativecommons.org/licenses/sampling+/1.0/",
]:
    got = freesound._code_for(url)
    check(f"{url.split('/licenses/')[-1]} is refused", got == "", f"got {got!r}")
check("the NC prose form is refused too", freesound._code_for(NC) == "")
check("a URL that is not a licence at all is refused", freesound._code_for("https://example.com/") == "")

# ⚠ THE CREDIT MUST NAME THE VERSION THAT ACTUALLY APPLIES. Crediting a CC BY
# 3.0 sound with a link to the 4.0 deed points the reader at terms that are not
# the ones on the file, so Freesound's own URL is kept rather than our canonical
# one — which is only the fallback for the versionless prose form.
print("\nThe credit points at the version that really applies")
by3 = freesound._licence_info("http://creativecommons.org/licenses/by/3.0/")
by4 = freesound._licence_info("https://creativecommons.org/licenses/by/4.0/")
check("a 3.0 sound keeps the 3.0 deed", by3["url"].endswith("/by/3.0/"), by3["url"])
check("…and says 3.0 on the badge", "3.0" in by3["label"], by3["label"])
check("a 4.0 sound keeps the 4.0 deed", by4["url"].endswith("/by/4.0/"), by4["url"])
check("…and says 4.0 on the badge", "4.0" in by4["label"], by4["label"])
prose = freesound._licence_info(BY)
check("the versionless prose form falls back to our deed", prose["url"] == freesound.LICENCES["by"][2], prose["url"])
check("a CC0 deed is kept exactly as sent", freesound._licence_info(LIVE_CC0[0])["url"] == LIVE_CC0[0])


print("A sound we cannot place is DROPPED, not drawn with a blank badge")


def raw(license_name: str, sound_id: str = "1234") -> dict:
    """The shape Freesound's search returns, cut down to what `_normalise` reads."""
    return {
        "id": sound_id,
        "name": "Test sound",
        "username": "someone",
        "license": license_name,
        "duration": 3.5,
        "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/1/1234-hq.mp3"},
        "images": {"waveform_m": "https://cdn.freesound.org/displays/1/1234_wave_M.png"},
        "url": f"https://freesound.org/s/{sound_id}/",
        "tags": ["test"],
    }


check("a NonCommercial sound normalises to None", freesound._normalise(raw(NC)) is None)
check(
    "…and so does its deed URL, which is the shape that really arrives",
    freesound._normalise(raw("https://creativecommons.org/licenses/by-nc/4.0/")) is None,
)
check("a licence Freesound has not invented yet also drops", freesound._normalise(raw("Sampling+")) is None)
check("an empty licence drops", freesound._normalise(raw("")) is None)

# ⚠ BUILT FROM THE URL FORM, NOT THE PROSE FORM. These two cards drive every
# check below them, and feeding them the documented-but-never-sent shape is
# exactly how the original bug passed its own test.
cc0_card = freesound._normalise(raw("http://creativecommons.org/publicdomain/zero/1.0/"))
by_card = freesound._normalise(raw("https://creativecommons.org/licenses/by/4.0/", "9999"))
check("a CC0 sound is kept", cc0_card is not None)
check("an Attribution sound is kept", by_card is not None)
check("…CC0 is coded 'cc0'", cc0_card["license"] == "cc0", str(cc0_card and cc0_card["license"]))
check("…Attribution is coded 'by'", by_card["license"] == "by", str(by_card and by_card["license"]))

# ⚠ THIS FLAG IS WHAT THE BADGE ON THE CARD READS, and it is the difference
# between a user who knows they owe a credit and one who finds out later.
check("CC0 does not ask for a credit", cc0_card["needs_credit"] is False)
check("Attribution does", by_card["needs_credit"] is True)

# The duration Freesound reports, in the units the rest of the app uses. A card
# that opened at 3.5ms instead of 3500ms would be a clip you cannot see.
check("seconds become milliseconds", cc0_card["duration_ms"] == 3500, str(cc0_card["duration_ms"]))


print("\nThe credit says what is actually owed")
check("a CC BY credit names the author", "someone" in by_card["attribution"], by_card["attribution"])
check("…and the sound", "Test sound" in by_card["attribution"], by_card["attribution"])
check("…and links the material, as the deed requires", "freesound.org/s/9999" in by_card["attribution"], by_card["attribution"])
# ⚠ A CC0 SOUND OWES NOBODY ANYTHING. Printing "you must credit" over a
# public-domain file is telling the customer to do work they do not owe, so the
# line says where it came from and says so.
check("a CC0 line says no credit is required", "no credit required" in cc0_card["attribution"], cc0_card["attribution"])

# ⚠ THE BADGE'S WORDING MUST NOT REACH THE CREDIT. `license_label` ends in
# "credit required" because that is what somebody CHOOSING a sound needs to see;
# `license_name` is the licence alone. The first build had `credit_line` reading
# the label, which published
#     "Piano chord 3" by mistakeless - CC BY 4.0 - credit required - https://…
# into the description of a finished video — an instruction to the audience,
# addressed to the wrong person. These two checks are the whole of that split.
check(
    "the badge says the obligation",
    by_card["license_label"].endswith("credit required"),
    by_card["license_label"],
)
check(
    "…and the credit does NOT repeat it",
    "credit required" not in by_card["attribution"],
    by_card["attribution"],
)
check(
    "the credit names the licence on its own",
    "(CC BY 4.0)" in by_card["attribution"],
    by_card["attribution"],
)
check(
    "a CC0 badge says public domain, not an obligation",
    cc0_card["license_label"].endswith("public domain"),
    cc0_card["license_label"],
)
check(
    "…and its credit carries the version too",
    "CC0 1.0" in cc0_card["attribution"],
    cc0_card["attribution"],
)
# The version has to reach the NAME as well as the label, or a CC BY 3.0 sound
# is credited as plain "CC BY" and the reader cannot tell which terms apply.
check(
    "a 3.0 sound is NAMED 3.0",
    freesound._licence_info("http://creativecommons.org/licenses/by/3.0/")["name"] == "CC BY 3.0",
    freesound._licence_info("http://creativecommons.org/licenses/by/3.0/")["name"],
)
check(
    "the versionless prose form is named without one, not with a guess",
    freesound._licence_info(BY)["name"] == "CC BY",
    freesound._licence_info(BY)["name"],
)


print("\nThe credit SURVIVES the import — it is stored, not recomputed")
# The obligation outlives the search that found it: the pane closes, the project
# is reopened weeks later, and the person publishing the video still has to know
# whom to name. That only works if the field round-trips through the schema.
card = AnimaticAsset(
    id="a1",
    kind="audio",
    upload_id="deadbeef1234",
    label="Test sound-freesound-9999.mp3",
    duration_ms=3500,
    attribution=by_card["attribution"],
)
check("AnimaticAsset carries an attribution", card.attribution == by_card["attribution"])
check(
    "…and it round-trips through the store's dict form",
    AnimaticAsset(**card.model_dump()).attribution == by_card["attribution"],
)
check(
    "…defaulting to empty for everything the user made themselves",
    AnimaticAsset(id="a2").attribution == "",
)

# The search item the client draws and the card it becomes must agree on the
# field names, or the credit is lost in the handover rather than in the store.
item = SoundSearchItem(**{k: v for k, v in by_card.items() if k in SoundSearchItem.model_fields})
check("SoundSearchItem accepts a normalised card as-is", item.id == "9999")
check("…and keeps the same attribution string", item.attribution == by_card["attribution"])
check("…and the same needs_credit flag", item.needs_credit is True)
check("…and the licence NAME, which the credit is built from", item.license_name == "CC BY 4.0", item.license_name)


print("\nThe key never leaves the server")
# `SoundStatus` is what the browser is told about the library's configuration.
# A field added here that carried the token would put it in the page source.
from server.schemas import SoundStatus  # noqa: E402  (read after the checks it belongs to)

fields = set(SoundStatus.model_fields)
check(
    "SoundStatus has no field that could carry a token",
    not any(w in f for f in fields for w in ("key", "token", "secret")),
    str(sorted(fields)),
)


print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The licence fence holds, and the credit survives the import.")

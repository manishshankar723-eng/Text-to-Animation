"""THE FIRST REFERENCE WAS DRAWN, PAID FOR, AND THEN THROWN AWAY.

Reported 27 Aug 2026, on the Cast step. The user pressed Generate for ANANYA
twice and could not get back to the first picture:

    "mai ananya ka pic banaya do baar magar mai pahla image nhi dekh pa raha
     hun … jaise Animatics regenerate ke baad do image dekh sakte hai aur jo
     select rahega ohi storypanel mai jayega"

`runGenerate` overwrote `referenceId` and `previewUrl` on the card. The image
itself was never lost — POST /characters/reference mints a FRESH
`uuid4().hex[:12]` per call and never touches the previous folder — so the take
was still sitting on the server, unreachable, with nothing in the UI pointing at
it. Two takes drawn, one payable, one invisible.

The board already solved this for panels (`PanelVersions` + the
/storyboards/{id}/panels/{i}/versions endpoints), and the user named that as the
behaviour they wanted. So the fix is the SAME control, not a new one:

  * `RefVersions.jsx` — the board's ‹ 1 / 2 › pill, reused verbatim down to the
    `.panel-versions` classes, sitting on the reference thumbnail.
  * Every generate and every upload APPENDS a take instead of replacing one.
  * ⚠ Picking a take swaps the card's live `referenceId` — the id
    `handleGenerate` hands to the board. What is on screen is what the panels
    are drawn from. A preview-only browser ("I can see it but can't have it")
    would not have answered the report.

⚠ NO SERVER WORK, AND THAT IS THE POINT. A panel redraw overwrites
`panel_NN.png`, which is why the board needs an archive on disk. A reference
never overwrites anything, so the takes are already durable and the whole
feature is client-side bookkeeping over ids that already exist.

⚠ WHY BOTH STEPS. The props step is the same card doing the same job with the
same buttons — the user's stated rule — so it gets the same control.

Run:
    python tests/ref_versions_check.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


comp = read("client", "src", "components", "RefVersions.jsx")
cast = read("client", "src", "components", "StoryboardCast.jsx")
assets = read("client", "src", "components", "StoryboardAssets.jsx")
board = read("client", "src", "components", "PanelVersions.jsx")
workflow = read("client", "src", "components", "ScriptToStoryboard.jsx")
css = read("client", "src", "styles", "storyboard.css")
main_py = read("server", "main.py")

# ---------------------------------------------------------------------------
print("[1] the take that is on screen is the take that ships")
# This is the whole report. Anything less is a picture viewer.
for name, src in (("cast", cast), ("props", assets)):
    check(
        f"the {name} step appends a take instead of overwriting one",
        "addVersion(i, { referenceId: res.reference_id" in src
        and "patch(i, { referenceId: res.reference_id" not in src,
    )
    check(
        f"…picking a take makes it live on the {name} card, id and preview "
        "together",
        "function pickVersion(i, n)" in src
        and "patch(i, { activeVersion: n, ...take })" in src,
    )
check(
    "⚠ …and the cast step still hands the board the card's LIVE `referenceId`, "
    "so the take on screen is the one every panel is drawn from",
    "if (ch.referenceId) refs[ch.name] = ch.referenceId;" in cast,
)
check(
    "…same on the props step",
    "if (it.referenceId) refs[it.name] = it.referenceId;" in assets,
)

# ---------------------------------------------------------------------------
print()
print("[2] an UPLOAD is a take too")
# Uploading used to be a one-way door: your own image replaced the drawn one and
# the drawn one was gone. It is an alternative to compare, like any other.
for name, src in (("cast", cast), ("props", assets)):
    check(
        f"the {name} step's upload appends as well",
        "addVersion(i, { referenceId: res.reference_id, previewUrl, "
        "uploaded: true })" in src,
    )
    check(
        f"⚠ …and the {name} step stores `uploaded` PER TAKE, or stepping back "
        "to your own image would let bulk Generate draw over it",
        "uploaded: prev.uploaded ?? false," in src,
    )

# ---------------------------------------------------------------------------
print()
print("[3] the takes survive leaving the step")
# The cast and props steps unmount on Back / forward; the workflow owns their
# saved fields. A durable list that forgot `versions` would keep only the last
# take — the exact loss this fixes.
for name, src in (("cast", cast), ("props", assets)):
    durable = src.split("const DURABLE = [")[1].split("]")[0]
    check(
        f"the {name} step marks `versions` and `activeVersion` durable",
        '"versions"' in durable and '"activeVersion"' in durable,
    )
    check(
        f"…and a {name} card saved before versions existed adopts its lone "
        "reference as take 1, rather than reading '0 versions' beside a "
        "picture that is plainly there",
        "prev.versions ??" in src and "prev.referenceId" in src,
    )
check(
    "the workflow still tracks every take's blob URL for revocation — a "
    "preview it never saw would leak",
    "previewUrls.current.push(fields.previewUrl)" in workflow,
)

# ---------------------------------------------------------------------------
print()
print("[4] appending is done off the LATEST card, not a stale snapshot")
# ⚠ `runGenerate(i, item)` deliberately takes a snapshot so "Generate all" does
# not wait on React state between iterations. Appending to that snapshot's
# `versions` would drop whatever landed after it was taken.
check(
    "the cast step reads the live array through a ref, not the closure",
    "const castRef = useRef(cast);" in cast
    and "castRef.current = cast;" in cast
    and "castRef.current[i]?.versions" in cast,
)
check(
    "…and the props step does the same",
    "const itemsRef = useRef(items);" in assets
    and "itemsRef.current = items;" in assets
    and "itemsRef.current[i]?.versions" in assets,
)

# ---------------------------------------------------------------------------
print()
print("[5] it is the BOARD's control, reused — not a second one invented")
# The user asked for this by pointing at the animatic/panel behaviour. A
# different-looking pill doing the same job is the mismatch they report most.
check(
    "RefVersions wears the board's own classes",
    '"panel-versions"' in comp
    and '"panel-versions-nav"' in comp
    and '"panel-versions-count"' in comp
    and '"panel-versions"' in board,
)
check(
    "…shows from ONE take with the arrows disabled, like the board — hiding it "
    "until there were two made the board's version control undiscoverable, and "
    "that was reported twice",
    "const only = total < 2;" in comp and "total < 1) return null;" in comp,
)
check(
    "…wraps at the ends, like the board's arrows",
    "(active + delta + total) % total" in comp
    and "(active + delta + total) % total" in board,
)
check(
    "⚠ …and swallows the click, or every arrow press would also open the "
    "lightbox behind it",
    "onClick={(e) => e.stopPropagation()}" in comp,
)
check(
    "both steps mount it on the thumbnail",
    "<RefVersions" in cast and "<RefVersions" in assets,
)
check(
    "⚠ …and the thumbnail is a positioning context, or the pill escapes it and "
    "anchors itself to the page",
    "position: relative;" in css.split(".cast-portrait {")[1].split("}")[0],
)
check(
    "the arrows are dead while that card — or a bulk run — is drawing",
    "disabled={ch.busy || bulkBusy || busy}" in cast
    and "disabled={it.busy || bulkBusy || busy}" in assets,
)

# ---------------------------------------------------------------------------
print()
print("[6] the takes really are still on the server")
# The client keeps ids, not images. That is only safe because a reference is
# never written over — unlike a panel, which is why the board needed an archive.
ref_block = main_py.split('@app.post("/characters/reference"')[1][:4000]
check(
    "⚠ a generated reference gets a FRESH id and its own folder, so an earlier "
    "take is never overwritten",
    "reference_id = uuid.uuid4().hex[:12]" in ref_block
    and "_references" in ref_block,
)
check(
    "…and any take can still be fetched back by its id",
    "getReferenceImageUrl" in read("client", "src", "api.js")
    and "/characters/reference/" in main_py,
)

# ---------------------------------------------------------------------------
print()
print("[7] a reference already paid for survives leaving the workflow")
# ⚠ THE SECOND HALF OF THE SAME REPORT: *"mai back aaya to mera ananya wala
# photo dikh hi nhi raha hai … baar baar generate karna pare, usko paisa lagta
# hai."* Section [3] keeps the takes while the workflow is MOUNTED. Walking out
# to Home unmounts it, and everything above lived in React state and blob URLs,
# both of which die with it. Only the draft survives that — and until now the
# cast and props references were never written to it, and never read back.
draft_py = read("server", "schemas.py")
api_js = read("client", "src", "api.js")
check(
    "the draft carries EVERY take, not just the live one — the others cost "
    "images too",
    "class RefTake(BaseModel):" in draft_py
    and "character_takes: dict[str, list[RefTake]]" in draft_py
    and "asset_takes: dict[str, list[RefTake]]" in draft_py,
)
check(
    "…and they are writable, or the client could never save them",
    "character_takes: dict[str, list[RefTake]] | None = None" in draft_py
    and "asset_takes: dict[str, list[RefTake]] | None = None" in draft_py,
)
check(
    "the draft response hands them back",
    'character_takes=p.get("character_takes") or {}' in main_py
    and 'asset_takes=p.get("asset_takes") or {}' in main_py,
)
check(
    "⚠ the workflow saves the STEPS' own refs, not just the ones picked up on "
    "the way out of the cast step — a draft saved before that walk would have "
    "forgotten every image just drawn",
    "refIdsOf(savedCastRefs)" in workflow
    and "asset_refs: refIdsOf(savedAssetRefs)" in workflow
    and "character_takes: refTakesOf(savedCastRefs)" in workflow,
)
check(
    "…and drawing one actually triggers that save",
    "savedCastRefs," in workflow.split("}, [")[-2].split("]);")[0]
    or "savedCastRefs,\n    savedAssetRefs," in workflow,
)
check(
    "⚠ what is stored is the ID, never the blob URL — an object URL means "
    "nothing to the next page load, let alone another machine",
    "reference_id: t.referenceId" in workflow
    and "previewUrl" not in workflow.split("function refTakesOf")[1].split("\n  }")[0],
)
check(
    "resuming puts the references back on both steps",
    "restoreSavedRefs(setSavedCastRefs, d.character_refs, d.character_takes);"
    in workflow
    and "restoreSavedRefs(setSavedAssetRefs, d.asset_refs, d.asset_takes);"
    in workflow,
)
check(
    "⚠ …and the live id is always reachable from the arrows, even when the "
    "take list disagrees with it",
    "if (active < 0) {" in workflow,
)

# ---------------------------------------------------------------------------
print()
print("[8] the picture comes back from the server, not from memory")
check(
    "there is an authed fetch for a reference image — it is owner-scoped, so "
    "it can never be a plain <img src>",
    "export async function fetchReferenceImage" in api_js
    and "Authorization: `Bearer ${token}`" in api_js,
)
check(
    "resuming pulls down the LIVE take's picture",
    "api\n        .fetchReferenceImage(entry.referenceId)" in workflow
    or ".fetchReferenceImage(entry.referenceId)" in workflow,
)
check(
    "⚠ …and only that one. A dozen names times three takes is megabytes "
    "nobody asked for, so an older take fetches its image when the arrows "
    "actually land on it",
    "await api.fetchReferenceImage(take.referenceId)" in cast
    and "await api.fetchReferenceImage(take.referenceId)" in assets,
)
check(
    "…and it is kept once fetched, so stepping back and forth costs one request",
    "k === n ? filled : v" in cast and "k === n ? filled : v" in assets,
)
check(
    "a reference the server no longer has leaves a placeholder instead of "
    "breaking the step",
    ".catch(() => {});" in workflow,
)
check(
    "the restored previews are still tracked for revocation",
    "previewUrls.current.push(url);" in workflow,
)

# ---------------------------------------------------------------------------
print()
print("[9] the count is legible in BOTH themes")
# ⚠ REPORTED WITH A SCREENSHOT: in light mode the pill showed two arrows around
# nothing. `.panel-versions` paints a hard-coded dark scrim (it sits over a
# picture, in either theme) but the count was `var(--text)`, which flips — so
# light mode put near-black ink on a near-black pill.
count_rule = css.split(".panel-versions-count {")[1].split("}")[0]
check(
    "the count's colour does not flip with the theme, because its background "
    "does not either",
    "color: var(--text)" not in count_rule and "color: #f4f6fa;" in count_rule,
)
check(
    "…and the scrim it sits on is still the dark one both themes get",
    "rgba(12, 14, 18, 0.88)" in css.split(".panel-versions {")[1].split("}")[0],
)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print(
    "Every take is kept, the one on screen is the one the panels are drawn "
    "from, and it is the board's own control doing it."
)

"""THE LANDING HERO'S FOUR TILES ARE PICTURES THE OWNER UPLOADS, AND THEY FOLLOW
WHAT IS LIVE.

Why this exists, in one sentence: the four biggest pictures on the page every
stranger lands on were hand-drawn SVG inside `Landing.jsx`, so they could not be
changed without a developer and could not follow a workflow being hidden — and
three separate rules had to hold at once for the fix to be true rather than
merely look true.

    tests/features_check.py pins live / soon / hidden and who may see what
    tests/showcase_check.py pins the OTHER public page's pictures
    THIS FILE               pins the hero-art store, its two public routes, the
                            visibility rule, and the client wiring that draws it

⚠ **THE VISIBILITY RULE IS THE POINT OF THE FILE.** Asked for directly: *"aisa
bana hi jo live hai uska dikhe image yaha pe aur jo hide hai uska nhi dikhe magar
mai jab hode se unhode karun to yeha pe image aa jana chaiye."* Section 5 hides a
workflow, proves its picture leaves the public payload, un-hides it and proves the
picture comes back — the round trip, not just the first half, because "hidden"
that also throws the file away is a different product.

⚠ **AND SO IS "A SEVENTH WORKFLOW NEEDS NO CODE".** *"aage ami aur v workflow
banau to o v same fuctiuon mai chale."* Section 6 invents one in the catalogue and
puts a picture on it without touching a line of this app.

⚠ **THE LAST SECTION IS SOURCE GREPS, AND THEY ARE NOT DECORATION.** Every route
check here would still pass with `Landing.jsx` drawing its four hard-coded SVG
tiles and ignoring the endpoint completely — the store would be right and the page
would be wrong. Those assertions are the ones that survive the next component.

⚠ **IT TOUCHES NOTHING REAL.** Every store is pointed at a fresh temporary
directory BEFORE `server.config` is imported. No MongoDB, no network, no AI
quota, no browser.

    python tests/landing_art_check.py
"""

import io
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Point every store at a temporary directory BEFORE anything imports config.
# `load_dotenv()` does not override variables that are already set, so these beat
# whatever is in the developer's .env.
# ---------------------------------------------------------------------------
_TMP = tempfile.mkdtemp(prefix="landing_art_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_FEATURES_PATH"] = os.path.join(_TMP, "features.json")
os.environ["API_LOCAL_LANDING_PATH"] = os.path.join(_TMP, "landing.json")
os.environ["API_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["API_LANDING_DIR"] = os.path.join(_TMP, "uploads", "_landing")
# ⚠ NO CACHE IN THIS FILE. The store is read straight after every write here, and
# a 60-second TTL would make half these assertions pass or fail on timing.
os.environ["API_LANDING_CACHE_TTL_S"] = "0"
os.environ["API_FEATURES_CACHE_TTL_S"] = "0"
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "landing-art-check-not-a-real-secret"
os.environ["ADMIN_EMAILS"] = "boss@example.com"

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import landing as landing_mod  # noqa: E402
from server.main import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "client", "src")

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def png_bytes(w=1600, h=1200, colour=(30, 90, 200, 255)):
    """A real PNG with a real alpha channel, made in memory.

    ⚠ DELIBERATELY BIGGER THAN `IMAGE_MAX_PX`, so the downscale is exercised by
    the very first upload rather than by one special case at the end.
    """
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), colour).save(buf, "PNG")
    return buf.getvalue()


def accounts():
    """One administrator, one ordinary user. Returns both tokens."""
    client.post("/auth/register", json={"email": "boss@example.com", "password": "password123"})
    client.post("/auth/register", json={"email": "user@example.com", "password": "password123"})
    admin = client.post(
        "/auth/login", json={"email": "boss@example.com", "password": "password123"}
    ).json()["access_token"]
    user = client.post(
        "/auth/login", json={"email": "user@example.com", "password": "password123"}
    ).json()["access_token"]
    return admin, user


ADMIN, USER = accounts()

WF = "plan-and-script"
OTHER = "script-to-storyboard"


def source(*rel):
    return open(os.path.join(SRC, *rel), encoding="utf-8").read()


def public_art():
    return client.get("/public/landing/art").json()["art"]


# ===========================================================================
print("\n1. The public read — no token, no account, and empty is normal")
# ===========================================================================
# ⚠ THE WHOLE REASON THE ROUTE IS PUBLIC. This art is in the hero of the page you
# reach BEFORE you have a token, so anything it cannot read without one it cannot
# draw.
r = client.get("/public/landing/art")
check("GET /public/landing/art needs no token", r.status_code, 200)
# ⚠ AND AN EMPTY MAP IS THE SHIPPED STATE. With nothing uploaded the hero draws
# the SVG tiles it always drew — this feature only ever REPLACES a drawing.
check("no pictures is a normal answer, not an error", r.json(), {"art": {}})


# ===========================================================================
print("\n2. The write is admin-only, and invisible to everybody else")
# ===========================================================================
# 404, not 403 — same rule as every other panel route: an ordinary account must
# not even be able to confirm the screen exists.
check(
    "a user gets 404 from GET /admin/landing/art",
    client.get("/admin/landing/art", headers=bearer(USER)).status_code,
    404,
)
check(
    "a user gets 404 from the upload route",
    client.post(
        f"/admin/landing/art/{WF}/image",
        files={"image": ("x.png", png_bytes(), "image/png")},
        headers=bearer(USER),
    ).status_code,
    404,
)
check(
    "an anonymous visitor gets 401 from GET /admin/landing/art",
    client.get("/admin/landing/art").status_code,
    401,
)
check("and nothing was uploaded", public_art(), {})


# ===========================================================================
print("\n3. The list IS the workflow catalogue — there is nothing to create")
# ===========================================================================
# ⚠ THE ONE STRUCTURAL DIFFERENCE FROM BANNERS AND SHOWCASE, and the reason a
# seventh workflow needs no code: the rows are not invented by an administrator,
# they are `features.py`'s workflow list. So there is no POST that makes a row and
# no DELETE that removes one.
r = client.get("/admin/landing/art", headers=bearer(ADMIN))
check("the panel can read it", r.status_code, 200)
panel = r.json()
ids = [w["id"] for w in panel["workflows"]]
check("every workflow in the catalogue has a row", len(ids), 6)
check("including the two that were never in the hero art", "text-to-image" in ids)
check("no row has a picture yet", [w for w in panel["workflows"] if w["has_image"]], [])
check("the panel is told how many tiles the hero draws", panel["hero_tiles"], 4)
check("and what it may upload", panel["allowed_types"], list(landing_mod.ALLOWED_IMAGE_TYPES))
# ⚠ THE FIRST FOUR VISIBLE ONES ARE THE ONES DRAWN, and the panel says which.
check("the first four are in the hero", [w["id"] for w in panel["workflows"] if w["in_hero"]], ids[:4])
check("the fifth and sixth are not", [w["id"] for w in panel["workflows"] if not w["in_hero"]], ids[4:])
# There is no create route to find.
check(
    "there is no POST /admin/landing/art",
    client.post("/admin/landing/art", json={"id": "made-up"}, headers=bearer(ADMIN)).status_code
    in (404, 405),
)


# ===========================================================================
print("\n4. An upload lands on the workflow, is normalised, and goes public")
# ===========================================================================
r = client.post(
    f"/admin/landing/art/{WF}/image",
    files={"image": ("hero.png", png_bytes(), "image/png")},
    headers=bearer(ADMIN),
)
check("POST the picture is accepted", r.status_code, 200)
row = r.json()
check("the row says it has one", row["has_image"], True)
check("and it is on the page", row["on_page"] and row["in_hero"], True)
URL = row["image_url"]
check("the url is relative, for the client to resolve", URL.startswith("/public/landing/image/"))
STAMP = URL.rsplit("/", 1)[-1]

# ⚠ WEBP, AND DOWNSCALED. A 1600px PNG on a 200px tile, four to a page, is the
# waste this normalisation exists to stop.
img = client.get(URL)
check("the picture is served publicly", img.status_code, 200)
check("as WEBP", img.headers["content-type"], "image/webp")
check(
    "cached forever, because the address IS the file",
    "immutable" in img.headers.get("cache-control", ""),
)
opened = Image.open(io.BytesIO(img.content))
check("it was downscaled to the tile's size", max(opened.size), landing_mod.IMAGE_MAX_PX)
check("and it kept its alpha channel", opened.mode in ("RGBA", "RGB"), True)

check("it is in the public payload, keyed by workflow id", public_art(), {WF: URL})

# ⚠ A NEW UPLOAD IS A NEW URL, WHICH IS THE ENTIRE CACHE STRATEGY. Overwriting one
# path is exactly how a picture change fails to appear for the person who most
# needed to see it.
r2 = client.post(
    f"/admin/landing/art/{WF}/image",
    files={"image": ("hero2.png", png_bytes(colour=(200, 20, 20, 255)), "image/png")},
    headers=bearer(ADMIN),
)
NEW_URL = r2.json()["image_url"]
check("replacing it gives a NEW address", NEW_URL != URL)
check("and the old file is gone", client.get(URL).status_code, 404)
check("and the old file is off the disk", os.path.isfile(landing_mod.image_path(STAMP)), False)

# Junk is refused at each of the three gates, cheapest first.
check(
    "a wrong content type is refused",
    client.post(
        f"/admin/landing/art/{WF}/image",
        files={"image": ("x.gif", b"GIF89a", "image/gif")},
        headers=bearer(ADMIN),
    ).status_code,
    415,
)
check(
    "bytes that are not an image are refused",
    client.post(
        f"/admin/landing/art/{WF}/image",
        files={"image": ("x.png", b"not a picture at all", "image/png")},
        headers=bearer(ADMIN),
    ).status_code,
    400,
)
# ⚠ AN ID THAT IS NOT A WORKFLOW IS A 404, not a new row. The store's key is the
# workflow, so an upsert on an unknown key would invent a picture for a workflow
# that does not exist and nothing would ever draw it.
check(
    "an unknown workflow is refused",
    client.post(
        "/admin/landing/art/not-a-workflow/image",
        files={"image": ("x.png", png_bytes(), "image/png")},
        headers=bearer(ADMIN),
    ).status_code,
    404,
)
# ⚠ AND A PATH-SHAPED ONE CANNOT REACH THE FILESYSTEM.
check("a path-shaped workflow id is not a workflow", landing_mod.is_workflow("../../etc/passwd"), False)
check("nor is an empty one", landing_mod.is_workflow(""), False)

# ⚠ A WELL-FORMED STAMP NOBODY UPLOADED IS A 404, not a read of the directory.
check(
    "a made-up stamp is not served",
    client.get("/public/landing/image/abcdefabcdef").status_code,
    404,
)
check(
    "and neither is a path",
    client.get("/public/landing/image/..%2F..%2Fconfig").status_code,
    404,
)


# ===========================================================================
print("\n5. HIDE takes the picture off the page — and UN-HIDE brings it back")
# ===========================================================================
# ⚠ THE ROUND TRIP, NOT JUST THE FIRST HALF. Asked for in one breath: *"jo hide
# hai uska nhi dikhe magar mai jab hode se unhode karun to yeha pe image aa jana
# chaiye."* A "hidden" that also threw the file away would pass the first four
# assertions here and fail the product.
client.post(
    f"/admin/landing/art/{OTHER}/image",
    files={"image": ("two.png", png_bytes(colour=(20, 160, 90, 255)), "image/png")},
    headers=bearer(ADMIN),
)
check("two workflows now have pictures", sorted(public_art()), sorted([WF, OTHER]))

r = client.patch(
    f"/admin/features/workflow.{WF}", json={"status": "hidden"}, headers=bearer(ADMIN)
)
check("hiding the workflow is accepted", r.status_code, 200)
check("it is gone from /public/workflows", WF not in [
    w["id"] for w in client.get("/public/workflows").json()["workflows"]
])
# ⚠ THE FILTER IS ON THE SERVER, NOT IN THE BROWSER. A picture of a switched-off
# workflow sitting in a public JSON payload is the same leak the landing page's
# hand-written workflow list used to be.
check("and its picture is gone from the public payload", WF in public_art(), False)
check("the other one is untouched", sorted(public_art()), [OTHER])
# But the file is KEPT, and the panel says exactly what happened.
hidden_row = next(
    w for w in client.get("/admin/landing/art", headers=bearer(ADMIN)).json()["workflows"]
    if w["id"] == WF
)
check("the panel still shows its picture", hidden_row["has_image"], True)
check("and says it is not on the page", hidden_row["on_page"], False)
check("nor in the hero", hidden_row["in_hero"], False)

client.patch(f"/admin/features/workflow.{WF}", json={"status": "live"}, headers=bearer(ADMIN))
check("un-hiding brings the picture straight back", WF in public_art(), True)
check("with the same address it had", public_art()[WF], NEW_URL)

# ⚠ "SOON" IS NOT "HIDDEN". A soon workflow is drawn on the landing page with a
# badge on it — it is a roadmap teaser, not a secret — so its tile keeps its
# picture. Squashing the two was the bug `features.py` splits `visible` from `on`
# to avoid.
client.patch(f"/admin/features/workflow.{WF}", json={"status": "soon"}, headers=bearer(ADMIN))
check("a SOON workflow keeps its picture", WF in public_art(), True)
client.patch(f"/admin/features/workflow.{WF}", json={"status": "live"}, headers=bearer(ADMIN))


# ===========================================================================
print("\n6. Removing a picture is not removing a row")
# ===========================================================================
r = client.delete(f"/admin/landing/art/{OTHER}/image", headers=bearer(ADMIN))
check("DELETE the picture is accepted", r.status_code, 200)
check("the row is still there", r.json()["id"], OTHER)
check("with no picture on it", r.json()["has_image"], False)
check("it left the public payload", OTHER in public_art(), False)
check("the workflow still has a row in the panel", OTHER in [
    w["id"] for w in client.get("/admin/landing/art", headers=bearer(ADMIN)).json()["workflows"]
])
check(
    "deleting a picture that is not there is harmless",
    client.delete(f"/admin/landing/art/{OTHER}/image", headers=bearer(ADMIN)).status_code,
    200,
)
check(
    "but an unknown workflow is still a 404",
    client.delete("/admin/landing/art/nope/image", headers=bearer(ADMIN)).status_code,
    404,
)


# ===========================================================================
print("\n7. A SEVENTH WORKFLOW NEEDS NO CODE — the whole second half of the ask")
# ===========================================================================
# ⚠ *"aage ami aur v workflow banau to o v same fuctiuon mai chale."* A workflow is
# added to this app by putting it in `features._WORKFLOWS` (and in `Sidebar.jsx`,
# which mirrors it) — an administrator cannot invent one from the panel, and never
# could. So this section adds one THE WAY IT IS ACTUALLY ADDED and then proves the
# landing-art screen, the store and the public payload all cope with no further
# code at all. If a future change makes this fail, the feature has quietly gone
# back to being a list of four.
#
# ⚠ IT PATCHES THE CATALOGUE, NOT THE STORE, and that is the point of the check:
# `save_feature` cannot set `group`, so a stored row for an unknown key becomes a
# CAPABILITY, not a workflow. The catalogue is the only place a workflow is born.
from server import features as features_mod  # noqa: E402

_WAS = list(features_mod._WORKFLOWS)
features_mod._WORKFLOWS.insert(0, ("brand-new-thing", "Brand New Thing", "✨"))
features_mod.all_features(fresh=True)
try:
    rows = client.get("/admin/landing/art", headers=bearer(ADMIN)).json()["workflows"]
    check("the new workflow has a row with no code change", "brand-new-thing" in [w["id"] for w in rows])
    # First in the catalogue, so `order` 0 — it takes the first hero tile.
    check("and it sorted into the hero", rows[0]["id"], "brand-new-thing")
    check("with no picture and no drawing to lose", rows[0]["has_image"], False)
    r = client.post(
        "/admin/landing/art/brand-new-thing/image",
        files={"image": ("new.png", png_bytes(colour=(240, 200, 40, 255)), "image/png")},
        headers=bearer(ADMIN),
    )
    check("a picture goes on it", r.status_code, 200)
    check("and reaches the public payload", "brand-new-thing" in public_art(), True)
    # ⚠ AND IT IS NOT ON THE LANDING PAGE UNTIL SOMEBODY WRITES ITS PARAGRAPH.
    # That is `Landing.jsx`'s pre-existing rule (`WORKFLOWS` is the sales copy and
    # an id with no copy is skipped), NOT something this feature changed — pinned
    # here so nobody reads "no code needed" as "no copy needed".
    check(
        "the public workflow list carries it",
        "brand-new-thing" in [w["id"] for w in client.get("/public/workflows").json()["workflows"]],
    )
    check(
        "and the hero needs a `COPY` entry to draw it",
        "COPY[w.id]" in source("components", "Landing.jsx"),
    )
    # ⚠ AND THE ONES PUSHED OUT ARE REPORTED, NOT SILENTLY DROPPED. Somebody whose
    # fifth workflow has a perfect picture and no tile has to be told why — which
    # is the difference between `on_page` and `in_hero` on the row.
    rows = client.get("/admin/landing/art", headers=bearer(ADMIN)).json()["workflows"]
    check("seven workflows now have rows", len(rows), 7)
    check("only four are in the hero", len([w for w in rows if w["in_hero"]]), 4)
    check(
        "and the three pushed out say so",
        len([w for w in rows if w["on_page"] and not w["in_hero"]]),
        3,
    )
finally:
    # ⚠ PUT THE CATALOGUE BACK WHATEVER HAPPENS. It is module state in a shared
    # process; leaving a seventh workflow in it would make every assertion after
    # this section depend on which order the sections ran in.
    features_mod._WORKFLOWS[:] = _WAS
    features_mod.all_features(fresh=True)


# ===========================================================================
print("\n8. The hero actually DRAWS it — the greps that outlive the routes")
# ===========================================================================
# ⚠ EVERY ASSERTION ABOVE WOULD PASS WITH `Landing.jsx` IGNORING THE ENDPOINT.
# The store would be right and the page would still be four hard-coded SVG tiles,
# which is the exact fault this work was done to fix.
lp = source("components", "Landing.jsx")
check("the page fetches the art", "publicLandingArt()" in lp)
check("and hands it to the hero", re.search(r"<PipelineArt\s+workflows=\{shown\}\s+art=\{art\}", lp) is not None)
# ⚠ THE FOUR TILES ARE NO LONGER FOUR LINES OF JSX. This is the assertion that
# breaks if somebody "simplifies" the map back into a fixed list.
check("the tiles are built from the live list", "tiles.map(" in lp)
check('and the hard-coded "Plan & script" tile is gone', '<ArtStep n="1" label="Plan & script">' not in lp)
check("a tile prefers the uploaded picture", "lp-art-photo" in lp)
check("and falls back to the drawing", "ART_BY_ID" in lp)
# The four drawings must still be reachable by id — a fallback nobody can look up
# is a fallback that has been deleted.
for _art in ("ScriptArt", "BoardArt", "PosesArt", "TimelineArt"):
    check(f"{_art} is still mapped to a workflow", f'": {_art}' in lp or f'"{_art}' in lp or _art in lp)
check("and an unmapped workflow gets its glyph", "lp-art-glyph" in lp)
# ⚠ THE SHEET HAS TO LOSE ITS PADDING FOR A PHOTOGRAPH, or every uploaded picture
# wears a hairline of white paper. The CSS is half the fix.
css = open(os.path.join(SRC, "styles", "landing.css"), encoding="utf-8").read()
check("the photo tile is full-bleed", ".lp-art-sheet.has-photo" in css)
check("and crops rather than letterboxes", "object-fit: cover" in css.split(".lp-art-photo")[1][:200])

# The panel screen, and its place in the rail.
ap = source("admin", "AdminPanel.jsx")
check("there is a Landing tab", 'id: "landing"' in ap)
check("the panel mounts it", "<AdminLanding />" in ap)
# ⚠ THE OTHER TABS MUST ALL STILL BE THERE. This repo's own history: a patch
# aimed at two lines of this component took the first 39 lines with it.
for _tab in ("overview", "users", "features", "pricing", "sales", "brand", "explore", "activity"):
    check(f"the {_tab} tab survived", f'id: "{_tab}"' in ap)

al = source("admin", "AdminLanding.jsx")
check("the screen reads the admin list", "adminListLandingArt()" in al)
check("uploads", "adminUploadLandingImage(" in al)
check("and removes", "adminRemoveLandingImage(" in al)
# ⚠ NO CREATE AND NO DELETE, because there is no row to make or destroy. Checked
# against the API surface rather than against button words: a grep for "＋ New"
# hits this file's own comment explaining that there is no such button.
apisrc = open(os.path.join(SRC, "api.js"), encoding="utf-8").read()
check("there is no create call to make", "adminCreateLandingArt" not in apisrc)
check("and no delete-the-row call", "adminDeleteLandingArt" not in apisrc)
check("so the screen cannot offer either", "adminCreate" not in al and "adminDelete" not in al)
# ⚠ AND IT DOES NOT GROW A SECOND VISIBILITY SWITCH. Live / soon / hidden is the
# Features tab's answer; a second control for it here is two places to disagree
# about what is on the front page.
check("it does not edit visibility", "adminUpdateFeature" not in al and '"hidden"' not in al)
# It must SAY what will happen, though — that is the whole reason the row carries
# `on_page` and `in_hero`.
check("it reports whether the picture is on the page", "on_page" in al and "in_hero" in al)


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures else "All landing-art checks passed."))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if failures else 0)

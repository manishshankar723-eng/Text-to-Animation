"""EXPLORE IS THE PUBLIC PAGE NOW, AND ITS WALL IS CURATED WORK A VISITOR CAN PLAY.

Why this exists, in one sentence: Explore changed sides — it used to be the
signed-in front door showing the customer's OWN projects, and it is now the
marketing page a stranger lands on — and three separate rules had to hold at once
for that to be true rather than merely look true.

    tests/admin_check.py    pins the routes and the roles
    tests/branding_check.py pins the name and the mark on the same public pages
    THIS FILE               pins the showcase store, its two public routes, and
                            the rule that no signed-in screen can reach Explore

⚠ **THE THREE RULES, AND WHY THEY ARE ONE FILE.** They were asked for in one
breath — *"any logged in user must not see explore ... the explore page is going
to be only used for getting users ... but the videos or images should be
clickable and be able to use it properly play"* — and each one is worthless
alone. A curated wall nobody outside can read is a private gallery; a public page
that still hangs off the rail is the old page with extra steps; a wall of
pictures that cannot play is a brochure.

⚠ **THE LAST TWO SECTIONS ARE SOURCE GREPS, AND THEY ARE THE POINT OF THE FILE.**
Every route check here would still pass if somebody put the Explore row back in
the sidebar, or wired a card straight into a workflow without a sign-in. The
store would be right and the product would be wrong. Those are the only
assertions that survive the next component being written.

⚠ **A VIDEO IS STORED AS IT ARRIVED AND SERVED WITH RANGES.** Section 4 pins the
byte-for-byte round trip, because a re-encode on the request path is the change
somebody makes when an upload feels slow — and it is the change that breaks
seeking on the one screen where seeking is the product demo.

⚠ **IT TOUCHES NOTHING REAL.** Every store is pointed at a fresh temporary
directory BEFORE `server.config` is imported. No MongoDB, no network, no AI
quota, no browser.

    python tests/showcase_check.py
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
_TMP = tempfile.mkdtemp(prefix="showcase_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_SHOWCASE_PATH"] = os.path.join(_TMP, "showcase.json")
os.environ["API_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["API_SHOWCASE_DIR"] = os.path.join(_TMP, "uploads", "_showcase")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "showcase-check-not-a-real-secret"
os.environ["ADMIN_EMAILS"] = "boss@example.com"

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import showcase as showcase_mod  # noqa: E402
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


def png_bytes(w=64, h=64, colour=(200, 30, 30, 255)):
    """A real PNG with a real alpha channel, made in memory."""
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), colour).save(buf, "PNG")
    return buf.getvalue()


# ⚠ NOT A REAL MP4, AND IT DOES NOT NEED TO BE. Nothing on this path decodes a
# clip — that is the whole point of "stored as it arrived" — so what matters is
# that the exact bytes handed in come back out.
#
# ⚠ IT IS ALSO WHY THE EARLY SECTIONS SEE NO AUTOMATIC POSTER. These bytes are
# not a decodable clip, so the grab added in section 11 finds nothing and leaves
# the slot empty - which is exactly the behaviour those sections were written to
# describe, and it now doubles as proof that a failed grab costs the upload
# nothing. Real clips, made with ffmpeg, are in section 11.
FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 8


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


def source(*rel):
    return open(os.path.join(SRC, *rel), encoding="utf-8").read()


# ===========================================================================
print("\n1. The public read — no token, no account")
# ===========================================================================
# ⚠ THE WHOLE REASON THE ROUTE IS PUBLIC. This wall is on the page you reach
# BEFORE you have a token, so anything it cannot read without one it cannot draw.
r = client.get("/public/showcase")
check("GET /public/showcase needs no token", r.status_code, 200)
check("an empty wall is a normal answer, not an error", r.json(), {"items": []})


# ===========================================================================
print("\n2. The write is admin-only, and invisible to everybody else")
# ===========================================================================
# 404, not 403 — same rule as every other panel route: an ordinary account must
# not even be able to confirm the screen exists.
check(
    "a user gets 404 from GET /admin/showcase",
    client.get("/admin/showcase", headers=bearer(USER)).status_code,
    404,
)
check(
    "a user gets 404 from POST /admin/showcase",
    client.post(
        "/admin/showcase", json={"title": "Mine now"}, headers=bearer(USER)
    ).status_code,
    404,
)
check(
    "an anonymous visitor gets 401 from GET /admin/showcase",
    client.get("/admin/showcase").status_code,
    401,
)
check("and nothing was created", client.get("/public/showcase").json()["items"], [])


# ===========================================================================
print("\n3. An item with no file is NOT on the page")
# ===========================================================================
# ⚠ THE HALF-FINISHED STATE IS REAL AND IS NORMAL: the row is created first and
# the upload lands on it afterwards, so "live but empty" is a thirty-second state
# inside the panel every single time. A card with nothing in it, on the page
# everybody lands on, is worse than one fewer card.
r = client.post(
    "/admin/showcase",
    json={"title": "Chai break", "blurb": "One afternoon.", "workflow": "script-to-storyboard"},
    headers=bearer(ADMIN),
)
check("POST /admin/showcase is accepted", r.status_code, 201)
item = r.json()
ITEM = item.get("id", "")
check("it is switched on by default", item.get("active"), True)
check("but it is not LIVE, because it has no file", item.get("live"), False)
check("the panel sees it", len(client.get("/admin/showcase", headers=bearer(ADMIN)).json()["items"]), 1)
check("the public wall does not", client.get("/public/showcase").json()["items"], [])

# ⚠ 400 AND NOT 422, AND THAT IS THE INTERESTING HALF. Two spaces satisfy
# Pydantic's `min_length=1` and get all the way to `create_item`, which collapses
# whitespace and finds nothing left. Both refusals matter: the schema catches an
# absent field, the store catches a blank one.
check(
    "an empty title is refused by the schema",
    client.post("/admin/showcase", json={}, headers=bearer(ADMIN)).status_code,
    422,
)
check(
    "and a whitespace-only one by the store",
    client.post("/admin/showcase", json={"title": "  "}, headers=bearer(ADMIN)).status_code,
    400,
)
check(
    "a made-up shape is refused",
    client.patch(
        f"/admin/showcase/{ITEM}", json={"aspect": "3:7"}, headers=bearer(ADMIN)
    ).status_code,
    400,
)
# ⚠ WHITESPACE IS COLLAPSED, NOT JUST TRIMMED. The caption clamps to two lines,
# and a pasted newline is an invisible character that changes where it falls.
r = client.patch(
    f"/admin/showcase/{ITEM}", json={"blurb": "  one   \n  line  "}, headers=bearer(ADMIN)
)
check("whitespace inside a caption is collapsed", r.json().get("blurb"), "one line")


# ===========================================================================
print("\n4. A video is stored as it arrived, and served back byte for byte")
# ===========================================================================
r = client.post(
    f"/admin/showcase/{ITEM}/media",
    files={"media": ("reel.mp4", FAKE_MP4, "video/mp4")},
    headers=bearer(ADMIN),
)
check("POST .../media takes an MP4", r.status_code, 200)
row = r.json() if r.status_code == 200 else {}
check("and records it as a video", row.get("kind"), "video")
check("the item is live now", row.get("live"), True)

wall = client.get("/public/showcase").json()["items"]
check("the public wall has it", len(wall), 1)
card = wall[0] if wall else {}
check("the card knows it moves", card.get("kind"), "video")
check("it carries no still yet", card.get("poster_url"), "")
check(
    "the payload is words, a shape and two addresses — nothing else",
    sorted(card),
    ["aspect", "blurb", "id", "kind", "media_url", "poster_url", "title", "workflow"],
)

media = client.get(card.get("media_url") or "/public/showcase/media/zzz")
check("the clip is public too", media.status_code, 200)
# ⚠ BYTE FOR BYTE. A re-encode on the request path is what somebody adds when an
# upload feels slow, and it is what breaks seeking on the one screen where
# watching the film IS the product demo.
check("and it is the file that went in, unchanged", media.content, FAKE_MP4)
check("served as a video", media.headers.get("content-type"), "video/mp4")
check(
    "cached for ever, because the address IS the file",
    "immutable" in (media.headers.get("cache-control") or ""),
)

# ⚠ AN ID THAT MERELY LOOKS RIGHT IS NOT ENOUGH. Serving any well-formed name
# out of the directory would turn this route into a read of whatever else ever
# landed there.
check(
    "an unknown but well-formed id is 404",
    client.get("/public/showcase/media/" + "a" * 12).status_code,
    404,
)
check(
    "and so is a path that is not an id at all",
    client.get("/public/showcase/media/..%2F..%2Fusers.json").status_code,
    404,
)

check(
    "a .mov is refused — Chrome on Windows will not play it",
    client.post(
        f"/admin/showcase/{ITEM}/media",
        files={"media": ("clip.mov", FAKE_MP4, "video/quicktime")},
        headers=bearer(ADMIN),
    ).status_code,
    415,
)


# ===========================================================================
print("\n5. A still can still be uploaded by hand, and it overrules the grab")
# ===========================================================================
r = client.post(
    f"/admin/showcase/{ITEM}/poster",
    files={"image": ("still.png", png_bytes(320, 180), "image/png")},
    headers=bearer(ADMIN),
)
check("POST .../poster is accepted", r.status_code, 200)
check("the row carries a still now", r.json().get("has_poster"), True)
card = client.get("/public/showcase").json()["items"][0]
check("and the card does", bool(card.get("poster_url")), True)
poster = client.get(card["poster_url"])
check("the still is public", poster.status_code, 200)
check("and re-encoded to WEBP", poster.headers.get("content-type"), "image/webp")
check(
    "a video is not accepted as a still",
    client.post(
        f"/admin/showcase/{ITEM}/poster",
        files={"image": ("reel.mp4", FAKE_MP4, "video/mp4")},
        headers=bearer(ADMIN),
    ).status_code,
    415,
)


# ===========================================================================
print("\n6. Swapping a clip for a picture takes the still with it")
# ===========================================================================
# ⚠ A POSTER BELONGS TO A VIDEO. Left behind, it is a file nobody will ever draw
# and a `has_poster` the panel would offer a Remove button for.
r = client.post(
    f"/admin/showcase/{ITEM}/media",
    files={"media": ("wide.png", png_bytes(1600, 900), "image/png")},
    headers=bearer(ADMIN),
)
check("the picture replaces the clip", r.json().get("kind"), "image")
check("and the still is gone with it", r.json().get("has_poster"), False)
# ⚠ MEASURED, NOT TYPED. Pillow was already holding the image, so the shape on
# the row is the real one — the dropdown's default would have cropped a
# landscape still into whatever somebody left it on.
check("the shape was measured off the picture", r.json().get("aspect"), "16:9")

r = client.post(
    f"/admin/showcase/{ITEM}/media",
    files={"media": ("tall.png", png_bytes(540, 960), "image/png")},
    headers=bearer(ADMIN),
)
check("a portrait picture measures 9:16", r.json().get("aspect"), "9:16")

# The old files must not still be on disk under the new row.
stored = sorted(os.listdir(os.environ["API_SHOWCASE_DIR"]))
check("only the live file is left on disk", len(stored), 1)


# ===========================================================================
print("\n7. Hiding, ordering and deleting")
# ===========================================================================
second = client.post(
    "/admin/showcase", json={"title": "Second", "rank": 1}, headers=bearer(ADMIN)
).json()
client.post(
    f"/admin/showcase/{second['id']}/media",
    files={"media": ("b.png", png_bytes(), "image/png")},
    headers=bearer(ADMIN),
)
titles = [i["title"] for i in client.get("/public/showcase").json()["items"]]
check("rank orders the wall", titles, ["Chai break", "Second"])

client.patch(f"/admin/showcase/{ITEM}", json={"rank": 5}, headers=bearer(ADMIN))
titles = [i["title"] for i in client.get("/public/showcase").json()["items"]]
check("…and reordering it reorders the page", titles, ["Second", "Chai break"])

client.patch(f"/admin/showcase/{ITEM}", json={"active": False}, headers=bearer(ADMIN))
titles = [i["title"] for i in client.get("/public/showcase").json()["items"]]
check("hiding takes it off the page", titles, ["Second"])
check(
    "but the panel still has it",
    len(client.get("/admin/showcase", headers=bearer(ADMIN)).json()["items"]),
    2,
)

check(
    "DELETE is accepted",
    client.delete(f"/admin/showcase/{ITEM}", headers=bearer(ADMIN)).status_code,
    200,
)
check(
    "and the file went with the row",
    len(os.listdir(os.environ["API_SHOWCASE_DIR"])),
    1,
)
check(
    "deleting it twice is a 404, not a crash",
    client.delete(f"/admin/showcase/{ITEM}", headers=bearer(ADMIN)).status_code,
    404,
)


# ===========================================================================
print("\n8. A dead store answers 'no wall', never a 500")
# ===========================================================================
# ⚠ THE PAGE EVERY PROSPECT LANDS ON. A marketing page missing its gallery is a
# bad afternoon; a marketing page answering 500 is a lost customer.
broken = os.environ["API_LOCAL_SHOWCASE_PATH"]
with open(broken, "w", encoding="utf-8") as fh:
    fh.write("{ this is not json")
showcase_mod._bump()
r = client.get("/public/showcase")
check("an unreadable store still answers 200", r.status_code, 200)
check("with nothing on the wall", r.json(), {"items": []})


# ===========================================================================
print("\n9. No signed-in screen can reach Explore any more")
# ===========================================================================
# ⚠ THE ASK, IN ITS OWN WORDS: *"any logged in user must not see explore which is
# happening right now"*. Every route check above would still pass with the rail
# row back in place — this is the assertion that survives the next component.
sidebar = source("components", "Sidebar.jsx")
check(
    'the rail has no onNavigate("explore")',
    'onNavigate("explore")' not in sidebar,
)
app_jsx = source("App.jsx")
# ⚠ THE BRANCH, NOT THE STRING. `nav === "explore"` is still in this file once,
# on purpose: it is the line that turns a left-over nav into Home. What must be
# gone is the CHAIN ENTRY that rendered the page inside the shell.
check(
    "the shell has no explore branch",
    'else if (nav === "explore")' not in app_jsx
    and 'else if (page === "explore")' not in app_jsx,
)
check(
    "and a left-over nav lands on Home instead of nothing",
    'const page = nav === "explore" ? "home" : nav;' in app_jsx,
)
check('the front door is Home', 'const LANDING_NAV = "home";' in app_jsx)
# The other half of the same rule: Explore has to be RENDERED on the logged-out
# side. A file nothing imports would pass every grep above by accident.
check(
    "Explore is rendered before the auth gate",
    app_jsx.index("<Explore") < app_jsx.index("---- Main content by nav ----"),
)


# ===========================================================================
print("\n10. Everything on the public page is a sign-in gate")
# ===========================================================================
# ⚠ *"if anyone clicks anywhere to use and create any workflow we must give a
# user first to login and then use"*. The failure this guards is subtle: a card
# or a tile wired straight to `onNavigate` would LOOK right on the page and do
# nothing at all, because a logged-out shell has nowhere to navigate to.
explore = source("components", "Explore.jsx")
check("Explore takes no onNavigate prop", "onNavigate" not in explore)
check("it asks for a sign-in instead", "onSignIn" in explore)
check(
    "and it reads the public workflow list, not an account's entitlements",
    "useLiveWorkflows" in explore and "workflowsKnown" not in explore,
)
check(
    "it reads the curated wall, not the customer's own work",
    "publicShowcase" in explore and "useDashboard" not in explore,
)
# ⚠ AND THE CLICK PLAYS THE THING. *"the videos or images should be clickable and
# be able to use it properly play"*.
check("a card opens the viewer", "MediaLightbox" in explore)
viewer = source("components", "MediaLightbox.jsx")
check("the viewer has a real player", "<video" in viewer)
check("with controls on it", re.search(r"\bcontrols\b", viewer) is not None)

# The workflow somebody clicked has to survive the sign-in, or the gate costs
# them the thing they came for.
check("the shell remembers what they clicked", "pendingWorkflow" in app_jsx)
check(
    "and only a real workflow id is carried through it",
    "asWorkflow(workflowId)" in app_jsx,
)


# ===========================================================================
print("\n11. A real clip brings its own thumbnail")
# ===========================================================================
# ⚠ THE BUG THIS SECTION EXISTS FOR: *"when i upload video from admin panel but
# when i see explore page so no thumbnail show in my upload video."* A video's
# still used to be a SECOND upload nobody knew to make, on the stated grounds
# that `imageio-ffmpeg` ships no `ffprobe`. It ships ffmpeg, and ffmpeg is what
# extracts frames.
#
# ⚠ THESE ARE REAL CLIPS, BUILT BY ffmpeg ITSELF, not fixtures. A fake MP4 would
# prove only that the code runs; the questions worth asking here - does a black
# opening get refused, is a portrait clip measured rather than assumed - cannot
# be asked of bytes that do not decode.
import subprocess  # noqa: E402


def _ffmpeg():
    try:
        from animatic import ffmpeg_exe

        return ffmpeg_exe()
    except Exception:  # noqa: BLE001 - no ffmpeg is a skip, not a failure
        return ""


FF = _ffmpeg()


def clip(name, lavfi, seconds=4):
    """A real, decodable MP4 built by ffmpeg from a synthetic source."""
    path = os.path.join(_TMP, name)
    subprocess.run(
        [FF, "-v", "error", "-f", "lavfi", "-i", f"{lavfi}:duration={seconds}",
         "-pix_fmt", "yuv420p", "-y", path],
        check=True,
    )
    with open(path, "rb") as fh:
        return fh.read()


def wall_item(title, blob, name="reel.mp4"):
    """A fresh item with a clip on it. Returns its public card."""
    made = client.post(
        "/admin/showcase", json={"title": title}, headers=bearer(ADMIN)
    ).json()
    client.post(
        f"/admin/showcase/{made['id']}/media",
        files={"media": (name, blob, "video/mp4")},
        headers=bearer(ADMIN),
    )
    rows = client.get("/admin/showcase", headers=bearer(ADMIN)).json()["items"]
    return made["id"], [row for row in rows if row["id"] == made["id"]][0]


if not FF:
    print("  SKIPPED - no ffmpeg on this machine (the grab fails soft by design)")
else:
    item_id, row = wall_item("Colour reel", clip("ok.mp4", "testsrc=size=640x360:rate=12"))
    check("uploading a clip leaves a still behind it", row.get("has_poster"), True)
    card = [c for c in client.get("/public/showcase").json()["items"] if c["id"] == item_id][0]
    check("and the public card carries its address", bool(card.get("poster_url")), True)
    grabbed = client.get(card["poster_url"])
    check("the grabbed still is public", grabbed.status_code, 200)
    check("and is a WEBP like every other picture here", grabbed.headers.get("content-type"), "image/webp")

    # ⚠ THE RATIO IS MEASURED OFF THE FRAME NOW, not taken from the dropdown.
    # A portrait phone clip left on the 16:9 default used to be cropped hard.
    _, portrait = wall_item("Portrait reel", clip("tall.mp4", "testsrc=size=360x640:rate=12"))
    check("a portrait clip is measured, not assumed", portrait.get("aspect"), "9:16")

    # ⚠ A BLACK FRAME IS REFUSED RATHER THAN SHIPPED. This is the one piece of
    # the original "ask for a still instead" reasoning that was right: films open
    # on black, and a wall of black rectangles is worse than a wall of glyphs.
    _, dark = wall_item("Black reel", clip("dark.mp4", "color=c=black:size=640x360:rate=12"))
    check("an all-black clip gets NO still", dark.get("has_poster"), False)
    check("but the clip itself still uploaded fine", dark.get("has_media"), True)

    # ⚠ A HAND-PICKED STILL OUTRANKS THE GRAB AND SURVIVES A RE-UPLOAD. The
    # frame that sells a film is rarely the one it opens on.
    chosen_id, _ = wall_item("Chosen still", clip("a.mp4", "testsrc=size=640x360:rate=12"))
    client.post(
        f"/admin/showcase/{chosen_id}/poster",
        files={"image": ("still.png", png_bytes(320, 180), "image/png")},
        headers=bearer(ADMIN),
    )
    mine = client.get(f"/public/showcase").json()
    before_url = [c for c in mine["items"] if c["id"] == chosen_id][0]["poster_url"]
    client.post(
        f"/admin/showcase/{chosen_id}/media",
        files={"media": ("b.mp4", clip("b.mp4", "testsrc=size=640x360:rate=12"), "video/mp4")},
        headers=bearer(ADMIN),
    )
    after = client.get("/public/showcase").json()
    after_url = [c for c in after["items"] if c["id"] == chosen_id][0]["poster_url"]
    check("re-uploading the clip does not overwrite a chosen still", after_url, before_url)

# ⚠ AND A CLIP THAT CANNOT BE DECODED COSTS THE UPLOAD NOTHING. The grab is a
# nicety; losing somebody's file over it would not be. `FAKE_MP4` is not a video.
broken_id, broken = wall_item("Broken reel", FAKE_MP4) if FF else (None, None)
if FF:
    check("undecodable bytes still upload", broken.get("has_media"), True)
    check("they just arrive without a still", broken.get("has_poster"), False)


# ===========================================================================
print("\n12. Explore has an address of its own")
# ===========================================================================
# ⚠ THE PAGE WHOSE JOB IS TO BE SHOWN TO PEOPLE HAD NO WAY OF BEING SHOWN. Until
# `?explore` it was internal state, reachable only by landing on `/`, reading the
# sales page and finding "See the work" - so a link to the marketing page could
# not be sent to anybody, and the VS Code task called MARKETING PAGE opened on
# the sales page instead.
app_src = source("App.jsx")
check("there is an explore parameter", 'EXPLORE_PARAM = "explore"' in app_src)
check("it is read at boot", "readExploreRoute()" in app_src)
check(
    "and it decides which public screen opens",
    re.search(r"useState\(\s*\(\)\s*=>\s*\n?\s*readExploreRoute\(\)", app_src) is not None,
)
# ⚠ `?admin` WINS. One is an address somebody was sent to work at.
check(
    "admin outranks it in one URL",
    re.search(r"has\(EXPLORE_PARAM\)\s*&&\s*!q\.has\(ADMIN_PARAM\)", app_src) is not None,
)
# ⚠ AND IT IS DROPPED ON SIGN-IN, because a signed-in customer never sees
# Explore again - the standing decision `LANDING_NAV` exists for.
check("signing in drops it", "syncExploreUrl(!authed" in app_src)
check(
    "and the effect watches BOTH, or the address would go stale",
    re.search(r"syncExploreUrl\(!authed[\s\S]{0,120}\[authed, authView\]", app_src) is not None,
)
# One helper writes the URL for both routes; two copies of `replaceState` is two
# places to get the no-router rule wrong.
check("both routes share one URL writer", "function syncUrlFlag(" in app_src)


# ===========================================================================
print("\n13. The public page has a rail, and it is a sign-in gate like everything else")
# ===========================================================================
# ⚠ ASKED FOR WITH THE KLING AI PAGE BESIDE IT: *"need the side bar explore page
# like this but with my own services, and when user click on any of this he must
# get sign in page and after sign in open the usual flow."* The rail is the fifth
# thing on this page that has to be a gate rather than a link — tiles, banners,
# cards, the viewer's button, and now this — and every one of them is one line
# away from quietly navigating instead.
explore_src = source("components", "Explore.jsx")
check("Explore draws a rail", 'className="xp-rail"' in explore_src)
# It reads the SAME live list the tiles do. A second copy is how a switched-off
# workflow comes back on one of the two — the bug `dashboard_feed.js` was
# extracted to prevent.
check(
    "the rail reads the live workflow list, not its own copy",
    re.search(r'xp-rail-list[\s\S]{0,200}\{live\.map\(', explore_src) is not None,
)
check(
    "every row asks for a sign-in",
    re.search(r'xp-rail-item[\s\S]{0,220}onSignIn\?\.\(', explore_src) is not None,
)
# ⚠ AND IT CARRIES THE WORKFLOW ID, which is what makes "after sign in open the
# usual flow" true rather than a hope: `pendingWorkflow` lands them in it.
check(
    "and carries which workflow it was",
    re.search(r'xp-rail-item[\s\S]{0,220}onSignIn\?\.\(w\.status', explore_src) is not None,
)
check("the rail is the one below the sign-in button", "xp-rail-cta" in explore_src)
# RULEBOOK E17/E18: a rail is read by glancing, and a clipped name is the bug.
check("rows use the short name the app rail uses", "shortLabel(w.id, w.title)" in explore_src)
check("a switched-off workflow still shows, wearing its badge", "xp-rail-soon" in explore_src)

rail_css = source("styles", "explore.css")
check("the rail has styles", ".xp-rail {" in rail_css)
# ⚠ THE PAGE IS MOVED OFF A FIXED RAIL, not wrapped in a new layout — every row
# on this page is a direct child of `.explore-public` and is positioned by name.
check("the page makes room for it", ".explore-public.xp-has-rail" in rail_css)
# Standing responsive rule: nothing with a fixed width may trap a phone.
check(
    "and it gets out of the way on a narrow screen",
    re.search(r'@media \(max-width: 1000px\)[\s\S]{0,220}\.xp-rail\s*\{\s*display:\s*none',
              rail_css) is not None,
)


# ===========================================================================
print("\n14. Explore is ONE tab in the admin panel, with two sections inside it")
# ===========================================================================
# ⚠ THEY WERE TWO TOP-LEVEL TABS FOR ONE SCREEN. *"admin panel banners and
# showcase both section same work kar raha hai explore page ke liye — so tum ek
# explore ka hi banao aur uske under banner and showcase rakho."* The old tab
# strip had already half-admitted it, with a comment reading *"they are the SAME
# PAGE … two tabs apart is two tabs too far"*.
panel = source("admin", "AdminPanel.jsx")
check("there is an Explore tab", 'id: "explore"' in panel)
check("and Banners is no longer one", 'id: "banners"' not in panel)
check("and neither is Showcase", 'id: "showcase"' not in panel)
check("the panel mounts the new parent", "<AdminExplore />" in panel)
# ⚠ THE OTHER TABS MUST ALL STILL BE THERE. This file's own history: a patch
# aimed at these two lines took the first 39 lines of this component with it.
for _tab in ("overview", "users", "features", "pricing", "sales", "brand", "activity"):
    check(f"the {_tab} tab survived the merge", f'id: "{_tab}"' in panel)

merged = source("admin", "AdminExplore.jsx")
check("the parent mounts both halves", "<AdminBanners />" in merged and "<AdminShowcase />" in merged)
# ⚠ THE SAME SEGMENTED CONTROL Activity, Sales and Features use. A second strip
# that merely RESEMBLED the ones already on this screen is the mismatch this
# repo keeps paying for.
check("it reuses the panel's own segmented control", "admin-seg-btn" in merged)
# ⚠ ONE AT A TIME, NOT BOTH HIDDEN WITH CSS: each child fetches on mount and
# holds an open create form, and a half-typed banner surviving a trip to
# Showcase and back is state nobody expects.
check(
    "and mounts one at a time",
    re.search(r'section === "banners" \? <AdminBanners /> : <AdminShowcase />', merged) is not None,
)
# Neither child was rewritten to get here — that is the point of the change.
check("AdminBanners still stands alone", "export default function AdminBanners" in source("admin", "AdminBanners.jsx"))
check("AdminShowcase still stands alone", "export default function AdminShowcase" in source("admin", "AdminShowcase.jsx"))


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures else "All showcase checks passed."))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if failures else 0)

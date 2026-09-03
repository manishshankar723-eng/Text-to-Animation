"""THE APP'S NAME AND MARK ARE AN ADMIN FIELD, AND ONE SAVE HAS TO LAND EVERYWHERE.

Why this exists, in one sentence: the product name was typed into eight
components and the mark was drawn in two more, so renaming the app was a code
change — and the single thing the owner asked for was that changing it ONCE
changes it in every place it appears.

    tests/admin_check.py         pins the routes and the roles
    tests/brand_landing_check.py watches the SHOP WINDOW in a real browser
    THIS FILE                    pins the store, the two routes, and the rule
                                 that no screen keeps its own copy of the name

⚠ **TWO LOGO SLOTS, ONE PER THEME.** A logo is a flat picture and does not
re-colour itself the way the drawn mark does, so the first white wordmark
uploaded here vanished into the light theme — *"jab mai light mode mai karta hun
to mera logo white mai merge ho raha hai."* Section 5 pins the pair and, more
importantly, pins the FALLBACK: one upload must still cover both themes, or the
simple case becomes a two-step chore.

⚠ **THE LAST CHECK IS A SOURCE GREP, AND IT IS THE POINT OF THE FILE.** Every
other check here would still pass if somebody added a ninth screen with the name
typed into it — the store would be right and the new screen would be wrong. So
the last section reads the JSX and fails if the brand string is hard-coded
anywhere a customer can see it. That is the only assertion that survives the next
component being written.

⚠ **IT TOUCHES NOTHING REAL.** Every store is pointed at a fresh temporary
directory BEFORE `server.config` is imported — local JSON for accounts, events
and branding, an in-memory job store, and an uploads directory of its own. No
MongoDB, no network, no AI quota, no browser.

    python tests/branding_check.py
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
_TMP = tempfile.mkdtemp(prefix="branding_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_LOCAL_BRANDING_PATH"] = os.path.join(_TMP, "branding.json")
os.environ["API_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["API_BRANDING_DIR"] = os.path.join(_TMP, "uploads", "_branding")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "branding-check-not-a-real-secret"
os.environ["ADMIN_EMAILS"] = "boss@example.com"

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import branding as branding_mod  # noqa: E402
from server import events as events_mod  # noqa: E402
from server.main import app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "client", "src")
BUILT_IN = branding_mod.DEFAULT_NAME

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


# ===========================================================================
print("\n1. The public read — no token, no account")
# ===========================================================================
# ⚠ THE WHOLE REASON THE ROUTE IS PUBLIC. The sign-in card and the landing page
# print the name, and neither has a session. A branding call that needed one
# would leave the first screen anybody sees wearing the built-in name forever.
r = client.get("/public/branding")
check("GET /public/branding needs no token", r.status_code, 200)
body = r.json() if r.status_code == 200 else {}
check("it answers with the built-in name", body.get("name"), BUILT_IN)
check("and no logo, so the app draws its own mark", body.get("logo_url"), "")
check("no light-mode logo either", body.get("logo_url_light"), "")
check("it ships in the built-in colours", body.get("theme_id"), "gold")
# ⚠ THE COLOURS ARE PUBLIC FOR THE SAME REASON THE NAME IS. The landing page,
# the sign-in card and a shared storyboard link are all painted before anybody
# has an account; a visitor who sees the built-in gold for a beat and then the
# customer's green has watched the app change colour under them.
#
# ⚠ AND THIS LIST IS A FENCE, NOT A FORMALITY. The read is unauthenticated, so
# every field added here is a field shown to the whole internet. A new one is
# meant to fail this line and be argued for, not slip in.
check(
    "the payload is a name, two marks and a palette — nothing else",
    sorted(body),
    ["accent", "ground", "logo_url", "logo_url_light", "name", "stamp",
     "stamp_light", "theme_id"],
)


# ===========================================================================
print("\n2. The write is admin-only, and invisible to everybody else")
# ===========================================================================
# 404, not 403 — same rule as every other panel route: an ordinary account must
# not even be able to confirm the screen exists.
check("a user gets 404 from GET /admin/branding", client.get("/admin/branding", headers=bearer(USER)).status_code, 404)
check(
    "a user gets 404 from PATCH /admin/branding",
    client.patch("/admin/branding", json={"name": "Hacked"}, headers=bearer(USER)).status_code,
    404,
)
check(
    "a user gets 404 from POST /admin/branding/logo/dark",
    client.post(
        "/admin/branding/logo/dark",
        files={"image": ("l.png", png_bytes(), "image/png")},
        headers=bearer(USER),
    ).status_code,
    404,
)
check("an anonymous visitor gets 401 from GET /admin/branding", client.get("/admin/branding").status_code, 401)
check("the app is still called what it was", client.get("/public/branding").json()["name"], BUILT_IN)


# ===========================================================================
print("\n3. Renaming the app")
# ===========================================================================
r = client.patch("/admin/branding", json={"name": "Acme Story Studio"}, headers=bearer(ADMIN))
check("PATCH /admin/branding is accepted", r.status_code, 200)
check("the panel is told the new name", r.json().get("name"), "Acme Story Studio")
check("and the PUBLIC read changes with it", client.get("/public/branding").json()["name"], "Acme Story Studio")

# ⚠ COLLAPSED, NOT JUST TRIMMED. The field is a textarea and a pasted name can
# carry a newline in the middle of it — invisible in the panel, and an invisible
# character inside `.sb-brand-name` (which is `white-space: nowrap`) silently
# widens the sidebar row.
r = client.patch("/admin/branding", json={"name": "  Acme   \n Studio  "}, headers=bearer(ADMIN))
check("whitespace inside a name is collapsed", r.json().get("name"), "Acme Studio")

# The sidebar ellipsises, so a name too long to fit would be trimmed SILENTLY.
long_name = "N" * 200
r = client.patch("/admin/branding", json={"name": long_name}, headers=bearer(ADMIN))
check("a very long name is capped, not stored whole", len(r.json().get("name", "")), branding_mod.NAME_MAX_CHARS)

# An empty box is "put it back", not "the app has no name".
r = client.patch("/admin/branding", json={"name": "   "}, headers=bearer(ADMIN))
check("an empty name falls back to the built-in one", r.json().get("name"), BUILT_IN)

check(
    "an empty PATCH is refused rather than silently doing nothing",
    client.patch("/admin/branding", json={}, headers=bearer(ADMIN)).status_code,
    400,
)

client.patch("/admin/branding", json={"name": "Acme Story Studio"}, headers=bearer(ADMIN))


# ===========================================================================
print("\n3b. Repainting the app")
# ===========================================================================
# ⚠ THE SERVER STORES TWO HEX STRINGS AND A LABEL, AND KNOWS NOTHING ELSE ABOUT
# COLOUR. Every token the app actually paints with is derived in
# `client/src/palette.js`, and `tests/palette_check.py` runs THAT module under
# node and measures the contrast of what comes out. Splitting it this way is the
# point: two copies of a colour derivation would be a rule written twice, and the
# Python one would be the copy nobody updated.
r = client.patch(
    "/admin/branding",
    json={"theme_id": "emerald", "accent": "#34D399", "ground": "#101815"},
    headers=bearer(ADMIN),
)
check("PATCH with colours is accepted", r.status_code, 200)
check("the panel is told the new accent", r.json().get("accent"), "#34d399")
pub = client.get("/public/branding").json()
check("and the PUBLIC read is repainted too", pub.get("accent"), "#34d399")
check("the ground travels with it", pub.get("ground"), "#101815")
check("and so does which card to ring", pub.get("theme_id"), "emerald")

# ⚠ SAVING A COLOUR MUST NOT BLANK A NAME. Both live on the one document, and
# `exclude_unset` is the whole reason a PATCH carrying only colours leaves the
# name alone. Without it this screen would rename the app to the built-in every
# time somebody tried a palette.
check("repainting left the name alone", pub.get("name"), "Acme Story Studio")

# The panel is a colour picker and a text box beside it, so the box holds half a
# colour for a keypress or two. `clean_hex` coerces rather than refusing — a 422
# here would mean an administrator unable to save their LOGO because of a colour
# field they never touched.
r = client.patch("/admin/branding", json={"accent": "#FFF"}, headers=bearer(ADMIN))
check("shorthand hex is expanded, not rejected", r.json().get("accent"), "#ffffff")
r = client.patch("/admin/branding", json={"accent": "red"}, headers=bearer(ADMIN))
check("a colour that is not a colour falls back to the built-in accent",
      r.json().get("accent"), branding_mod.DEFAULT_ACCENT)
r = client.patch("/admin/branding", json={"theme_id": "../../etc/passwd"}, headers=bearer(ADMIN))
check("a theme id that is not a slug falls back too",
      r.json().get("theme_id"), branding_mod.DEFAULT_THEME_ID)

# ⚠ THE PANEL NEEDS TO KNOW WHAT "PUT IT BACK" MEANS, the same way it does for
# the name — otherwise "Back to built-in" is an administrator guessing hexes.
admin_row = client.get("/admin/branding", headers=bearer(ADMIN)).json()
check("the panel is told the shipped palette",
      admin_row.get("default_theme", {}).get("accent"), branding_mod.DEFAULT_ACCENT)

# ⚠ A DOCUMENT WRITTEN BEFORE COLOURS EXISTED HAS NONE, and every reader of the
# row must still get values it can paint with. This is the upgrade path, and it
# is the one thing here that cannot be checked through the API — the API would
# have written the defaults in on the way past.
check("a row with no colours in it still reads as the built-in",
      branding_mod.clean_hex(None, branding_mod.DEFAULT_ACCENT),
      branding_mod.DEFAULT_ACCENT)

client.patch(
    "/admin/branding",
    json={"theme_id": branding_mod.DEFAULT_THEME_ID,
          "accent": branding_mod.DEFAULT_ACCENT,
          "ground": branding_mod.DEFAULT_GROUND},
    headers=bearer(ADMIN),
)


# ===========================================================================
print("\n4. Uploading a logo")
# ===========================================================================
r = client.post(
    "/admin/branding/logo/dark",
    files={"image": ("logo.png", png_bytes(), "image/png")},
    headers=bearer(ADMIN),
)
check("POST /admin/branding/logo/dark is accepted", r.status_code, 200)
row = r.json() if r.status_code == 200 else {}
check("the panel is told there is now a logo", row.get("has_logo"), True)
stamp_one = row.get("stamp") or ""
check("and given a stamp for it", bool(re.fullmatch(r"[a-f0-9]{12}", stamp_one)), True)
check("the dark slot owns it", row.get("logos", {}).get("dark", {}).get("own"), True)
# ⚠ ONE UPLOAD IS A COMPLETE ANSWER. The commonest logo is a full-colour mark
# that reads on both grounds, and making somebody upload the same file twice to
# get started would be a worse product than the one-slot version this replaces.
check("the light slot does NOT own one", row.get("logos", {}).get("light", {}).get("own"), False)

pub = client.get("/public/branding").json()
check("the public read carries the logo's address", pub.get("logo_url"), f"/public/branding/logo/{stamp_one}")
check("and the light theme borrows the same file", pub.get("logo_url_light"), pub.get("logo_url"))

img = client.get(pub["logo_url"])
check("that address serves the file, to anybody, with no token", img.status_code, 200)
check("as a PNG", img.headers.get("content-type"), "image/png")
# ⚠ THE URL CARRIES THE FILE'S ID, SO IT CAN BE KEPT FOREVER. That is what makes
# a NEW upload appear instantly everywhere: a new file is a new address, and no
# cache anywhere is holding the old bytes under it.
check("and told to cache it forever", "immutable" in (img.headers.get("cache-control") or ""), True)
check("the alpha channel survived", Image.open(io.BytesIO(img.content)).mode, "RGBA")

# The person uploading has a print master and no way to resize it; refusing it
# would stop them branding the app at all.
r = client.post(
    "/admin/branding/logo/dark",
    files={"image": ("big.png", png_bytes(2000, 1000), "image/png")},
    headers=bearer(ADMIN),
)
check("an oversized logo is accepted", r.status_code, 200)
stamp_two = r.json().get("stamp")
served = client.get(f"/public/branding/logo/{stamp_two}")
big = Image.open(io.BytesIO(served.content))
check("scaled down to the cap", max(big.size), branding_mod.LOGO_MAX_PX)
check("with its shape kept", big.size[0] // big.size[1], 2)

check("replacing the logo changes the stamp", stamp_two != stamp_one, True)
check("so the two addresses are different", bool(stamp_two), True)
# ⚠ A STALE ADDRESS STILL SERVES, BUT IS NOT KEPT. A tab that was open across the
# change must not show a broken image; it also must not pin today's bytes under
# yesterday's URL, or a hard refresh would bring the old logo back.
old = client.get(f"/public/branding/logo/{stamp_one}")
check("an old stamp still serves the live logo", old.status_code, 200)
check("but is told not to cache it", old.headers.get("cache-control"), "no-store")
check(
    "and the superseded file is gone from disk",
    os.path.isfile(branding_mod.logo_path(stamp_one)),
    False,
)

check(
    "a PDF is refused",
    client.post(
        "/admin/branding/logo/dark",
        files={"image": ("l.pdf", b"%PDF-1.4", "application/pdf")},
        headers=bearer(ADMIN),
    ).status_code,
    415,
)
check(
    "and so is a PNG that is not one",
    client.post(
        "/admin/branding/logo/dark",
        files={"image": ("l.png", b"not an image at all", "image/png")},
        headers=bearer(ADMIN),
    ).status_code,
    400,
)
check(
    "an invented slot is a 404",
    client.post(
        "/admin/branding/logo/sepia",
        files={"image": ("l.png", png_bytes(), "image/png")},
        headers=bearer(ADMIN),
    ).status_code,
    404,
)
check("a refused upload left the good logo alone", client.get("/public/branding").json()["stamp"], stamp_two)

check("a nonsense stamp is a 404, not a file read", client.get("/public/branding/logo/../../etc").status_code, 404)


# ===========================================================================
print("\n5. A SECOND LOGO FOR THE LIGHT THEME")
# ===========================================================================
# ⚠ THE FAULT THIS SECTION EXISTS FOR. A logo is a flat picture: the drawn mark
# is painted in `currentColor` and re-colours itself for free, an uploaded white
# wordmark does not — and it disappeared into the light theme completely.
r = client.post(
    "/admin/branding/logo/light",
    files={"image": ("black.png", png_bytes(colour=(0, 0, 0, 255)), "image/png")},
    headers=bearer(ADMIN),
)
check("POST /admin/branding/logo/light is accepted", r.status_code, 200)
light_row = r.json() if r.status_code == 200 else {}
stamp_light = light_row.get("stamp_light") or ""
check("the light slot now owns its own file", light_row.get("logos", {}).get("light", {}).get("own"), True)
check("and it is a DIFFERENT file from the dark one", stamp_light != stamp_two, True)
check("the dark slot was not touched", light_row.get("stamp"), stamp_two)

pub = client.get("/public/branding").json()
check("the public read carries both addresses", pub.get("logo_url_light"), f"/public/branding/logo/{stamp_light}")
check("and they are different", pub["logo_url"] != pub["logo_url_light"], True)
check("both files serve", client.get(pub["logo_url"]).status_code, 200)
check("including the light one", client.get(pub["logo_url_light"]).status_code, 200)
check(
    "each is cached forever under its own address",
    "immutable" in (client.get(pub["logo_url_light"]).headers.get("cache-control") or ""),
    True,
)

# ⚠ REMOVING ONE SLOT IS NOT "NO LOGO" — that theme borrows the other, and only
# clearing BOTH brings the drawn mark back. A Remove that silently blanked the
# app would be the worse surprise.
r = client.delete("/admin/branding/logo/light", headers=bearer(ADMIN))
check("DELETE on one slot is accepted", r.status_code, 200)
check("that slot stops owning a file", r.json().get("logos", {}).get("light", {}).get("own"), False)
check("but the theme falls back rather than going bare", r.json().get("logo_url_light"), f"/public/branding/logo/{stamp_two}")
check("and there is still a logo", r.json().get("has_logo"), True)
check("the removed file is gone from disk", os.path.isfile(branding_mod.logo_path(stamp_light)), False)

# The other direction: a LIGHT-only deployment. The dark theme borrows it.
client.delete("/admin/branding/logo/dark", headers=bearer(ADMIN))
r = client.post(
    "/admin/branding/logo/light",
    files={"image": ("only.png", png_bytes(colour=(0, 0, 0, 255)), "image/png")},
    headers=bearer(ADMIN),
)
only = r.json().get("stamp_light")
check("a light-only upload is enough on its own", r.json().get("has_logo"), True)
check("and the DARK theme borrows it", r.json().get("logo_url"), f"/public/branding/logo/{only}")
client.delete("/admin/branding/logo/light", headers=bearer(ADMIN))

# Back to one dark logo for the section below.
r = client.post(
    "/admin/branding/logo/dark",
    files={"image": ("logo.png", png_bytes(), "image/png")},
    headers=bearer(ADMIN),
)
stamp_two = r.json().get("stamp")


# ===========================================================================
print("\n6. Removing both goes back to the built-in mark")
# ===========================================================================
r = client.delete("/admin/branding/logo/dark", headers=bearer(ADMIN))
check("DELETE /admin/branding/logo/dark is accepted", r.status_code, 200)
check("the panel is told there is no logo", r.json().get("has_logo"), False)
check("the public read says so too", client.get("/public/branding").json()["logo_url"], "")
check("and neither does the light theme", client.get("/public/branding").json()["logo_url_light"], "")
check("the old address is now a 404", client.get(f"/public/branding/logo/{stamp_two}").status_code, 404)
check("and the NAME was not touched by removing the mark", client.get("/public/branding").json()["name"], "Acme Story Studio")

# ⚠ THE DOCUMENT AND THE FILE ARE TWO STORES AND THEY CAN DRIFT — a restored
# database, a wiped uploads volume. The failure mode without this is every screen
# in the app drawing a broken-image icon where its mark should be.
client.post(
    "/admin/branding/logo/dark",
    files={"image": ("logo.png", png_bytes(), "image/png")},
    headers=bearer(ADMIN),
)
stamp_three = client.get("/public/branding").json()["stamp"]
os.remove(branding_mod.logo_path(stamp_three))
branding_mod._bump()
check(
    "a logo whose file has vanished reads as no logo at all",
    client.get("/public/branding").json()["logo_url"],
    "",
)


# ===========================================================================
print("\n7. Every change is recorded, with who did it")
# ===========================================================================
rows = events_mod.list_events(limit=200, types=["admin.branding_changed"])
check("branding changes reach the activity feed", len(rows) >= 4, True)
check("each one names the administrator", all(e.get("actor") == "boss@example.com" for e in rows), True)
actions = {e.get("meta", {}).get("action") for e in rows}
check("a rename is recorded as one", "renamed" in actions, True)
check("an upload is recorded as one", "logo_uploaded" in actions, True)
check("a removal is recorded as one", "logo_removed" in actions, True)
# ⚠ WHICH SLOT, NOT JUST "A LOGO CHANGED". Two logos means "the logo was
# replaced" is only half an answer to the question the feed is opened for.
slots = {e.get("meta", {}).get("slot") for e in rows if e.get("meta", {}).get("slot")}
check("and a logo change names the theme it was for", slots, {"dark", "light"})
renames = [e for e in rows if e.get("meta", {}).get("action") == "renamed"]
check(
    "and a rename says what it was before",
    any(e["meta"].get("was") and e["meta"].get("now") for e in renames),
    True,
)


# ===========================================================================
print("\n8. NO SCREEN KEEPS ITS OWN COPY OF THE NAME")
# ===========================================================================
# ⚠ THIS IS THE CHECK THE FILE EXISTS FOR. Everything above would still pass if
# somebody wrote a ninth screen with the name typed into it — the store would be
# right and that screen would be stale forever. So: read the source.
#
# The brand string is allowed in exactly three kinds of place, and each is a
# FALLBACK rather than a display:
#   · `branding.js`  — what the app shows when the server has never answered
#   · `index.html`   — the tab title before any JavaScript has run
#   · a comment      — prose explaining the above
ALLOWED = {"branding.js"}

offenders = []
for folder, _dirs, files in os.walk(SRC):
    for fname in files:
        if not fname.endswith((".jsx", ".js")):
            continue
        if fname in ALLOWED:
            continue
        path = os.path.join(folder, fname)
        for n, line in enumerate(open(path, encoding="utf-8"), 1):
            if BUILT_IN not in line:
                continue
            # Comments are prose about the default, not a printed name.
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            offenders.append(f"{os.path.relpath(path, ROOT)}:{n}")

check(f"no component hard-codes {BUILT_IN!r}", offenders, [])

# And the other half of the same rule: the screens that DO print it must be
# reading the store. A screen that prints nothing at all would pass the grep
# above by accident.
READERS = [
    "components/Sidebar.jsx",
    "components/Login.jsx",
    "components/Landing.jsx",
    "components/PublicStoryboard.jsx",
    "components/Explore.jsx",
    "admin/AdminShell.jsx",
]
for rel in READERS:
    text = open(os.path.join(SRC, *rel.split("/")), encoding="utf-8").read()
    check(f"{rel} reads the brand store", "useBranding" in text, True)

logo = open(os.path.join(SRC, "components", "Logo.jsx"), encoding="utf-8").read()
check("Logo.jsx prefers an uploaded logo over the drawn mark", "useBranding" in logo, True)


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures else "All branding checks passed."))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if failures else 0)

"""Contract checks for the admin panel (Phase 1).

⚠ THIS ONE TOUCHES NOTHING REAL. Every store is pointed at a fresh temporary
directory BEFORE `server.config` is imported — local JSON for accounts, drafts
and events, an in-memory job store — so it needs no MongoDB, no network and no
AI quota. That is deliberately unlike `profile_check.py`, which writes to the
live database and is therefore the one suite nobody dares run.

What it actually guards, in rough order of how much it would hurt to get wrong:

  PRIVILEGE SEPARATION. `role` on a profile is the person's JOB TITLE and is
  self-service through `PATCH /auth/me`; `account_role` is the privilege and is
  not. Those two nearly shipped as one field, which would have made "make me an
  administrator" an ordinary profile edit. The first check in this file is a
  user trying exactly that.

  THE GUARD. Every /admin route must answer 404 — not 403 — to somebody without
  the role, so an ordinary account cannot even confirm the panel exists.

  SELF-TARGETING. An administrator may not disable, demote or delete their own
  account. It is the only thing between a mis-click and a site with no
  administrators left.

  IMMEDIATE REVOCATION. `get_current_user` caches a resolved user for 30s
  against its token. Disabling an account has to drop that cache, or the lock
  goes on being unlocked for half a minute — which is precisely the half minute
  that matters.

  THE EVENT LOG. Registering, signing in and failing to sign in must each leave
  a row, an admin action must record WHO did it as well as who it happened to,
  and a listing must never carry a password hash.

    python tests/admin_check.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Point every store at a temporary directory BEFORE anything imports config.
# `load_dotenv()` does not override variables that are already set, so these
# beat whatever is in the developer's .env.
# ---------------------------------------------------------------------------
_TMP = tempfile.mkdtemp(prefix="admin_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "admin-check-not-a-real-secret"
# The bootstrap floor, exercised at the end. A pinned address is an admin
# whatever its document says, and its role cannot be changed from the panel.
os.environ["ADMIN_EMAILS"] = "pinned@example.com"

from fastapi.testclient import TestClient  # noqa: E402

from server import events as events_mod, users as users_mod  # noqa: E402
from server.main import app  # noqa: E402

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)


def register(email, password="password123"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    return r


def login(email, password="password123"):
    return client.post("/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def types_for(email):
    """Every event type recorded against one address, newest first."""
    return [e["type"] for e in events_mod.list_events(50, email=email)]


# ===========================================================================
print("\n--- accounts ---")
# ===========================================================================
r = register("boss@example.com")
check("register returns 201", r.status_code, 201)
boss = r.json()["access_token"]

r = register("cust@example.com")
cust = r.json()["access_token"]
register("other@example.com")

# The role is granted the way `seed_admin.py --role admin` grants it.
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)
check("set_role makes an admin", users_mod.is_admin("boss@example.com"))
check("an ordinary account is not", users_mod.is_admin("cust@example.com"), False)


# ===========================================================================
print("\n--- privilege separation: the job title is not the role ---")
# ===========================================================================
# THE CHECK THIS FILE EXISTS FOR. `role` is in PROFILE_FIELDS and a user may set
# it to anything they like — it is what they do for a living. If the privilege
# had been stored under the same name, this request would be a promotion.
r = client.patch("/auth/me", json={"role": "admin"}, headers=bearer(cust))
check("PATCH /auth/me role=admin succeeds (it is a job title)", r.status_code, 200)
check("…and is echoed back as the job title", r.json()["role"], "admin")
check("…and does NOT make them an admin", users_mod.is_admin("cust@example.com"), False)
check(
    "…and account_role still says user",
    r.json()["account_role"],
    "user",
)
r = client.get("/admin/users", headers=bearer(cust))
check("…and /admin is still closed to them", r.status_code, 404)

# The privilege field is not in the profile allow-list at all.
check(
    "account_role is not a self-editable profile field",
    users_mod.ROLE_FIELD in users_mod.PROFILE_FIELDS,
    False,
)
client.patch("/auth/me", json={"account_role": "admin"}, headers=bearer(cust))
check(
    "…so PATCHing it directly is dropped",
    users_mod.is_admin("cust@example.com"),
    False,
)


# ===========================================================================
print("\n--- the guard: 404 to everyone else, on every route ---")
# ===========================================================================
ROUTES = [
    ("GET", "/admin/overview"),
    ("GET", "/admin/users"),
    ("GET", "/admin/users/cust@example.com"),
    ("GET", "/admin/events"),
    ("GET", "/admin/meta"),
    ("POST", "/admin/users/other@example.com/disabled"),
    ("POST", "/admin/users/other@example.com/role"),
    ("POST", "/admin/users/other@example.com/note"),
    ("DELETE", "/admin/users/other@example.com"),
]
for method, path in ROUTES:
    r = client.request(method, path, headers=bearer(cust), json={})
    # ⚠ 404, NOT 403. A 403 confirms the panel exists and that the caller merely
    # lacks the role, which is a map of the site handed to any account.
    check(f"non-admin {method} {path} → 404", r.status_code, 404)

r = client.get("/admin/overview")
check("no token at all → 401/403", client.get("/admin/overview").status_code in (401, 403))


# ===========================================================================
print("\n--- the panel reads ---")
# ===========================================================================
r = client.get("/admin/overview", headers=bearer(boss))
check("admin can read the overview", r.status_code, 200)
ov = r.json()
# boss, cust, other. `pinned@example.com` is registered further down.
check("overview counts every account", ov["users_total"], 3)
check("overview counts the admins", ov["users_admin"], 1)
check("signups today includes them all", ov["signups_today"], ov["users_total"])
check("the signup chart is 30 days long", len(ov["signups_daily"]), 30)
check("…and zero-filled (no gaps)", all("day" in p and "count" in p for p in ov["signups_daily"]))
check("the stores are reported honestly", ov["stores"]["users"], "local")

r = client.get("/admin/users?limit=10", headers=bearer(boss))
check("admin can list users", r.status_code, 200)
listing = r.json()
check("the listing paginates against a total", listing["total"], ov["users_total"])
blob = r.text
check("no password hash reaches the client", "password_hash" in blob, False)
check("no saved api keys reach the client", "api_keys" in blob, False)

r = client.get("/admin/users?search=cust", headers=bearer(boss))
check("search narrows the list", [u["email"] for u in r.json()["users"]], ["cust@example.com"])
r = client.get("/admin/users?search=.*", headers=bearer(boss))
# ⚠ A REGEX METACHARACTER IS TEXT, NOT A PATTERN. Unescaped, `.*` would match
# every account — a search box that quietly returns the whole table.
check("a regex in the search box is treated as literal text", r.json()["total"], 0)

r = client.get("/admin/users/cust@example.com", headers=bearer(boss))
check("admin can open one account", r.status_code, 200)
detail = r.json()
check("…and it is not flagged as their own", detail["is_self"], False)
check("…and reports the job title separately", detail["role_title"], "admin")
check("…while the privilege stays 'user'", detail["user"]["account_role"], "user")

r = client.get("/admin/users/boss@example.com", headers=bearer(boss))
check("an admin may READ their own row", r.status_code, 200)
check("…and it is flagged as their own", r.json()["is_self"], True)


# ===========================================================================
print("\n--- self-targeting is refused ---")
# ===========================================================================
r = client.post("/admin/users/boss@example.com/disabled", json={"disabled": True}, headers=bearer(boss))
check("an admin cannot disable themselves", r.status_code, 400)
r = client.post("/admin/users/boss@example.com/role", json={"account_role": "user"}, headers=bearer(boss))
check("an admin cannot demote themselves", r.status_code, 400)
r = client.delete("/admin/users/boss@example.com", headers=bearer(boss))
check("an admin cannot delete themselves", r.status_code, 400)
check("…and is still an admin afterwards", users_mod.is_admin("boss@example.com"))


# ===========================================================================
print("\n--- disabling takes effect IMMEDIATELY, not in 30 seconds ---")
# ===========================================================================
# Warm the resolved-user cache: this is the read that would otherwise keep
# serving the account after it is locked.
check("the account works before", client.get("/auth/me", headers=bearer(cust)).status_code, 200)

r = client.post(
    "/admin/users/cust@example.com/disabled", json={"disabled": True}, headers=bearer(boss)
)
check("disable succeeds", r.status_code, 200)
check("…and the row says so", r.json()["disabled"], True)

# ⚠ NO SLEEP HERE. If `auth.forget_cached_email` were not called this would pass
# only after the 30-second TTL, which is exactly the bug the call prevents.
check(
    "the token stops working in the same instant",
    client.get("/auth/me", headers=bearer(cust)).status_code,
    403,
)
check("…and a fresh sign-in is refused too", login("cust@example.com").status_code, 403)

client.post(
    "/admin/users/cust@example.com/disabled", json={"disabled": False}, headers=bearer(boss)
)
check("re-enabling lets them back in", login("cust@example.com").status_code, 200)


# ===========================================================================
print("\n--- roles, and the ADMIN_EMAILS floor ---")
# ===========================================================================
r = client.post(
    "/admin/users/other@example.com/role", json={"account_role": "admin"}, headers=bearer(boss)
)
check("an admin can promote somebody", r.status_code, 200)
check("…and it takes", users_mod.is_admin("other@example.com"))
r = client.post(
    "/admin/users/other@example.com/role", json={"account_role": "user"}, headers=bearer(boss)
)
check("…and demote them again", users_mod.is_admin("other@example.com"), False)

r = client.post(
    "/admin/users/other@example.com/role", json={"account_role": "root"}, headers=bearer(boss)
)
check("an unknown role is rejected by the schema", r.status_code, 422)

# The env floor. `pinned@example.com` is an admin before it even has an account.
register("pinned@example.com")
check("an ADMIN_EMAILS address is an admin", users_mod.is_admin("pinned@example.com"))
check(
    "…even with no role on its document",
    users_mod.get_user_by_email("pinned@example.com").get(users_mod.ROLE_FIELD),
    None,
)
r = client.post(
    "/admin/users/pinned@example.com/role", json={"account_role": "user"}, headers=bearer(boss)
)
# ⚠ REFUSED, NOT SILENTLY IGNORED. `role_of` would keep answering "admin", so a
# 200 here would be the panel reporting a change that did not happen.
check("…and the panel refuses to demote it rather than no-opping", r.status_code, 409)
check("…so it is still an admin", users_mod.is_admin("pinned@example.com"))
check(
    "…and the detail marks it locked",
    client.get("/admin/users/pinned@example.com", headers=bearer(boss)).json()["role_locked"],
    True,
)


# ===========================================================================
print("\n--- the event log ---")
# ===========================================================================
check("registering is recorded", "user.registered" in types_for("other@example.com"))
check("signing in is recorded", "user.login" in types_for("cust@example.com"))

login("cust@example.com", "wrong-password")
recent = events_mod.list_events(5, email="cust@example.com")
check("a bad password is recorded", recent[0]["type"], "user.login_failed")
# ⚠ THE 401 KEEPS THIS SECRET AND THE LOG MUST NOT. A run of failures against
# addresses that exist is an attack on accounts; the same run against addresses
# that don't is a scanner working a list, and only this flag tells them apart.
check("…and says the account existed", recent[0]["meta"]["existed"], True)
login("nobody@example.com", "wrong-password")
check(
    "…and says when it did not",
    events_mod.list_events(1, email="nobody@example.com")[0]["meta"]["existed"],
    False,
)

# Who DID it, as distinct from who it happened to.
admin_rows = [
    e for e in events_mod.list_events(50, email="other@example.com")
    if e["type"] == "admin.role_changed"
]
check("an admin action is recorded", len(admin_rows), 2)
check("…against the customer", admin_rows[0]["email"], "other@example.com")
check("…naming the administrator who did it", admin_rows[0]["actor"], "boss@example.com")
check("…and what changed", admin_rows[0]["meta"], {"was": "admin", "now": "user"})

# Asking for one address finds the rows where they are the ACTOR too, or "what
# has this administrator been doing" returns nothing at all.
by_actor = [e for e in events_mod.list_events(50, email="boss@example.com")]
check("a listing by address includes what they did to others", len(by_actor) >= 2)

r = client.get("/admin/events?type=user.login&limit=5", headers=bearer(boss))
check("the events route filters by type", r.status_code, 200)
check("…and returns only that type", {e["type"] for e in r.json()["events"]}, {"user.login"})

r = client.get("/admin/meta", headers=bearer(boss))
check("meta names the caller", r.json()["you"], "boss@example.com")
check("…and offers every known type", set(r.json()["event_types"]), set(events_mod.KNOWN_TYPES))


# ===========================================================================
print("\n--- the private note ---")
# ===========================================================================
r = client.post(
    "/admin/users/cust@example.com/note",
    json={"note": "Asked about invoicing on the 3rd."},
    headers=bearer(boss),
)
check("a note saves", r.status_code, 200)
check(
    "…and an admin can read it back",
    client.get("/admin/users/cust@example.com", headers=bearer(boss)).json()["admin_note"],
    "Asked about invoicing on the 3rd.",
)
me = client.get("/auth/me", headers=bearer(login("cust@example.com").json()["access_token"]))
# ⚠ IT IS ABOUT THEM AND NOT FOR THEM. /auth/me is built from an explicit field
# list, so this holds by construction — asserted anyway, because the day someone
# switches it to a passthrough is the day it stops holding.
check("…but the customer never sees it", "admin_note" in me.text, False)
check("…and neither is the note put in the event log", "invoicing" in str(events_mod.list_events(50)), False)


# ===========================================================================
print("\n--- deleting ---")
# ===========================================================================
r = client.delete("/admin/users/other@example.com", headers=bearer(boss))
check("delete succeeds", r.status_code, 204)
check("…the account is gone", users_mod.get_user_by_email("other@example.com"), None)
check("…and it is recorded", "admin.user_deleted" in types_for("other@example.com"))
r = client.delete("/admin/users/other@example.com", headers=bearer(boss))
check("…deleting it twice 404s", r.status_code, 404)


# ===========================================================================
print("\n--- job counts ---")
# ===========================================================================
from server.jobs import get_store  # noqa: E402
from server.schemas import JobKind  # noqa: E402

store = get_store()
store.create("A", kind=JobKind.STORYBOARD, owner="cust@example.com")
store.create("B", kind=JobKind.STORYBOARD, owner="cust@example.com")
store.create("C", kind=JobKind.PLAN, owner="cust@example.com")
store.create("D", kind=JobKind.PLAN, owner="boss@example.com")

check("count_by_kind counts one owner", store.count_by_kind(owner="cust@example.com"),
      {"storyboard": 2, "plan": 1})
check("…and the whole store", sum(store.count_by_kind().values()), 4)

r = client.get("/admin/users/cust@example.com", headers=bearer(boss))
check("the detail shows the breakdown", r.json()["jobs_by_kind"], {"storyboard": 2, "plan": 1})
check("…and the total on the row", r.json()["user"]["projects"], 3)

r = client.get("/admin/users?with_counts=false", headers=bearer(boss))
# ⚠ None AND 0 ARE DIFFERENT ANSWERS. "not counted" must not render as
# "no projects", which is why the field is nullable rather than defaulted.
check("counts are absent unless asked for", r.json()["users"][0]["projects"], None)
r = client.get("/admin/users?with_counts=true&search=cust", headers=bearer(boss))
check("…and present when they are", r.json()["users"][0]["projects"], 3)


# ===========================================================================
shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All admin panel checks passed.")

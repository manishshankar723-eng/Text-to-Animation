"""Contract checks for the user profile and password change.

Covers the parts that can actually hurt: the PATCH allow-list (a crafted body
must not be able to grant itself another account's rights), password change
requiring the CURRENT password, and owner isolation. Creates throwaway accounts
and deletes them afterwards. No AI quota.

    python tests/profile_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from server import config, users
from server.main import app
from server.mongo import get_db

failures: list[str] = []
emails: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
PW = "profile-pass-12345"


def new_user():
    email = f"_prof_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": PW})
    assert r.status_code == 201, r.text
    emails.append(email)
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


email, auth = new_user()

print("\n[1] a new profile is empty, not null")
r = client.get("/auth/me", headers=auth)
check("GET -> 200", r.status_code, 200)
p = r.json()
check("email is the login", p["email"], email)
check("full_name defaults to ''", p["full_name"], "")
check("default_style defaults to ''", p["default_style"], "")
check("created_at is present", bool(p["created_at"]))

print("\n[2] edit identity")
r = client.patch("/auth/me", headers=auth, json={
    "full_name": "  Manish Shankar  ", "display_name": "Manish",
})
check("PATCH -> 200", r.status_code, 200)
check("full_name saved and TRIMMED", r.json()["full_name"], "Manish Shankar")
check("display_name saved", r.json()["display_name"], "Manish")
check("persisted across requests", client.get("/auth/me", headers=auth).json()["full_name"], "Manish Shankar")

print("\n[3] partial PATCH must not blank other fields")
r = client.patch("/auth/me", headers=auth, json={"company": "VRImmersive Tech"})
check("company saved", r.json()["company"], "VRImmersive Tech")
check("full_name untouched", r.json()["full_name"], "Manish Shankar")
check("display_name untouched", r.json()["display_name"], "Manish")

print("\n[4] creative defaults")
r = client.patch("/auth/me", headers=auth, json={
    "default_style": "comic", "default_aspect_ratio": "16:9",
    "default_genre": "mythology", "timezone": "Asia/Kolkata", "role": "Director",
})
check("style saved", r.json()["default_style"], "comic")
check("aspect saved", r.json()["default_aspect_ratio"], "16:9")
check("genre saved", r.json()["default_genre"], "mythology")
check("timezone saved", r.json()["timezone"], "Asia/Kolkata")
check("role saved", r.json()["role"], "Director")

print("\n[5] PATCH is an ALLOW-LIST — privilege fields must be ignored")
before = users.get_user_by_email(email)
r = client.patch("/auth/me", headers=auth, json={
    "full_name": "Still Me",
    "email": "attacker@evil.dev",       # would hijack the login
    "disabled": True,                   # would lock the account
    "password_hash": "$2b$12$forged",   # would take over the account
    "api_keys": {"meshy": "stolen"},
})
check("PATCH -> 200 (unknown keys ignored, not fatal)", r.status_code, 200)
after = users.get_user_by_email(email)
check("email NOT changed", after["email"], before["email"])
check("disabled NOT set", after.get("disabled", False), False)
check("password_hash NOT overwritten", after["password_hash"], before["password_hash"])
check("api_keys NOT injected", after.get("api_keys", {}), before.get("api_keys", {}))
check("the legitimate field still applied", r.json()["full_name"], "Still Me")
check("can still log in with the original password",
      client.post("/auth/login", json={"email": email, "password": PW}).status_code, 200)

print("\n[6] length limits are enforced")
check("over-long full_name -> 422",
      client.patch("/auth/me", headers=auth, json={"full_name": "x" * 200}).status_code, 422)

print("\n[7] profiles are private to their owner")
other_email, other_auth = new_user()
check("stranger sees their OWN empty profile",
      client.get("/auth/me", headers=other_auth).json()["full_name"], "")
client.patch("/auth/me", headers=other_auth, json={"full_name": "Someone Else"})
check("my profile is unaffected", client.get("/auth/me", headers=auth).json()["full_name"], "Still Me")

print("\n[8] auth required")
check("GET without token -> 401", client.get("/auth/me").status_code, 401)
check("PATCH without token -> 401", client.patch("/auth/me", json={"full_name": "x"}).status_code, 401)
check("password change without token -> 401",
      client.post("/auth/me/password", json={"current_password": PW, "new_password": "whatever123"}).status_code, 401)

print("\n[9] change password")
check("wrong current password -> 400",
      client.post("/auth/me/password", headers=auth,
                  json={"current_password": "not-my-password", "new_password": "brand-new-pass-1"}).status_code, 400)
check("still logs in with the old one after a failed attempt",
      client.post("/auth/login", json={"email": email, "password": PW}).status_code, 200)
check("reusing the same password -> 400",
      client.post("/auth/me/password", headers=auth,
                  json={"current_password": PW, "new_password": PW}).status_code, 400)
check("too-short new password -> 422",
      client.post("/auth/me/password", headers=auth,
                  json={"current_password": PW, "new_password": "short"}).status_code, 422)

NEW_PW = "brand-new-pass-1"
check("valid change -> 204",
      client.post("/auth/me/password", headers=auth,
                  json={"current_password": PW, "new_password": NEW_PW}).status_code, 204)
check("old password no longer works",
      client.post("/auth/login", json={"email": email, "password": PW}).status_code, 401)
check("new password works",
      client.post("/auth/login", json={"email": email, "password": NEW_PW}).status_code, 200)
check("profile survived the password change",
      client.get("/auth/me", headers=auth).json()["company"], "VRImmersive Tech")

print("\n[10] delete account still works (it lives in the profile now)")
check("DELETE -> 204", client.delete("/auth/me", headers=auth).status_code, 204)
check("account is gone", users.get_user_by_email(email), None)

print("\n[11] cleanup")
db = get_db()
removed = db[config.USERS_COLLECTION].delete_many({"email": {"$in": emails}}).deleted_count
print(f"  removed {removed} throwaway account(s)")
check("only the real account remains", db[config.USERS_COLLECTION].count_documents({}), 1)

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All profile checks passed.")

"""Checks for the SIGN-IN -> DASHBOARD path — the one every session starts with.

What this is guarding, in plain terms: an account with work in it used to wait
noticeably longer at the dashboard than a new one, and the more work it had the
longer it waited. Four separate causes, four sections here:

  1. `POST /auth/login` reports how much work the account has, in the same
     answer as the token, so the browser can tell a new account from a
     returning one WITHOUT a second round trip. A new account gets `{}`.
  2. `GET /jobs` no longer ships `params` — the run inputs, reference images
     included — to screens that print a name and a date.
  3. `GET /storyboards?limit=N` applies the workflow and draft filters INSIDE
     the query. Filtering after the limit read an already-truncated page and
     could report an empty library over a full one.
  4. `GET /animatics` builds every card's cover from ONE shared board cache.
     It used to start a fresh one per card, so listing N projects made N
     sequential round trips to the job store.

Runs against the in-memory job store, so it touches no real project data. It
does create one account to hold a token, and deletes it on the way out.

    python tests/dashboard_boot_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ BEFORE the server package is imported — the store is chosen at import time.
os.environ["API_JOB_STORE"] = "memory"

from fastapi.testclient import TestClient  # noqa: E402

from server import users  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.main import app  # noqa: E402
from server.schemas import JobKind, JobStatus  # noqa: E402

failures: list[str] = []


def check(label, got, want=True):
    ok = got == want
    print(f"    {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r})"))
    if not ok:
        failures.append(label)


client = TestClient(app)
# ⚠ NOT a `.local` / `.test` address: `EmailStr` runs email-validator, which
# rejects special-use domains outright, and register answers 422 rather than
# creating anything. example.com is reserved for exactly this and passes.
EMAIL = f"dashcheck-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "dashboard-check-pw"


def main() -> int:
    # ---------------------------------------------------------------- 1
    print("  the login answer says whether this account has anything")
    r = client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    check("register succeeds", r.status_code, 201)
    check("counts is present", "counts" in r.json(), True)
    check("a brand-new account counts to nothing", r.json().get("counts"), {})

    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    store = get_store()

    # A library: 3 character runs with heavy inputs, 2 original boards, 9 copies
    # newer than the originals, 4 animatics off 2 boards, 1 plan.
    heavy = {"reference": "x" * 4000, "prompt": "a very long prompt"}
    for i in range(3):
        store.create(f"char {i}", kind=JobKind.GENERATE, owner=EMAIL, params=dict(heavy))

    boards = []
    for i in range(2):
        j = store.create(f"orig {i}", kind=JobKind.STORYBOARD, owner=EMAIL,
                         params={"count": 6})
        store.update(j.job_id, status=JobStatus.SUCCEEDED,
                     result={"panels": [{"url": f"/p/{n}"} for n in range(6)]})
        boards.append(j.job_id)
    # ⚠ NEWER than the originals, and more of them than a dashboard page holds.
    for i in range(9):
        j = store.create(f"copy {i}", kind=JobKind.STORYBOARD, owner=EMAIL,
                         params={"count": 6, "workflow": "animatic-image"})
        store.update(j.job_id, status=JobStatus.SUCCEEDED)
    draft = store.create("half-made board", kind=JobKind.STORYBOARD, owner=EMAIL,
                         params={"count": 3})
    store.update(draft.job_id, status=JobStatus.DRAFT)

    for i in range(4):
        store.create(
            f"anim {i}", kind=JobKind.ANIMATIC, owner=EMAIL,
            params={
                "settings": {"aspect_ratio": "16:9"},
                "frames": [{
                    "id": f"f{i}", "duration_ms": 2000,
                    # The source kind whose version token needs the BOARD record.
                    "src": {"kind": "panel", "storyboard_id": boards[i % 2], "index": i},
                }],
                "texts": [], "audio_tracks": [],
            },
        )
    store.create("a plan", kind=JobKind.PLAN, owner=EMAIL,
                 params={"messages": [{"role": "user", "text": "hi"}], "scripts": []})

    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    counts = r.json().get("counts") or {}
    check("login still succeeds", r.status_code, 200)
    check("character runs counted", counts.get("generate"), 3)
    check("boards counted (originals + copies + draft)", counts.get("storyboard"), 12)
    check("animatics counted", counts.get("animatic"), 4)
    check("plans counted", counts.get("plan"), 1)

    # ---------------------------------------------------------------- 2
    print("  GET /jobs does not ship the run inputs")
    r = client.get("/jobs?kind=generate,meshy&limit=8", headers=headers)
    check("200", r.status_code, 200)
    jobs = r.json()
    check("all three runs listed", len(jobs), 3)
    check("params is empty in the answer", all(j["params"] == {} for j in jobs), True)
    check("the fields the screens DO read survive",
          all(j["character_name"] and j["created_at"] and j["status"] for j in jobs), True)
    check("`result` is still part of the shape (the ZIP link lives there)",
          all("result" in j for j in jobs), True)
    check("the payload is proportionate to what is drawn", len(r.content) < 2000, True)
    # ⚠ The store must be untouched: dropping is about the ANSWER.
    check("the stored records still have their inputs",
          all(len((store.get(j["job_id"]).params or {}).get("reference", "")) == 4000
              for j in jobs), True)

    # ---------------------------------------------------------------- 3
    print("  GET /storyboards filters before it limits")
    r = client.get("/storyboards?limit=8", headers=headers)
    check("200", r.status_code, 200)
    titles = sorted(b["title"] for b in r.json())
    # Nine copies and a draft are all NEWER. Filtering after the limit would
    # have handed this workflow an empty library.
    check("both originals found under an 8-row limit", titles, ["orig 0", "orig 1"])
    check("the draft is not offered as a finished board",
          any(b["title"] == "half-made board" for b in r.json()), False)

    r = client.get("/storyboards?workflow=animatic-image&limit=8", headers=headers)
    check("the copies library fills its page", len(r.json()), 8)
    r = client.get("/storyboards?workflow=animatic-image&limit=100", headers=headers)
    check("and has all nine when asked for them", len(r.json()), 9)
    # `*` is what the downstream workflows ask for. The browser now sends it
    # percent-encoded (URLSearchParams writes `%2A`); both must mean the same.
    for q in ("*", "%2A"):
        r = client.get(f"/storyboards?workflow={q}&limit=100", headers=headers)
        check(f"workflow={q} returns every non-draft board", len(r.json()), 11)

    # ---------------------------------------------------------------- 4
    print("  GET /animatics reads each board once, not once per card")
    reads = {"n": 0}
    real_get = store.get

    def counting_get(job_id):
        reads["n"] += 1
        return real_get(job_id)

    store.get = counting_get
    try:
        r = client.get("/animatics?limit=8", headers=headers)
    finally:
        store.get = real_get

    check("200", r.status_code, 200)
    cards = r.json()
    check("all four listed", len(cards), 4)
    check("every card still gets a cover", all(c["cover_url"] for c in cards), True)
    check("covers still carry a version token",
          all("?v=" in (c["cover_url"] or "") for c in cards), True)
    print(f"      store reads to list {len(cards)} animatics off {len(boards)} boards:"
          f" {reads['n']}")
    check("one read per BOARD, not one per card", reads["n"] <= len(boards), True)

    # ---------------------------------------------------------------- 5
    print("  the rest of the dashboard's feeds still answer")
    for path in ("/plans?limit=8", "/final-videos?limit=8",
                 "/auth/me", "/auth/me/entitlements"):
        check(f"{path}", client.get(path, headers=headers).status_code, 200)

    return 0


try:
    code = main()
finally:
    print("\n=== cleanup ===")
    # The jobs were only ever in the in-process store. The ACCOUNT is real.
    if users.get_user_by_email(EMAIL):
        users.delete_user(EMAIL)
    print(f"    {'ok  ' if not users.get_user_by_email(EMAIL) else 'FAIL'} "
          f"test account removed")

print()
if failures:
    print(f"FAILED ({len(failures)}): " + ", ".join(failures))
    sys.exit(1)
print("All dashboard-boot checks passed — sign-in tells the client what it needs,")
print("and no list pays for data nobody draws.")
sys.exit(0)

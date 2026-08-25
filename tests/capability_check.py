"""A LOCKED CAPABILITY IS DRAWN AS LOCKED — the browser's half of the resolver.

Phases 2-5 built the enforcement: `require_feature('cap.veo-render')` and its
twenty-four neighbours refuse a request the account may not make. What none of
them did was TELL anyone. A customer whose Veo was off still saw ✨ Animate,
still wrote a motion prompt, still pressed the button, and learned the answer
from a 403. Workflows were handled — hidden, badged or locked in the rail — and
the capabilities inside them were not.

So this file guards the one claim that fix rests on: **the sentence on the
greyed-out button is the same sentence the route would have refused with.** Two
wordings for one refusal is how a support agent ends up unable to tell which
rule fired, and it is the only part of this that cannot be seen by looking at
the screen.

In rough order of how much it would hurt to get wrong:

  ONE WORDING, TWO SURFACES. `refusal()` writes the 403 detail AND the `reason`
  on `/auth/me/entitlements`. Asserted by pressing the route and comparing
  strings, not by eye.

  IT FAILS OPEN. Before the boot call answers, every capability is ON. A cold
  start that greyed out a paid-for button for a second — on every page load, for
  every account — would be a worse bug than the one this fixes.

  THREE STATES, NOT TWO. Gone (hidden / not in this rollout) → the control is
  not drawn. Locked (a tier) → drawn, disabled, wearing the reason. On → drawn.
  A single boolean cannot say that, and squashing it is what made the old
  "soon" placeholder navigate to a blank page.

  THE REFUSAL NAMES THE PLAN when the plan is the reason. "Not enabled for your
  account" leaves a paying customer with nowhere to go; this is the one refusal
  with an action attached.

  THE FALLBACK CATALOGUE MATCHES. `CAPABILITIES` in `client/src/entitlements.js`
  is the list the app uses when the call has not answered. It is a copy of
  `_CAPABILITIES` in `server/features.py`, and the two drifting apart means an
  account is quietly told it has something it does not.

The last section runs the browser module itself under **node** — the same
arrangement `audio_mix_check.py` uses for the exporter's twin. `entitlements.js`
imports no React precisely so that it can be driven here; without node those
checks report SKIPPED rather than passing.

    python tests/capability_check.py

Needs no MongoDB, no network and no AI quota: every store points at a fresh
temporary directory before `server.config` is imported.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.mkdtemp(prefix="capability_check_")
os.environ["API_USER_STORE"] = "local"
os.environ["API_JOB_STORE"] = "memory"
os.environ["API_LOCAL_USERS_PATH"] = os.path.join(_TMP, "users.json")
os.environ["API_LOCAL_DRAFTS_PATH"] = os.path.join(_TMP, "drafts.json")
os.environ["API_LOCAL_EVENTS_PATH"] = os.path.join(_TMP, "events.json")
os.environ["API_LOCAL_FEATURES_PATH"] = os.path.join(_TMP, "features.json")
os.environ["API_LOCAL_TIERS_PATH"] = os.path.join(_TMP, "tiers.json")
os.environ["API_LOCAL_OFFERS_PATH"] = os.path.join(_TMP, "offers.json")
os.environ["API_LOCAL_SUBSCRIPTIONS_PATH"] = os.path.join(_TMP, "subs.json")
os.environ["API_LOCAL_USAGE_PATH"] = os.path.join(_TMP, "usage.json")
os.environ["API_LOCAL_JOBS_PATH"] = os.path.join(_TMP, "jobs.json")
os.environ["API_REAP_ORPHANED_JOBS"] = "0"
os.environ["JWT_SECRET"] = "capability-check-not-a-real-secret"
# No cache: a write must be visible to the next read, so a failure here is a bug
# in the rules and never a stale read racing the assertion. Same as features_check.
os.environ["API_FEATURE_CACHE_TTL_S"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from server import features as feat, users as users_mod  # noqa: E402
from server.jobs import get_store  # noqa: E402
from server.main import app  # noqa: E402
from server.schemas import JobKind  # noqa: E402

failures: list[str] = []
skipped: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


def skip(label, why):
    print(f"  SKIP {label}  ({why})")
    skipped.append(label)


client = TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def register(email):
    return client.post(
        "/auth/register", json={"email": email, "password": "password123"}
    ).json()["access_token"]


def set_status(key, status):
    r = client.patch(f"/admin/features/{key}", json={"status": status}, headers=bearer(BOSS))
    assert r.status_code == 200, r.text


def set_rollout(key, **rollout):
    body = {"rollout": {"mode": "all", "emails": [], "percent": 100, **rollout}}
    r = client.patch(f"/admin/features/{key}", json=body, headers=bearer(BOSS))
    assert r.status_code == 200, r.text


def set_min_tier(key, tier):
    r = client.post(f"/admin/features/{key}/min-tier", json={"tier": tier}, headers=bearer(BOSS))
    assert r.status_code == 200, r.text


def caps(token):
    """`{id: row}` from the boot call — what the browser will actually hold."""
    body = client.get("/auth/me/entitlements", headers=bearer(token)).json()
    return {c["id"]: c for c in body["capabilities"]}


BOSS = register("boss@example.com")
CUST = register("cust@example.com")
users_mod.set_role("boss@example.com", users_mod.ROLE_ADMIN)
# ⚠ ON AN UNLIMITED TIER, for the reason written at the top of features_check:
# this suite is about what is switched ON, not how much of it you may have, and
# a quota refusal here would send the next person hunting in the wrong module.
users_mod.set_tier("cust@example.com", "production")

animatic = get_store().create("A", kind=JobKind.ANIMATIC, owner="cust@example.com")
ANIMATE = f"/animatics/{animatic.job_id}/animate"


# ===========================================================================
print("\n--- the boot call carries the capabilities, shaped for a button ---")
# ===========================================================================
# ⚠ PRE-SHAPED, LIKE THE WORKFLOWS LIST. A capability is not a page — it is a
# button inside one — and the browser has to draw it in three states. Working
# that out from the raw `states` map would put the drawing rules in two places,
# which is the thing the resolver exists to end.
rows = caps(CUST)
check("all six are there", len(rows), 6)
check("…every one of them on", all(c["on"] for c in rows.values()))
check("…each carrying the label to print", rows["veo-render"]["label"], "Veo video renders")
check("…and the icon", bool(rows["veo-render"]["icon"]), True)
check("…with no reason, because there is nothing to refuse", rows["veo-render"]["reason"], "")
check("…and the key, so a caller can find it in `states`",
      rows["veo-render"]["key"], "cap.veo-render")
check("the ids are the key without its prefix",
      sorted(rows), ["3d-meshy", "captions", "director", "image-generate",
                     "tts-voiceover", "veo-render"])

# ⚠ THE FALLBACK IN THE BROWSER IS A COPY OF THE SERVER'S CATALOGUE, and it is
# what the app draws with before this call lands. If the two drift, an account
# is briefly told it has something it does not.
CLIENT_ENTITLEMENTS = ROOT / "client/src/entitlements.js"
client_src = CLIENT_ENTITLEMENTS.read_text(encoding="utf-8")
client_ids = re.findall(r'\{\s*id:\s*"([^"]+)"', client_src)
server_ids = [key.split(".", 1)[1] for key, f in feat.all_features().items()
              if f.get("group") == feat.GROUP_CAPABILITY]
check("the client's fallback list matches the server's, in order",
      client_ids, sorted(server_ids, key=lambda i: feat.all_features()[f"cap.{i}"]["order"]))


# ===========================================================================
print("\n--- ONE wording for one refusal ---")
# ===========================================================================
# THE CLAIM THIS WHOLE FILE IS FOR. The greyed-out button and the error the
# customer would have got by pressing it have to say the same thing, or the
# support ticket that arrives cannot be matched to the rule that fired.
set_status("cap.veo-render", "soon")
row = caps(CUST)["veo-render"]
refused = client.post(ANIMATE, json={"frame_ids": []}, headers=bearer(CUST))
check("the route still refuses", refused.status_code, 403)
check("…and the browser was given the SAME sentence",
      row["reason"], refused.json()["detail"])
check("…which says 'not yet', because that is what soon means",
      "yet" in row["reason"], True)
check("…drawn, and not usable", (row["on"], row["status"]), (False, "soon"))
set_status("cap.veo-render", "live")


# ===========================================================================
print("\n--- gone, locked, on: three states, not two ---")
# ===========================================================================
# 1. GONE — the kill switch. Nothing to say and nothing to sell, so the control
#    is not drawn at all.
set_status("cap.veo-render", "hidden")
check("a hidden capability is not in the list", "veo-render" in caps(CUST), False)
check("…and the route refuses",
      client.post(ANIMATE, json={}, headers=bearer(CUST)).status_code, 403)
# ⚠ AND AN ADMIN DOES NOT BYPASS IT. "hidden" is the switch you throw when
# something is broken; it has to mean everyone.
check("…for an administrator too", "veo-render" in caps(BOSS), False)
set_status("cap.veo-render", "live")

# 2. GONE — a rollout this account is not in. Same answer, different reason.
set_rollout("cap.captions", mode="admins")
check("a capability still being staged is not drawn", "captions" in caps(CUST), False)
check("…but the person staging it can see it", "captions" in caps(BOSS), True)
set_rollout("cap.captions", mode="all")

# 3. LOCKED — one purchase away. ⚠ DRAWN, because nobody upgrades for what they
#    cannot see. This is the case that separates a lock from a kill switch.
set_min_tier("cap.veo-render", "production")
users_mod.set_tier("cust@example.com", "trial")
row = caps(CUST)["veo-render"]
check("a capability above the tier IS drawn", bool(row), True)
check("…marked locked", row["locked"], True)
check("…naming the tier that unlocks it", row["min_tier"], "production")
# ⚠ THE PLAN IS NAMED, not just the fact of being locked. "Not enabled for your
# account" is a dead end; this is the one refusal with an action attached.
check("…and the reason names the plan, by NAME not by id",
      "Production" in row["reason"] or "production" in row["reason"], True)
refused = client.post(ANIMATE, json={}, headers=bearer(CUST))
check("…the route refuses with that same sentence",
      row["reason"], refused.json()["detail"])

# 4. ON — buy it and nothing else changes.
users_mod.set_tier("cust@example.com", "production")
row = caps(CUST)["veo-render"]
check("moving the account up unlocks it", (row["on"], row["locked"]), (True, False))
check("…and the reason goes away with the lock", row["reason"], "")
set_min_tier("cap.veo-render", "")


# ===========================================================================
print("\n--- an override reopens it, in both directions ---")
# ===========================================================================
# The lever that exists so one customer can be given early access — and so one
# customer can be switched off without touching anybody else.
r = client.post("/admin/users/cust@example.com/override",
                json={"key": "cap.tts-voiceover", "value": False}, headers=bearer(BOSS))
check("an override can be set", r.status_code, 200)
row = caps(CUST).get("tts-voiceover")
check("…and it closes the capability", row is None or row["on"] is False, True)

set_status("cap.director", "hidden")
client.post("/admin/users/cust@example.com/override",
            json={"key": "cap.director", "value": True}, headers=bearer(BOSS))
row = caps(CUST).get("director")
check("an override reopens even a HIDDEN capability", row is not None and row["on"], True)
set_status("cap.director", "live")
client.post("/admin/users/cust@example.com/override",
            json={"key": "cap.director", "value": None}, headers=bearer(BOSS))
client.post("/admin/users/cust@example.com/override",
            json={"key": "cap.tts-voiceover", "value": None}, headers=bearer(BOSS))
check("clearing them puts everything back", len(caps(CUST)), 6)


# ===========================================================================
print("\n--- `explain` is `is_on` with the answer kept ---")
# ===========================================================================
# The guard needs the SOURCE, not the verdict: "off" and "off because it is on a
# plan you are not on" are the same refusal and two different sentences.
set_min_tier("cap.3d-meshy", "pro")
users_mod.set_tier("cust@example.com", "trial")
state = feat.explain("cust@example.com", "cap.3d-meshy")
check("it answers with the whole state", state["source"], "tier")
check("…and `is_on` still answers a bool",
      feat.is_on("cust@example.com", "cap.3d-meshy"), False)
# ⚠ AN UNKNOWN KEY IS ON. A guard naming a feature nobody added to the catalogue
# must not silently close a working route — the typo belongs in the panel, not
# in a customer's face.
check("an unknown key fails OPEN", feat.explain("cust@example.com", "cap.nonsense")["on"], True)
set_min_tier("cap.3d-meshy", "")
users_mod.set_tier("cust@example.com", "production")


# ===========================================================================
print("\n--- the browser's half, under node ---")
# ===========================================================================
# `entitlements.js` imports no React so that it can be driven here directly.
# These are the rules the greyed-out button is actually drawn from.
HARNESS = """
import { capabilityState, setEntitlements, clearEntitlements, getEntitlements }
  from "%(mod)s";

const answer = JSON.parse(process.argv[2]);
const out = {};

// Before the call lands: nothing is known, and everything is on.
out.coldStart = capabilityState(null, "veo-render");
out.coldStartUnknownId = capabilityState(undefined, "whatever");

// After it lands.
out.on = capabilityState(answer, "image-generate");
out.locked = capabilityState(answer, "veo-render");
out.missing = capabilityState(answer, "captions");

// The store: set, read back by identity, and clear back to fail-open.
setEntitlements(answer);
out.stored = getEntitlements() === answer;
out.storedState = capabilityState(getEntitlements(), "veo-render");
clearEntitlements();
out.clearedState = capabilityState(getEntitlements(), "veo-render");

console.log(JSON.stringify(out));
"""


def run_node(answer: dict):
    if not shutil.which("node"):
        return None
    harness = os.path.join(_TMP, "harness.mjs")
    with open(harness, "w", encoding="utf-8") as fh:
        fh.write(HARNESS % {"mod": CLIENT_ENTITLEMENTS.as_uri()})
    proc = subprocess.run(
        ["node", harness, json.dumps(answer)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        print("    node said:", (proc.stderr or "").strip()[:400])
        return None
    return json.loads(proc.stdout)


# A realistic answer: one capability on, one locked, and captions ABSENT — which
# is what a hidden capability or a rollout looks like from the browser.
set_min_tier("cap.veo-render", "production")
users_mod.set_tier("cust@example.com", "trial")
set_status("cap.captions", "hidden")
answer = client.get("/auth/me/entitlements", headers=bearer(CUST)).json()
set_status("cap.captions", "live")
set_min_tier("cap.veo-render", "")
users_mod.set_tier("cust@example.com", "production")

browser = run_node(answer)
if browser is None:
    for label in ("nothing is drawn as locked before the call answers",
                  "an on capability reads as on",
                  "a locked one carries the server's own sentence",
                  "one the server never mentioned is not drawn at all",
                  "the store hands back what it was given",
                  "clearing it goes back to fail-open, never to all-off"):
        skip(label, "node not available")
else:
    cold = browser["coldStart"]
    # ⚠ THE ONE THAT WOULD HURT EVERY ACCOUNT, NOT SOME. A cold start that reads
    # as "off" greys out a paid-for button on every page load in the app.
    check("nothing is drawn as locked before the call answers",
          (cold["on"], cold["visible"], cold["known"]), (True, True, False))
    check("…including an id the browser has never heard of",
          browser["coldStartUnknownId"]["on"], True)

    check("an on capability reads as on", browser["on"]["on"], True)

    locked = browser["locked"]
    check("a locked one is visible, off and marked locked",
          (locked["visible"], locked["on"], locked["locked"]), (True, False, True))
    server_reason = next(c["reason"] for c in answer["capabilities"] if c["id"] == "veo-render")
    check("a locked one carries the server's own sentence",
          locked["reason"], server_reason)
    check("…and the tier to sell", locked["minTier"], "production")

    missing = browser["missing"]
    # ⚠ MISSING FROM AN ANSWER THAT ARRIVED IS "HIDDEN"; missing from one that
    # never arrived is "we don't know". `known` is what tells them apart.
    check("one the server never mentioned is not drawn at all",
          (missing["visible"], missing["on"], missing["known"]), (False, False, True))
    check("…with no reason, because there is nothing to offer", missing["reason"], "")

    check("the store hands back what it was given", browser["stored"], True)
    check("…and the same answer comes out of it", browser["storedState"]["locked"], True)
    check("clearing it goes back to fail-open, never to all-off",
          browser["clearedState"]["on"], True)


# ===========================================================================
print("\n--- and the guards are still the real lock ---")
# ===========================================================================
# ⚠ THE BROWSER'S ANSWER IS A COURTESY, NOT A GATE. Everything above changes
# what is DRAWN. Editing it in a debugger turns a control back on and the route
# still refuses — which is the whole reason `require_feature` stays where it is.
set_status("cap.veo-render", "hidden")
check("a switched-off route refuses however the button was drawn",
      client.post(ANIMATE, json={"frame_ids": []}, headers=bearer(CUST)).status_code, 403)
check("…before the body is even validated",
      client.post(ANIMATE, json={"nonsense": 1}, headers=bearer(CUST)).status_code, 403)
set_status("cap.veo-render", "live")


shutil.rmtree(_TMP, ignore_errors=True)
print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
if skipped:
    print(f"{len(skipped)} skipped (no node).")
print("All capability-surfacing checks passed.")

"""workflow_reach_check.py — A "LIVE" WORKFLOW THAT NO CUSTOMER CAN SEE.

    python tests/workflow_reach_check.py    (no backend of your own; needs node
                                             for the panel half)

Why this file exists, in one sentence: **a workflow was switched on and nobody
could see it, and the admin panel said everything was fine.**

    "mai jab text to turnaround image workflow live kiya hun na hi wo user
     workflow mai na landing page mai dikh raha hai kyun?"

⚠ **THE RESOLVER WAS RIGHT. THE SETTING WAS THE TRAP.**
`workflow.text-to-image` was `status: live` with `rollout.mode: "allowlist"` and
an EMPTY email list, so `_rollout_passes` refused every account — the sidebar
dropped it, `/public/workflows` dropped it, and the landing page went on saying
"Three workflows".

⚠ **AND IT IS INVISIBLE TO THE ONLY PERSON WHO EVER CHECKS.** Admins pass every
rollout gate on purpose (you cannot stage a feature you are locked out of), so
the administrator who threw the switch sees the workflow in their OWN rail and
concludes it launched. That asymmetry is the whole bug, and section 2 pins it
directly: the same feature, the same instant, visible to an admin and invisible
to everybody else.

⚠ **THE FIX IS A WARNING, NOT A CORRECTION.** An empty allowlist is a legitimate
state — you make the list before you fill it — so nothing rewrites the setting.
`reachesNobody()` names it, the row turns amber, and a "Show it to everyone"
button sits next to the sentence. Section 3 renders the real row and requires
the alarming half to be there and the reassuring half to be GONE, because
"Everyone in the rollout can use it." printed above the warning is exactly how
this was missed.

⚠ **AND SECTION 1 IS THE OTHER HALF OF WHAT WAS ASKED** — *"sab workflow check
karna kaam kar raha hai ki nhi"*. Every workflow in the catalogue, one at a time:
live and open reaches a signed-in customer AND the logged-out landing page; each
of the four rollout modes does what it says; and hidden means hidden to admins
too. Six workflows × the states they can be in, rather than the one that was
reported.

RULEBOOK **E104**. Touches no real store — every path is a temp dir set before
`server.config` is imported.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"

# ---------------------------------------------------------------------------
# Every store into a temp dir BEFORE anything imports config. Same rule, and the
# same reason, as `branding_check.py` — and RULEBOOK G13, which was paid for by
# a test spending the developer's own quota.
# ---------------------------------------------------------------------------
_TMP = tempfile.mkdtemp(prefix="wf_reach_")
ADMIN = "boss@example.com"
USER = "msk@example.com"
os.environ.update({
    "API_USER_STORE": "local",
    "API_JOB_STORE": "memory",
    "API_LOCAL_USERS_PATH": os.path.join(_TMP, "users.json"),
    "API_LOCAL_EVENTS_PATH": os.path.join(_TMP, "events.json"),
    "API_LOCAL_FEATURES_PATH": os.path.join(_TMP, "features.json"),
    "API_LOCAL_BRANDING_PATH": os.path.join(_TMP, "branding.json"),
    "API_LOCAL_JOBS_PATH": os.path.join(_TMP, "jobs.json"),
    "API_LOCAL_USAGE_PATH": os.path.join(_TMP, "usage.json"),
    "API_LOCAL_DRAFTS_PATH": os.path.join(_TMP, "drafts.json"),
    "API_UPLOAD_DIR": os.path.join(_TMP, "uploads"),
    "ADMIN_EMAILS": ADMIN,
    "API_REAP_ORPHANED_JOBS": "0",
})

from server import features  # noqa: E402  — must come after the env above

failures = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


def says_line(markup):
    """Just the row's summary paragraph.

    ⚠ NOT THE WHOLE MARKUP, AND THE DIFFERENCE IS A REAL ONE. The `Live`
    button carries `title="Everyone in the rollout can use it."` — a correct
    description of what the STATUS means, on a control whose whole job is to
    describe the statuses. Searching the whole row for that string therefore
    fails forever however right the fix is. The claim being tested is about the
    SENTENCE PRINTED UNDER THE ROW, so that is what gets read.
    """
    at = markup.find("admin-feature-says")
    if at < 0:
        return ""
    end = markup.find("</p>", at)
    return markup[at:end if end > 0 else len(markup)]


def sees(email, key, is_admin=False):
    """Is this feature VISIBLE to this account? (`""` = the landing page.)"""
    return features.resolve(email, is_admin=is_admin)[key]["visible"]


def public_ids():
    """What a logged-out visitor is told — the landing page's own list."""
    return [w["id"] for w in features.public_workflows()["workflows"]]


def set_rollout(key, mode, **kw):
    roll = {"mode": mode, "emails": kw.get("emails", []), "percent": kw.get("percent", 100)}
    features.save_feature(key, {"status": kw.get("status", "live"), "rollout": roll},
                          actor=ADMIN)


ALL_WORKFLOWS = [f"workflow.{wid}" for wid, _l, _i in features._WORKFLOWS]
REPORTED = "workflow.text-to-image"


# ===========================================================================
print("1 · every workflow, live and open, reaches a customer AND the shop window")
# ===========================================================================
check(f"the catalogue still has every workflow ({len(ALL_WORKFLOWS)})",
      len(ALL_WORKFLOWS) >= 6, str(ALL_WORKFLOWS))
# ⚠ THE ONE THAT WAS REPORTED, NAMED. A sweep that stops covering the actual
# complaint is a sweep that passes for the wrong reason.
check("…including the one that was reported", REPORTED in ALL_WORKFLOWS)

for key in ALL_WORKFLOWS:
    wid = key.split(".", 1)[1]
    set_rollout(key, "all")
    ok_user = sees(USER, key)
    ok_anon = sees("", key)
    ok_admin = sees(ADMIN, key, is_admin=True)
    check(f"{wid}: a customer sees it", ok_user)
    check(f"{wid}: …and so does the landing page", ok_anon and wid in public_ids())
    check(f"{wid}: …and so does an admin", ok_admin)

check("the landing page lists all of them at once",
      sorted(public_ids()) == sorted(k.split(".", 1)[1] for k in ALL_WORKFLOWS),
      str(public_ids()))


# ===========================================================================
print("\n2 · the trap: LIVE, and reaching nobody but the person who set it")
# ===========================================================================
# The exact state that was reported, rebuilt from the screenshot: Live, "Named
# people", nobody named.
set_rollout(REPORTED, "allowlist", emails=[])
row = features.all_features()[REPORTED]
check("the feature really is stored as live", row["status"] == "live", row["status"])
check("…with an allowlist and nobody on it",
      row["rollout"]["mode"] == "allowlist" and row["rollout"]["emails"] == [],
      json.dumps(row["rollout"]))

check("a customer cannot see it — this is the reported bug", sees(USER, REPORTED) is False)
check("…nor can the landing page", REPORTED.split(".", 1)[1] not in public_ids())
# ⚠ THE ASYMMETRY IS THE BUG. The admin sees it and concludes it launched.
check("…but the ADMIN can, which is why nobody noticed",
      sees(ADMIN, REPORTED, is_admin=True) is True)

# The other way to reach nobody, and it has to be caught by the same rule.
set_rollout(REPORTED, "percent", percent=0)
check("a 0% rollout is the same trap", sees(USER, REPORTED) is False)
check("…and the admin still sees it", sees(ADMIN, REPORTED, is_admin=True) is True)

# The modes that are meant to exclude people are left alone — they say so.
set_rollout(REPORTED, "allowlist", emails=[USER])
check("naming somebody lets exactly them in", sees(USER, REPORTED) is True)
check("…and nobody else", sees("someone.else@example.com", REPORTED) is False)
set_rollout(REPORTED, "admins")
check("'admins only' keeps a customer out", sees(USER, REPORTED) is False)
set_rollout(REPORTED, "all", status="hidden")
check("'hidden' hides it from the admin too, as it promises",
      sees(ADMIN, REPORTED, is_admin=True) is False)

# Put it back the way a working deployment has it.
set_rollout(REPORTED, "all")
check("and switching it back to Everyone fixes it in one step", sees(USER, REPORTED) is True)


# ===========================================================================
print("\n3 · a live workflow leads somewhere — every one of them")
# ===========================================================================
# ⚠ "VISIBLE" AND "WORKS" ARE TWO DIFFERENT QUESTIONS, and the second half of
# what was asked was the second one: *"sab workflow check karna kaam kar raha
# hai ki nhi"*. A workflow can pass every check above and still be a rail row
# that navigates to a blank page, because the rail is DATA from the server while
# the pages are a chain of `else if` in `App.jsx`. Nothing connects the two but
# the id — so the id is what gets checked, in both directions.
import re as _re


def read_src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


app_jsx = read_src("client", "src", "App.jsx")
sidebar_jsx = read_src("client", "src", "components", "Sidebar.jsx")
icons_jsx = read_src("client", "src", "components", "WorkflowIcon.jsx")

for key in ALL_WORKFLOWS:
    wid = key.split(".", 1)[1]
    check(f"{wid}: has a page in App.jsx", f'page === "{wid}"' in app_jsx)
    # The rail's offline fallback, used when the entitlements call fails and
    # this browser has never had an answer. A workflow missing from it vanishes
    # exactly when the server is already having a bad day.
    check(f"{wid}: is in the sidebar's offline fallback", f'id: "{wid}"' in sidebar_jsx)
    # The collapsed rail draws a short name under each glyph; without one it
    # falls back to the last word of the label, which is a guess.
    check(f"{wid}: has a short name for the collapsed rail", f'"{wid}":' in sidebar_jsx)
    check(f"{wid}: has a drawn glyph, not an emoji", f'"{wid}": (' in icons_jsx)

# ⚠ THE OTHER DIRECTION, WHICH IS THE ONE THAT ROTS QUIETLY: a drawing or a
# short name for a workflow the catalogue no longer has. Nothing breaks, so
# nothing tells you — it is dead code that reads like a feature.
ids = {k.split(".", 1)[1] for k in ALL_WORKFLOWS}
icon_ids = set(_re.findall(r'^\s*"([a-z0-9-]+)": \(', icons_jsx, _re.M))
check("no drawn glyph belongs to a workflow that no longer exists",
      icon_ids <= ids, ", ".join(sorted(icon_ids - ids)))
short_ids = set(_re.findall(r'^\s*"([a-z0-9-]+)": "', sidebar_jsx, _re.M))
check("no short name belongs to a workflow that no longer exists",
      short_ids <= ids, ", ".join(sorted(short_ids - ids)))

# ⚠ AND THE OFFLINE FALLBACK MUST STILL MATCH THE SERVER, LABEL FOR LABEL.
# `Sidebar.jsx` says in as many words to keep its array byte-identical to
# `_WORKFLOWS`, because a mismatch reorders somebody's rail on the one day the
# server cannot answer — the day nobody is looking at the rail's order.
for wid, label, _icon in features._WORKFLOWS:
    check(f"{wid}: the fallback label matches the server's",
          f'id: "{wid}", label: "{label}"' in sidebar_jsx, label)


# ===========================================================================
print("\n4 · the panel says so — rendered, not read as source")
# ===========================================================================
ENTRY = """
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FeatureRow, reachesNobody } from "./src/admin/AdminFeatures.jsx";

const base = {
  key: "workflow.text-to-image",
  label: "Text to Turnaround Image",
  icon: "IMG",
  group: "workflow",
  note: "",
  order: 1,
  status: "live",
  min_tier: null,
  updated_at: null,
  updated_by: null,
};

const draw = (feature) => renderToStaticMarkup(
  React.createElement(FeatureRow, {
    feature, tiers: [], onSaveMinTier: () => {}, busy: "",
    first: false, last: false, onSave: () => {}, prev: null, next: null,
  }));

const out = { render: {}, fn: {} };
out.render.trap = draw({ ...base, rollout: { mode: "allowlist", emails: [], percent: 100 } });
out.render.zero = draw({ ...base, rollout: { mode: "percent", emails: [], percent: 0 } });
out.render.fine = draw({ ...base, rollout: { mode: "all", emails: [], percent: 100 } });
out.render.named = draw({
  ...base, rollout: { mode: "allowlist", emails: ["a@b.com"], percent: 100 } });
out.render.hidden = draw({
  ...base, status: "hidden", rollout: { mode: "allowlist", emails: [], percent: 100 } });

// The rule on its own, so a failure says whether the logic or the markup broke.
out.fn = {
  emptyList: reachesNobody("live", { mode: "allowlist", emails: [] }),
  zeroPercent: reachesNobody("live", { mode: "percent", percent: 0 }),
  everyone: reachesNobody("live", { mode: "all" }),
  named: reachesNobody("live", { mode: "allowlist", emails: ["a@b.com"] }),
  adminsOnly: reachesNobody("live", { mode: "admins" }),
  hidden: reachesNobody("hidden", { mode: "allowlist", emails: [] }),
  noRollout: reachesNobody("live", undefined),
};

process.stdout.write(JSON.stringify(out));
"""


def run_js():
    if not shutil.which("node"):
        print("  node is not on PATH — the panel half was not checked.")
        return None
    if not (CLIENT / "node_modules" / "react-dom").exists():
        print("  client/node_modules is missing — run `cd client && npm install`.")
        return None
    work = tempfile.mkdtemp(prefix="wf_reach_js_")
    entry = CLIENT / "__wf_reach_entry.jsx"
    try:
        entry.write_text(ENTRY, encoding="utf-8")
        bundle = os.path.join(work, "bundle.cjs")
        esbuild = CLIENT / ("node_modules/.bin/esbuild.cmd" if os.name == "nt"
                            else "node_modules/.bin/esbuild")
        build = subprocess.run(
            [str(esbuild), str(entry), "--bundle", "--platform=node", "--format=cjs",
             "--loader:.js=jsx", "--jsx=automatic", f"--outfile={bundle}",
             # `api.js` reads Vite's `import.meta.env` at module scope; under node
             # that object does not exist and the bundle throws before a check runs.
             '--define:import.meta.env={"VITE_API_BASE":"http://127.0.0.1:8000"}',
             "--log-level=error"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(CLIENT))
        if build.returncode != 0:
            print("    esbuild said:", (build.stderr or "").strip()[:1200])
            return None
        proc = subprocess.run(["node", bundle], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(CLIENT))
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1600])
            return None
        return json.loads(proc.stdout)
    finally:
        entry.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)


data = run_js()
if data is None:
    failures.append("the panel half could not run")
else:
    fn = data["fn"]
    check("an empty named list is flagged", bool(fn["emptyList"]), repr(fn["emptyList"]))
    check("a 0% rollout is flagged", bool(fn["zeroPercent"]), repr(fn["zeroPercent"]))
    check("Everyone is not flagged", fn["everyone"] == "")
    check("a list with somebody on it is not flagged", fn["named"] == "")
    # ⚠ NOT FLAGGED, AND THAT IS DELIBERATE: "Admins only" already says on the
    # row exactly who it reaches. A warning on a setting that is describing
    # itself accurately is noise, and noise is what gets ignored.
    check("'admins only' is not flagged — the row already says it", fn["adminsOnly"] == "")
    check("a hidden feature is not flagged — it already says it is gone", fn["hidden"] == "")
    check("a feature with no rollout at all is not flagged", fn["noRollout"] == "")

    trap = data["render"]["trap"]
    check("the row says no customer can see it", "no customer can see it" in trap)
    check("…and says WHY", "nobody is on the list yet" in trap)
    check("…and says the admin's own view is the reason it looked fine",
          "admins pass every rollout" in trap)
    check("…and offers the one-click fix", "Show it to everyone" in trap)
    check("…and the row itself is marked", "unreachable" in trap)
    # ⚠ THE REASSURING SENTENCE MUST BE GONE, NOT MOVED. "Everyone in the
    # rollout can use it." printed above a warning is how this was missed.
    check("…and the summary line does NOT still claim everyone can use it",
          "Everyone in the rollout can use it" not in says_line(trap),
          says_line(trap)[:120])

    check("a 0% rollout gets the same treatment",
          "no customer can see it" in data["render"]["zero"]
          and "0%" in data["render"]["zero"])

    fine = data["render"]["fine"]
    check("a normal live row is not shouted at", "no customer can see it" not in fine)
    check("…and its summary line does say everyone can use it",
          "Everyone in the rollout can use it" in says_line(fine))
    check("a row with somebody named is not shouted at",
          "no customer can see it" not in data["render"]["named"])
    check("a hidden row is not shouted at either",
          "no customer can see it" not in data["render"]["hidden"])


# ===========================================================================
print("\n" + ("FAILED: " + "; ".join(failures) if failures
              else f"All workflow-reach checks passed ({len(ALL_WORKFLOWS)} workflows)."))
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if failures else 0)

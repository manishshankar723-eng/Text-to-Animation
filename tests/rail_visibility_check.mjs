// Proves a HIDDEN workflow never appears in the sidebar — not even for a frame.
//
// THE BUG THIS LOCKS DOWN. The rail has to be drawn on the first paint, before
// any request can have answered, and it used to be drawn from `WORKFLOWS` in
// Sidebar.jsx — the built-in list of every workflow that EXISTS. So an
// administrator who had hidden two of them watched both reappear for about a
// second on every single reload. A hidden feature that flashes up on every
// refresh is not hidden, so this is a correctness test, not a polish one.
//
// The fix has three moving parts and each is checked here:
//   1. a successful `/auth/me/entitlements` answer is REMEMBERED per account;
//   2. the first paint reads it SYNCHRONOUSLY, so frame one is already right;
//   3. an admin change FORGETS it at once, so the administrator who just hid
//      something does not see it one last time on their next reload.
//
// ⚠ IT HAS TO BE BUNDLED FIRST, and the flags are not optional:
//   --define:import.meta.env  `api.js` reads Vite's env object, which plain
//                             node has no such thing as.
//   --jsx=automatic           it imports Sidebar.jsx for the real `WORKFLOWS`
//                             array — reading the built-in list from a copy
//                             would let the copy drift and the test pass while
//                             the app was wrong.
//   --alias:@app              so the imports below name the app, not a path
//                             that changes with where the bundle is written.
//
//   cd client && npx esbuild ../tests/rail_visibility_check.mjs --bundle \
//     --format=esm --platform=node --jsx=automatic \
//     --outfile="$TMPDIR/rail.bundle.mjs" --alias:@app=./src \
//     --define:import.meta.env='{"VITE_API_BASE":"http://127.0.0.1:8000"}' \
//     && node "$TMPDIR/rail.bundle.mjs"
//
// (Write the bundle OUTSIDE the repo — it is a build artefact, not a source
// file, and nothing here should have to gitignore it.)

import assert from "node:assert";

// --- the browser bits the modules expect -------------------------------------
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

// The two workflows the administrator has hidden, and which must never be drawn.
const HIDDEN = ["create-animatic-image", "animatics-to-video"];

// What the server sends: the full list MINUS the hidden ones. This is the whole
// point — the server has already filtered, and the browser's job is to not
// second-guess it with a built-in list of its own.
const SERVER_WORKFLOWS = [
  { id: "plan-and-script", label: "Plan & Script", icon: "🗓️", status: "live" },
  { id: "text-to-image", label: "Text to Turnaround Image", icon: "🖼️", status: "live" },
  { id: "script-to-storyboard", label: "Script to Storyboard", icon: "📝", status: "live" },
  { id: "storyboard-to-animatics", label: "Video Editor", icon: "🎬", status: "live" },
];

let calls = [];
let entitlementsBody = { workflows: SERVER_WORKFLOWS, tier: "free", features: {} };
globalThis.fetch = async (url) => {
  const path = String(url);
  calls.push(path.replace("http://127.0.0.1:8000", ""));
  const body = path.includes("/auth/me/entitlements")
    ? entitlementsBody
    : path.includes("/auth/me")
      ? { display_name: "Admin", account_role: "admin" }
      : path.includes("/admin/")
        ? { ok: true, key: "workflow.create-animatic-image" }
        : [];
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => (h === "content-type" ? "application/json" : null) },
    json: async () => body,
  };
};

const api = await import("@app/api.js");
const cache = await import("@app/session_cache.js");
const { WORKFLOWS } = await import("@app/components/Sidebar.jsx");

let failed = 0;
const check = (label, fn) => {
  try {
    fn();
    console.log("    ok   " + label);
  } catch (e) {
    failed++;
    console.log("    FAIL " + label + "  " + e.message);
  }
};
const ids = (list) => (list || []).map((w) => w.id);
const reset = () => {
  cache.reset();
  store.clear();
  calls = [];
};

// -----------------------------------------------------------------------------
console.log("\n  the premise");
check("the built-in list still contains the hidden workflows", () => {
  for (const h of HIDDEN) {
    assert.ok(
      ids(WORKFLOWS).includes(h),
      `${h} is not in WORKFLOWS — this test is checking nothing`
    );
  }
});
check("the server's answer does not", () => {
  for (const h of HIDDEN) assert.ok(!ids(SERVER_WORKFLOWS).includes(h));
});

// -----------------------------------------------------------------------------
console.log("\n  1. signing in remembers what the server said");
reset();
api.setSession("tok", "admin@studio.test");
cache.prefetch({ email: "admin@studio.test", counts: { animatic: 3 } });
await cache.ensure("entitlements");

check("the answer was stored", () =>
  assert.ok(api.getRememberedEntitlements("admin@studio.test"))
);
check("and it does NOT contain the hidden workflows", () => {
  const got = ids(api.getRememberedEntitlements("admin@studio.test").workflows);
  for (const h of HIDDEN) assert.ok(!got.includes(h), `${h} was remembered`);
});

// -----------------------------------------------------------------------------
console.log("\n  2. RELOAD — the first paint, before any request answers");
// A reload is: module state gone, localStorage kept, token still there.
cache.reset();
calls = [];

const firstPaint = cache.rememberedEntitlements();
check("the rail is known SYNCHRONOUSLY, with no request made", () => {
  assert.ok(firstPaint, "nothing to draw — the rail would fall back to WORKFLOWS");
  assert.deepStrictEqual(calls, [], `it made a request first: ${calls}`);
});
check("⚠ THE HIDDEN WORKFLOWS ARE ABSENT FROM FRAME ONE", () => {
  const drawn = ids(firstPaint.workflows);
  for (const h of HIDDEN) {
    assert.ok(!drawn.includes(h), `${h} would flash up on this reload`);
  }
});
check("and the rest of the rail is intact", () =>
  assert.deepStrictEqual(ids(firstPaint.workflows), ids(SERVER_WORKFLOWS))
);
check("the tier rides along, so that does not flash either", () =>
  assert.strictEqual(firstPaint.tier, "free")
);

// -----------------------------------------------------------------------------
console.log("\n  3. the administrator hides one MORE workflow");
reset();
api.setSession("tok", "admin@studio.test");
cache.prefetch({ email: "admin@studio.test" });
await cache.ensure("entitlements");
assert.ok(api.getRememberedEntitlements("admin@studio.test"));

// From here the server answers with one fewer workflow.
const NARROWER = SERVER_WORKFLOWS.filter((w) => w.id !== "script-to-storyboard");
entitlementsBody = { workflows: NARROWER, tier: "free", features: {} };

await api.adminUpdateFeature("workflow.script-to-storyboard", { visible: false });
// The invalidation kicks off a re-read; wait for it to settle.
await cache.ensure("entitlements");

check("the admin call re-read the entitlements", () =>
  assert.ok(
    calls.filter((c) => c.includes("/auth/me/entitlements")).length >= 2,
    `entitlements was fetched ${calls.filter((c) => c.includes("entitlements")).length}×`
  )
);
check("⚠ THE NEXT RELOAD WOULD NOT SHOW THE JUST-HIDDEN WORKFLOW", () => {
  const drawn = ids(cache.rememberedEntitlements().workflows);
  assert.ok(
    !drawn.includes("script-to-storyboard"),
    "the stale answer survived the admin change — it would flash once more"
  );
});
const adminResult = await api.adminUpdateFeature("workflow.x", { visible: false });
check("`.then(entitlementsChanged)` did not swallow the response", () =>
  assert.ok(adminResult && adminResult.ok, `got ${JSON.stringify(adminResult)}`)
);
entitlementsBody = { workflows: SERVER_WORKFLOWS, tier: "free", features: {} };

// -----------------------------------------------------------------------------
console.log("\n  4. the fail-open rules still hold");
reset();
api.setSession("tok", "nobody@studio.test");
check("a browser that has never had an answer knows nothing", () =>
  assert.strictEqual(cache.rememberedEntitlements(), null)
);
check("an answer with NO workflows is never remembered", () => {
  api.rememberEntitlements("nobody@studio.test", { workflows: [], tier: "free" });
  assert.strictEqual(api.getRememberedEntitlements("nobody@studio.test"), null);
});

// -----------------------------------------------------------------------------
console.log("\n  5. it belongs to the account, and leaves with it");
reset();
api.setSession("tokA", "a@studio.test");
api.rememberEntitlements("a@studio.test", {
  workflows: [{ id: "plan-and-script", label: "P", icon: "x", status: "live" }],
  tier: "pro",
});
api.setSession("tokB", "b@studio.test");
check("account B does not inherit account A's rail", () =>
  assert.strictEqual(api.getRememberedEntitlements("b@studio.test"), null)
);
check("account A's is still its own", () =>
  assert.ok(api.getRememberedEntitlements("a@studio.test"))
);

api.setSession("tokA", "a@studio.test");
api.clearSession();
check("signing out forgets it", () =>
  assert.strictEqual(api.getRememberedEntitlements("a@studio.test"), null)
);

console.log("\n" + (failed ? `${failed} FAILURE(S)` : "ALL PASS"));
process.exit(failed ? 1 : 0);

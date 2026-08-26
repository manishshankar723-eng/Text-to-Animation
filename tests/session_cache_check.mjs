// The four promises `client/src/session_cache.js` makes, checked against the
// real module with `fetch` and `localStorage` stubbed.
//
// That module is what turned signing in from "mount the dashboard, THEN start
// asking" into "ask at the moment of authentication, and keep the answers". The
// promises are worth pinning down because every one of them is invisible when
// it breaks — the app still works, it just quietly goes back to being slow, or
// quietly shows one customer's library to the next:
//
//   1. ONE REQUEST PER FEED. Callers that arrive mid-flight join the promise
//      already running; they never start a second one.
//   2. WHAT WE HAVE IS READABLE SYNCHRONOUSLY, so a component's FIRST render can
//      have real content and never draw a loader at all.
//   3. AN ACCOUNT THE SERVER SAID IS EMPTY NEVER WAITS — and an account we know
//      nothing about is not assumed to be empty.
//   4. NOTHING SURVIVES AN ACCOUNT CHANGE, including answers still in the air.
//
// ⚠ IT HAS TO BE BUNDLED FIRST — `api.js` reads Vite's `import.meta.env`, which
// plain node has no such thing as. Write the bundle OUTSIDE the repo; it is a
// build artefact, not a source file:
//
//   cd client && npx esbuild ../tests/session_cache_check.mjs --bundle \
//     --format=esm --platform=node --outfile="$TMPDIR/sc.bundle.mjs" \
//     --alias:@app=./src \
//     --define:import.meta.env='{"VITE_API_BASE":"http://127.0.0.1:8000"}' \
//     && node "$TMPDIR/sc.bundle.mjs"

import assert from "node:assert";

// --- the browser bits the modules expect -------------------------------------
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

let calls = [];
let delayMs = 0;
globalThis.fetch = async (url) => {
  const path = String(url);
  calls.push(path.replace("http://127.0.0.1:8000", ""));
  if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
  const body = path.includes("/auth/me/entitlements")
    ? {
        workflows: [{ id: "plan-and-script", label: "P", icon: "x", status: "live" }],
        tier: "free",
      }
    : path.includes("/auth/me")
      ? { display_name: "Tester", account_role: "user" }
      : [{ job_id: "x1" }];
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => (h === "content-type" ? "application/json" : null) },
    json: async () => body,
  };
};

const api = await import("@app/api.js");
const cache = await import("@app/session_cache.js");

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
const reset = () => {
  cache.reset();
  store.clear();
  calls = [];
};
const listCalls = () => calls.filter((u) => !u.startsWith("/auth/"));

// -----------------------------------------------------------------------------
console.log("\n  1. a returning account: one request per feed, no duplicates");
reset();
api.setSession("tok", "a@b.c");
cache.prefetch({ email: "a@b.c", counts: { animatic: 5 } });
// Three more callers pile in while everything is still in flight — which is
// exactly what happens when Home mounts a few milliseconds after Login fired.
cache.revalidate();
cache.revalidate();
await Promise.all(cache.FEED_KEYS.map((k) => cache.ensure(k)));

check("every feed fetched exactly once", () => {
  const counted = {};
  for (const u of calls) counted[u] = (counted[u] || 0) + 1;
  const dupes = Object.entries(counted).filter(([, n]) => n > 1);
  assert.deepStrictEqual(dupes, [], `duplicated: ${JSON.stringify(dupes)}`);
});
check("8 feeds -> 8 requests", () =>
  assert.strictEqual(calls.length, 8, `got ${calls.length}: ${calls}`)
);
check("the dashboard asked for a PAGE, not the whole library", () =>
  assert.ok(
    listCalls().every((u) => u.includes(`limit=${cache.DASH_LIMIT}`)),
    listCalls().join(" ")
  )
);
check("reads are synchronous once landed", () =>
  assert.ok(Array.isArray(cache.read("animatics")))
);
check("every list has landed", () =>
  assert.ok(cache.LIST_KEYS.every((k) => cache.hasLanded(k)))
);

// -----------------------------------------------------------------------------
console.log("\n  2. a brand-new account: seeded, so nothing shimmers");
reset();
api.setSession("tok2", "new@b.c");
cache.prefetch({ email: "new@b.c", counts: {} });
check("recognised as new", () => assert.strictEqual(cache.isNewAccount(), true));
check("every list is readable IMMEDIATELY", () =>
  assert.ok(
    cache.LIST_KEYS.every(
      (k) => Array.isArray(cache.read(k)) && cache.read(k).length === 0
    )
  )
);
check("and no list request was made for it", () =>
  assert.deepStrictEqual(listCalls(), [], listCalls().join(" "))
);

// -----------------------------------------------------------------------------
console.log("\n  3. reload: the hint comes back with no login call");
cache.reset();
calls = [];
check("hint recovered from storage", () =>
  assert.deepStrictEqual(cache.hint(), {}, JSON.stringify(cache.hint()))
);
check("still recognised as new", () =>
  assert.strictEqual(cache.isNewAccount(), true)
);

// -----------------------------------------------------------------------------
console.log("\n  4. no hint at all -> wait, do not assume");
reset();
api.setSession("tok3", "unknown@b.c");
check("hint is null, NOT {}", () => assert.strictEqual(cache.hint(), null));
check("an unknown account is not treated as a new one", () =>
  assert.strictEqual(cache.isNewAccount(), false)
);
check("nothing has landed, so the screen waits", () =>
  assert.ok(!cache.LIST_KEYS.every((k) => cache.hasLanded(k)))
);

// -----------------------------------------------------------------------------
console.log("\n  5. the entitlements answer is remembered for the next paint");
reset();
api.setSession("tok", "rail@b.c");
check("nothing remembered before the first answer", () =>
  assert.strictEqual(cache.rememberedEntitlements(), null)
);
cache.prefetch({ email: "rail@b.c" });
await cache.ensure("entitlements");
check("it is remembered once the answer lands", () =>
  assert.ok(cache.rememberedEntitlements()?.workflows?.length)
);
cache.reset(); // a reload: module state gone, storage kept
calls = [];
check("and survives a reload, synchronously, with no request", () => {
  assert.ok(cache.rememberedEntitlements()?.workflows?.length);
  assert.deepStrictEqual(calls, [], `it fetched first: ${calls}`);
});
// ⚠ AWAITED, and the first draft of this check was not — it asserted straight
// after `prefetch` and failed, because `run()` starts its fetcher in a
// MICROTASK. That is the module behaving correctly: the synchronous half of
// `prefetch` is deliberately only the seeding, so a caller can read and paint
// before anything touches the network.
cache.prefetch({ email: "rail@b.c" });
await cache.ensure("entitlements");
check("⚠ SEEDED WITHOUT A TIMESTAMP, so it is always re-read", () =>
  assert.ok(
    calls.some((c) => c.includes("/auth/me/entitlements")),
    "a remembered rail was believed without ever being checked against the server"
  )
);
check("the re-read replaced the seed with a real answer", () =>
  assert.ok(cache.hasLanded("entitlements") && !cache.errorOf("entitlements"))
);

// -----------------------------------------------------------------------------
console.log("\n  6. switching account mid-flight: the old answers are dropped");
reset();
api.setSession("tokA", "one@b.c");
delayMs = 60;
const inFlight = cache.revalidate();
cache.reset(); // the switch happens while all eight are in the air
api.setSession("tokB", "two@b.c");
await inFlight;
delayMs = 0;
check("no stale data landed in the new session", () =>
  assert.ok(
    cache.FEED_KEYS.every((k) => !cache.hasLanded(k)),
    "a previous account's answer was written after the reset"
  )
);

// -----------------------------------------------------------------------------
console.log("\n  7. a failed refresh must not blank the screen");
reset();
api.setSession("tok", "a@b.c");
await cache.ensure("animatics");
const good = cache.read("animatics");
check("we have something to lose", () => assert.ok(Array.isArray(good)));
const okFetch = globalThis.fetch;
globalThis.fetch = async () => {
  throw new TypeError("Failed to fetch");
};
await cache.refresh(["animatics"]);
globalThis.fetch = okFetch;
check("the last good value is still there", () =>
  assert.deepStrictEqual(cache.read("animatics"), good)
);
check("and the failure is recorded beside it", () =>
  assert.ok(cache.errorOf("animatics").length > 0, cache.errorOf("animatics"))
);

console.log("\n" + (failed ? `${failed} FAILURE(S)` : "ALL PASS"));
process.exit(failed ? 1 : 0);

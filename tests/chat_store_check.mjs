// The browser half of the ✨ AI Editor's many chats: `chat_sessions.js`.
//
// The server half is `tests/chat_sessions_check.py`. This is the other side of
// the line that module's header draws:
//
//     SERVER   the chats themselves — the work, so it follows the account.
//     BROWSER  which chat was open, and a mirror of that one chat's turns.
//
// What it guards, in rough order of how much it would hurt to get wrong:
//
//   1. THE OLD TRANSCRIPT IS RESCUED, NOT ORPHANED. Every existing user has a
//      conversation sitting under the v1 key. A migration that missed it would
//      look, to the person who typed it, exactly like the new feature deleted
//      their chat history — and `forgetLegacy` must be a SEPARATE call, so a
//      failed upload cannot lose it.
//   2. A PLAN IS NEVER MIRRORED. `toStore` drops unapplied steps and marks the
//      turn `stale`, because a stale plan is not a saving, it is an Apply button
//      that would run against a timeline the user has since edited.
//   3. THE MIRROR SWEEP ACTUALLY SWEEPS. Deleting a chat must not leave its copy
//      in the browser for ever — and the walk is BACKWARDS, because removing a
//      key shifts every index after it.
//   4. NAMING. The first thing the PERSON said, cut on a word, never the agent's
//      opening line — which is near-identical in every chat about one film.
//   5. NOTHING THROWS WHEN STORAGE IS BLOCKED. Private mode still gets a working
//      chat; it just does not paint instantly.
//
// ⚠ NO BUNDLER NEEDED. `chat_sessions.js` imports nothing, so plain node loads
// it as-is once `localStorage` exists:
//
//   node tests/chat_store_check.mjs

import assert from "node:assert";

// --- the browser bit the module expects --------------------------------------
// ⚠ `length` AND `key(i)` ARE PART OF IT. The sweep walks the store by index;
// a stub without them would pass while the real sweep did nothing at all.
let store = new Map();
let blocked = false;

function makeStorage() {
  const guard = () => {
    if (blocked) throw new Error("storage is blocked");
  };
  return {
    get length() {
      guard();
      return store.size;
    },
    key(i) {
      guard();
      return [...store.keys()][i] ?? null;
    },
    getItem(k) {
      guard();
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      guard();
      store.set(k, String(v));
    },
    removeItem(k) {
      guard();
      store.delete(k);
    },
  };
}

globalThis.localStorage = makeStorage();

const {
  MAX_KEPT,
  UNTITLED,
  agoLabel,
  forgetLegacy,
  forgetMirror,
  isFull,
  labelFor,
  readLegacy,
  readMirror,
  readOpen,
  sweepMirrors,
  titleFor,
  toStore,
  writeMirror,
  writeOpen,
} = await import("../client/src/animatic/agent/chat_sessions.js");

const failures = [];

function check(label, ok, detail = "") {
  console.log(`  ${ok ? "ok  " : "FAIL"}   ${label}${ok || !detail ? "" : ` — ${detail}`}`);
  if (!ok) failures.push(label);
}

const user = (text) => ({ id: `u${text.length}`, role: "user", kind: "text", text });
const agent = (text) => ({ id: `a${text.length}`, role: "agent", kind: "answer", text });

// ===========================================================================
console.log("\n[1] naming a chat\n");
// ===========================================================================
check("a chat with nothing in it has no name", titleFor([]) === "");
check(
  "…and draws as its placeholder",
  labelFor({ title: "" }) === UNTITLED && labelFor(null) === UNTITLED,
  labelFor(null)
);
check(
  "the name is the first thing the PERSON said",
  titleFor([user("add sound effects"), agent("A devotional family film…")]) ===
    "add sound effects"
);
// ⚠ THE ORDER MATTERS AND THE AGENT SPEAKS FIRST IN A LOOK. Its opening line is
// about the FILM, so it is near-identical in every chat about one project — a
// list of those is a list of rows nobody can tell apart.
check(
  "…even when the agent spoke first",
  titleFor([agent("A devotional family film for Ganesh Chaturthi"), user("cut shot 4")]) ===
    "cut shot 4"
);
check("…and whitespace is collapsed", titleFor([user("  add\n\n sound  ")]) === "add sound");
{
  const source = "please add sound effects and background music across the whole story";
  const long = titleFor([user(source)]);
  check("a long line is cut", long.length <= 49, `${long.length}: ${long}`);
  check("…and says it was cut", long.endsWith("…"), long);
  // ⚠ THE LAST WORD IS WHOLE. Not "does it end in a letter" — it does, and
  // should — but "does the source carry on with a SPACE where this stopped".
  // A row is one line in a narrow panel, and "…background music acr…" is a
  // title that has to be read twice.
  const body = long.slice(0, -1);
  const after = source.slice(body.length, body.length + 1);
  check(
    "…on a word, not mid-word",
    source.startsWith(body) && (after === "" || after === " "),
    `cut to "${body}", source carries on with "${after}"`
  );
}
check("an empty message is not a name", titleFor([user("   "), user("real one")]) === "real one");

// ===========================================================================
console.log("\n[1b] ⚠ IS THERE ROOM FOR ANOTHER CHAT — asked BEFORE ＋ opens one\n");
// ===========================================================================
// ⚠ THIS SHIPPED MISSING AND IT WAS REPORTED FROM A LIVE DEPLOYMENT. With the
// operator's ceiling set to 1 and one real chat already saved, ＋ answered with a
// cheerful empty "New chat" — and the refusal only arrived once a whole message
// had been typed and the autosave came back 409: *"maine admin panel mai ek
// likha, to yaha pe new chat open hua — kya ye sahi hai?"*. It was not.
{
  const said = (n) => ({ session_id: `s${n}`, title: `chat ${n}`, turn_count: 3 });
  const blank = (n) => ({ session_id: `b${n}`, title: "", turn_count: 0 });

  check("room below the ceiling", isFull([said(1)], 3) === false);
  check("⚠ no room AT the ceiling", isFull([said(1), said(2)], 2) === true);
  check("…and none past it either", isFull([said(1), said(2), said(3)], 2) === true);

  // ⚠ `length >= limit` IS NOT THE WHOLE ANSWER. The server sweeps chats nobody
  // ever typed in BEFORE it refuses, so an empty row is what makes the room.
  // Answering "full" here would grey out ＋ on a project the server would have
  // taken another chat for.
  check(
    "⚠ an empty chat among them still counts as room",
    isFull([said(1), blank(2)], 2) === false
  );
  check(
    "…even well past the ceiling",
    isFull([said(1), said(2), blank(3)], 2) === false
  );

  // ⚠ 0 IS "NO CEILING", NEVER "NO ROOM" — the same rule the server enforces,
  // and the same trap `opacity: 0` fell into: a zero read as falsey.
  check("⚠ a ceiling of 0 is no ceiling at all", isFull([said(1), said(2)], 0) === false);
  check("…and so is a missing one", isFull([said(1)], undefined) === false);
  check("an empty project has room", isFull([], 1) === false);
  check("a missing list is not full", isFull(undefined, 5) === false);
}

// ===========================================================================
console.log("\n[2] how long ago\n");
// ===========================================================================
const NOW = Date.parse("2026-09-05T12:00:00Z");
const ago = (ms) => agoLabel(new Date(NOW - ms).toISOString(), NOW);
// ⚠ NEVER "0m". A chat saved four seconds ago reading "0m" looks like a broken
// clock, and this list is mostly read seconds after a save.
check("under a minute is 'now'", ago(4000) === "now", ago(4000));
check("minutes", ago(12 * 60000) === "12m", ago(12 * 60000));
check("hours", ago(3 * 3600e3) === "3h", ago(3 * 3600e3));
check("days", ago(5 * 86400e3) === "5d", ago(5 * 86400e3));
check("older than a week becomes a date", !/^\d+d$/.test(ago(30 * 86400e3)), ago(30 * 86400e3));
check("no timestamp is no label", agoLabel("") === "" && agoLabel("nonsense") === "");

// ===========================================================================
console.log("\n[3] ⚠ WHAT IS MIRRORED IS NOT WHAT IS DRAWN\n");
// ===========================================================================
{
  const planTurn = {
    id: "p1",
    role: "agent",
    kind: "plan",
    text: "13 transitions",
    steps: [{ verb: "add_transition", args: { after: 1 } }],
    passes: [{ door: "animate", why: "shot 3 is a still" }],
  };
  const [kept] = toStore([planTurn]);
  check("an unapplied plan keeps its words", kept.text === "13 transitions");
  check("⚠ …but NOT its steps — a stale plan is a trap", kept.steps === undefined);
  check("…and it is marked stale so the bubble can say so", kept.stale === true);

  const [applied] = toStore([{ ...planTurn, applied: true, steps: 4 }]);
  check("a plan that WAS applied is remembered as a fact", applied.steps === 4);

  // ⚠ AN OFFER SURVIVES AND A PLAN DOES NOT, and the difference is what the
  // button does: a door button re-reads the film NOW and prices it there.
  check("a paid-work offer survives", Array.isArray(kept.passes) && kept.passes.length === 1);

  const many = Array.from({ length: MAX_KEPT + 25 }, (_, i) => user(`m${i}`));
  check(`only the last ${MAX_KEPT} are kept`, toStore(many).length === MAX_KEPT);
  check(
    "…and they are the NEWEST ones",
    toStore(many)[MAX_KEPT - 1].text === `m${MAX_KEPT + 24}`
  );
}

// ===========================================================================
console.log("\n[4] the mirror, and which chat was open\n");
// ===========================================================================
store = new Map();
writeOpen("film1", "sessA");
check("the open chat is remembered", readOpen("film1") === "sessA");
check("…per project, not globally", readOpen("film2") === "");
writeOpen("film1", "");
check("…and can be forgotten", readOpen("film1") === "");

writeMirror("film1", "sessA", [user("hello"), agent("hi")]);
check("a mirrored chat reads back", readMirror("film1", "sessA")?.length === 2);
check("…and an unmirrored one is null, not []", readMirror("film1", "sessZ") === null);
forgetMirror("film1", "sessA");
check("…and it can be dropped", readMirror("film1", "sessA") === null);

// ===========================================================================
console.log("\n[5] ⚠ THE SWEEP — a deleted chat does not haunt the browser\n");
// ===========================================================================
store = new Map();
writeMirror("film1", "a", [user("one")]);
writeMirror("film1", "b", [user("two")]);
writeMirror("film1", "c", [user("three")]);
writeMirror("film2", "z", [user("other film")]);
check("four mirrors exist", store.size === 4, String(store.size));
const removed = sweepMirrors("film1", ["a", "c"]);
check("the one no longer in the list is swept", removed === 1, String(removed));
check("…the kept ones survive", readMirror("film1", "a") !== null && readMirror("film1", "c") !== null);
check("…the swept one is gone", readMirror("film1", "b") === null);
// ⚠ ANOTHER PROJECT'S MIRRORS ARE NOT THIS PROJECT'S BUSINESS. A sweep keyed on
// the bare prefix would empty every other film's cache on every list refresh.
check("…and ANOTHER project is untouched", readMirror("film2", "z") !== null);

// A sweep that keeps nothing must actually remove all of them — the backwards
// walk is what makes this pass; a forward one skips every other key.
store = new Map();
["p", "q", "r", "s"].forEach((s) => writeMirror("film1", s, [user(s)]));
check("a sweep that keeps nothing removes all of them", sweepMirrors("film1", []) === 4);
check("…and the store is empty", store.size === 0, String(store.size));

// ===========================================================================
console.log("\n[6] ⚠ THE OLD SINGLE TRANSCRIPT IS RESCUED, NOT ORPHANED\n");
// ===========================================================================
store = new Map();
check("no legacy chat is null, not []", readLegacy("film1") === null);
store.set(
  "aniwala.editorChat.v1.film1",
  JSON.stringify([user("add sound effects"), agent("done")])
);
const legacy = readLegacy("film1");
check("the v1 store is found", legacy?.length === 2);
check("…and it can be named", titleFor(legacy) === "add sound effects");
// ⚠ READING IT DOES NOT DELETE IT. The caller drops it only once the rescued
// turns are actually on the server — a read that deleted as it went would lose
// the conversation to any failure in between, and a network is at its most
// likely to fail exactly there.
check("⚠ …and READING it did not delete it", readLegacy("film1")?.length === 2);
forgetLegacy("film1");
check("…only forgetting does", readLegacy("film1") === null);
store.set("aniwala.editorChat.v1.film1", "{ this is not json");
check("a corrupt v1 store is not a crash", readLegacy("film1") === null);
store.set("aniwala.editorChat.v1.film1", "[]");
check("an EMPTY v1 store is nothing to rescue", readLegacy("film1") === null);

// ===========================================================================
console.log("\n[7] blocked storage is not a crash\n");
// ===========================================================================
store = new Map();
blocked = true;
assert.doesNotThrow(() => {
  writeOpen("film1", "sessA");
  writeMirror("film1", "sessA", [user("hi")]);
  forgetMirror("film1", "sessA");
  forgetLegacy("film1");
  sweepMirrors("film1", []);
});
check("every write survives blocked storage", true);
check("…and every read gives the default back", readOpen("film1") === "");
check("…including the mirror", readMirror("film1", "sessA") === null);
check("…and the legacy read", readLegacy("film1") === null);
blocked = false;

console.log();
if (failures.length) {
  console.log(`FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("✓ the browser half holds: naming, the mirror, the sweep and the rescue.");

// The four promises `ScriptChat.jsx`'s session helpers make, checked against the
// real module with `localStorage` stubbed.
//
// The chat inside Script → Storyboard's script box is stored in the browser and
// nowhere else, so these helpers are the whole of "which conversation am I in".
// Every promise is invisible when it breaks — the chat still works, it is just
// talking about the wrong film, or quietly leaking keys into storage:
//
//   1. AN ID SURVIVES A REFRESH. It is read back from storage, not re-minted,
//      or the transcript it was written under is orphaned on every mount.
//   2. A NEW STORYBOARD IS A NEW CHAT. `resetScriptChat()` changes the id AND
//      deletes what the old one was holding.
//   3. NOTHING ACCUMULATES. Every stored transcript is swept on reset, not just
//      the outgoing one, so a key orphaned by a crash cannot pile up.
//   4. BLOCKED STORAGE IS NOT A CRASH. Private mode still gets a working chat;
//      it just doesn't survive a refresh.
//
// ⚠ IT HAS TO BE BUNDLED FIRST — the module imports `api.js`, which reads Vite's
// `import.meta.env`, and plain node has no such thing. Write the bundle OUTSIDE
// the repo; it is a build artefact, not a source file:
//
//   cd client && npx esbuild ../tests/script_chat_session_check.mjs --bundle \
//     --format=esm --platform=node --jsx=automatic \
//     --outfile="$TEMP/scs.bundle.mjs" \
//     --define:import.meta.env='{"VITE_API_BASE":"http://127.0.0.1:8000"}' \
//     && node "$TEMP/scs.bundle.mjs"
//
// ⚠ `--jsx=automatic` IS NOT OPTIONAL. Vite uses the automatic runtime, so no
// component in this app imports React by name; bundled with esbuild's classic
// default, the first JSX file thrown by the loader is `Icon.jsx` with
// "React is not defined" — a failure about a module this test never touches.

import assert from "node:assert";

// --- the browser bits the module expects -------------------------------------
// `length` and `key(i)` are part of it: the sweep walks the store backwards, and
// a stub without them would pass while the real sweep did nothing.
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
    getItem: (k) => {
      guard();
      return store.has(k) ? store.get(k) : null;
    },
    setItem: (k, v) => {
      guard();
      store.set(k, String(v));
    },
    removeItem: (k) => {
      guard();
      store.delete(k);
    },
  };
}
globalThis.localStorage = makeStorage();

const { currentScriptChatSession, resetScriptChat } = await import(
  "../client/src/components/ScriptChat.jsx"
);

// The storage layout IS the contract between this module and any future reader
// of it, so the test names it rather than asking the module what it uses.
const PREFIX = "aniwala.scriptChat.v2.";
const SESSION_KEY = "aniwala.scriptChatSession.v1";
const LEGACY_KEY = "aniwala.scriptChat.v1";

const failures = [];
function check(label, got, want = true) {
  const good = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${good ? "ok  " : "FAIL"} ${label}${good ? "" : `  (got ${JSON.stringify(got)})`}`);
  if (!good) failures.push(label);
}

console.log("\n[1] an id survives a refresh");
store = new Map();
const first = currentScriptChatSession();
check("minted something", typeof first === "string" && first.length > 0);
check("wrote it down", store.get(SESSION_KEY), first);
check("a second call reads it back rather than minting", currentScriptChatSession(), first);

console.log("\n[2] a new storyboard is a new chat");
store.set(PREFIX + first, JSON.stringify([{ role: "user", text: "old film" }]));
const second = resetScriptChat();
check("the id changed", second !== first);
check("the new id is stored", store.get(SESSION_KEY), second);
check("the old transcript is gone", store.has(PREFIX + first), false);
check("and the new session starts empty", store.has(PREFIX + second), false);

console.log("\n[3] nothing accumulates");
store.set(PREFIX + "orphan-a", "[]");
store.set(PREFIX + "orphan-b", "[]");
store.set(PREFIX + second, "[]");
store.set("aniwala.somethingElse", "keep me");
resetScriptChat();
check("every transcript was swept",
      [...store.keys()].filter((k) => k.startsWith(PREFIX)).length, 0);
check("an unrelated key was left alone", store.get("aniwala.somethingElse"), "keep me");

console.log("\n[4] the pre-session key is dropped");
store.set(LEGACY_KEY, JSON.stringify([{ role: "user", text: "from the old build" }]));
currentScriptChatSession();
check("legacy transcript removed", store.has(LEGACY_KEY), false);

console.log("\n[5] blocked storage is not a crash");
blocked = true;
let id;
assert.doesNotThrow(() => {
  id = currentScriptChatSession();
});
check("still handed back an id", typeof id === "string" && id.length > 0);
assert.doesNotThrow(() => {
  id = resetScriptChat();
});
check("reset still handed back an id", typeof id === "string" && id.length > 0);
blocked = false;

console.log();
if (failures.length) {
  console.log(`FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("All script-chat session checks passed.");

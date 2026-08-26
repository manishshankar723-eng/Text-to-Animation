// session_cache.js — THE BOOT READS, FETCHED ONCE PER SESSION AND KEPT.
//
// ⚠ WHAT THIS FIXES, because the shape of the module only makes sense next to
// it: signing in used to be "hand React a token, let it mount Home, and let
// Home start asking". Nine requests, every one of them beginning AFTER the
// dashboard was already on screen, so the first thing a returning customer saw
// was their own empty dashboard with the word "Loading…" in six places. Then
// they clicked a workflow and came back, and every one of those nine ran again,
// because nothing anywhere remembered the answer.
//
// So: the fetch starts at AUTH — inside `Login`'s submit, one line after the
// token is written, before React has been told anything changed — and the
// answers live here, at module scope, for as long as the session does.
//
// THREE RULES THIS FILE EXISTS TO KEEP:
//
//   1. ONE REQUEST PER FEED, EVER, per session. A component mounting while a
//      fetch is in flight awaits THAT promise; it does not start a second one.
//      This is why `_pending` is keyed and why `ensure` checks it first.
//   2. WHAT WE HAVE IS SHOWN IMMEDIATELY. `read()` is SYNCHRONOUS, so a
//      component can put real content in its very first render and never show
//      a loader at all. A refresh happens behind the already-drawn screen —
//      stale-while-revalidate, which is what every mail client does and what
//      makes an inbox feel instant when it is not.
//   3. NOTHING SURVIVES AN ACCOUNT CHANGE. See `reset` and `_epoch`.
//
// ⚠ THIS IS THE DASHBOARD'S CACHE, NOT A GENERAL DATA LAYER. Its feeds are
// deliberately SHORT lists — `DASH_LIMIT`, not the hundred a library asks for.
// Do not point a library screen at it: it would be handed a page and think it
// had everything. The libraries keep their own full-fat reads.

import * as api from "./api.js";

// How many items each dashboard feed asks for. Home prints two per workflow;
// eight leaves room for the "View all" count to still be EXACT for most
// accounts — a page that comes back short of this is the whole library, which
// is how `totalFor` in Home.jsx knows when it may print a number — while
// keeping every one of these requests small.
//
// ⚠ RAISING THIS RAISES WHAT EVERY SIGN-IN COSTS. It is the number that turned
// "fetch a hundred projects to print two" back into something proportionate.
export const DASH_LIMIT = 8;

// Each feed is a name and the one call that fills it. Nothing else in this file
// knows what any of them mean — Home does the interpreting.
const FEEDS = {
  me: () => api.me(),
  entitlements: () => api.entitlements(),
  jobs: () => api.listJobs(api.CHARACTER_JOB_KINDS, DASH_LIMIT),
  // TWO board feeds, because the two board workflows own different sets: Script
  // to Storyboard has the originals (untagged), Image to Animatic Image has its
  // own copies. Same split the server makes — see list_storyboards.
  boards: () => api.listStoryboards("", DASH_LIMIT),
  copiedBoards: () => api.listStoryboards("animatic-image", DASH_LIMIT),
  animatics: () => api.listAnimatics(DASH_LIMIT),
  videos: () => api.listFinalVideos(DASH_LIMIT),
  plans: () => api.listPlans(DASH_LIMIT),
};

export const FEED_KEYS = Object.keys(FEEDS);

/**
 * The rail the server last sent this account, or `null`.
 *
 * ⚠ THE SHELL READS THIS SYNCHRONOUSLY, IN A `useState` INITIALISER, so the
 * sidebar's FIRST render already has the right rows. Anything later than that
 * is a frame with the wrong list in it, which is the bug.
 */
export function rememberedEntitlements() {
  return (
    _data.entitlements || api.getRememberedEntitlements(api.getEmail()) || null
  );
}

// The lists, as opposed to `me` / `entitlements`. Home waits on these; the
// shell waits on the other two. Kept apart so neither has to know the other's
// business — and so a slow entitlements call cannot hold up "Recent work".
export const LIST_KEYS = FEED_KEYS.filter(
  (k) => k !== "me" && k !== "entitlements"
);

// After this long, a cached answer is still SHOWN but is refreshed behind the
// screen on the next mount. Sixty seconds is chosen against how this app is
// actually used: work is created by a job that takes minutes, so a minute-old
// library is not a wrong library — and the workflows that DO change second by
// second (a running render) poll for themselves and always did.
const STALE_AFTER_MS = 60_000;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
// `_owner` is the account this cache belongs to and `_epoch` is bumped by every
// reset. Together they are the answer to the race that matters here: switching
// account while eight requests are in the air. Each of those requests resolves
// against the epoch it was STARTED in, and a resolution from an older epoch is
// dropped on the floor — so the previous customer's library can never land in
// the new one's dashboard, however the timing falls.
let _owner = null;
let _epoch = 0;
let _data = {}; // key → value
let _at = {}; // key → when it landed (ms)
let _error = {}; // key → message, cleared by the next success
let _pending = {}; // key → in-flight promise
let _counts = null; // the login hint; see api.getWorkCounts

const _listeners = new Set();

function emit() {
  for (const fn of _listeners) {
    try {
      fn();
    } catch {
      // A subscriber that throws must not stop the others being told.
    }
  }
}

/** Re-render on any change. Returns the unsubscribe. */
export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------
/** The cached value, or `undefined` if we have never had one. SYNCHRONOUS. */
export function read(key) {
  return _data[key];
}

export function errorOf(key) {
  return _error[key] || "";
}

/** Has this feed ever answered? (An empty list still counts as an answer.) */
export function hasLanded(key) {
  return key in _data;
}

/** Is a request for this feed in the air right now? */
export function isPending(key) {
  return Boolean(_pending[key]);
}

function isStale(key) {
  const at = _at[key];
  return !at || Date.now() - at > STALE_AFTER_MS;
}

// ---------------------------------------------------------------------------
// The login hint — new account or returning one?
// ---------------------------------------------------------------------------
/**
 * `{kind: n}` from the sign-in that started this session, or `null`.
 *
 * ⚠ `null` AND `{}` ARE DIFFERENT ANSWERS. `{}` means the server counted and
 * found nothing — paint the empty dashboard, skip the loaders. `null` means
 * nobody has told us, so behave exactly as the app did before this existed.
 * See `api.getWorkCounts`, which keeps the same distinction in storage.
 *
 * ⚠ IT READS STORAGE ON DEMAND, and does NOT rely on `prefetch` having run
 * first. React runs a child's effects before its parent's, so on a reload the
 * dashboard mounts and asks this question BEFORE the shell's effect has had a
 * chance to call `prefetch` - and a hint that only existed after prefetch would
 * therefore be missing at the exact moment it is needed. Reading it here makes
 * the answer independent of who woke up first.
 */
export function hint() {
  if (_counts === null) {
    const who = api.getEmail();
    if (who && api.getToken()) _counts = api.getWorkCounts(who);
  }
  return _counts;
}

/**
 * How much work this account has in total, or `null` when there is no hint.
 * ⚠ IT COUNTS EVERY KIND, including ones no dashboard group prints — it is the
 * answer to "has this person used the app", not to "what is on this screen".
 */
export function hintTotal() {
  if (!_counts) return null;
  return Object.values(_counts).reduce((n, v) => n + (Number(v) || 0), 0);
}

/**
 * A confident "this account is brand new". FALSE when we simply don't know.
 *
 * This is the whole point of the hint: it is the difference between showing a
 * first-time user six shimmering skeletons for the second and a half it takes
 * to confirm they have nothing, and showing them the actual empty dashboard
 * instantly. Only ever used to decide what to DRAW.
 */
export function isNewAccount() {
  return hintTotal() === 0;
}

// ---------------------------------------------------------------------------
// Fetching
// ---------------------------------------------------------------------------
function run(key) {
  const fetcher = FEEDS[key];
  if (!fetcher) return Promise.resolve(undefined);

  const startedIn = _epoch;
  const p = Promise.resolve()
    .then(fetcher)
    .then((value) => {
      if (startedIn !== _epoch) return undefined; // a different account now
      _data[key] = value;
      _at[key] = Date.now();
      delete _error[key];
      // ⚠ THE RAIL IS WRITTEN DOWN, and only this one. The sidebar has to be
      // drawn before any request can have answered, and drawing it from the
      // built-in list meant a workflow an administrator had HIDDEN flashed up
      // on every reload. Kept here, the next first paint starts from what this
      // account was actually told. See `rememberEntitlements` in api.js.
      if (key === "entitlements") api.rememberEntitlements(_owner, value);
      return value;
    })
    .catch((e) => {
      if (startedIn !== _epoch) return undefined;
      // ⚠ THE LAST GOOD VALUE IS KEPT. A failed refresh must leave what is on
      // screen alone — replacing a library with an error because one poll
      // blipped is how a working app looks broken. The message is recorded
      // beside it so the screen can say so without losing the content.
      _error[key] = e?.message || "Couldn't load this.";
      return _data[key];
    })
    .finally(() => {
      if (startedIn !== _epoch) return;
      delete _pending[key];
      emit();
    });

  _pending[key] = p;
  return p;
}

/**
 * Make sure this feed is loaded or loading. Returns the value.
 *
 * ⚠ RULE 1 LIVES HERE: an in-flight request is JOINED, never duplicated. Home
 * mounting three milliseconds after `Login` kicked off the prefetch has to end
 * up awaiting that same promise, or the whole exercise just doubled the number
 * of requests a sign-in makes.
 */
export function ensure(key) {
  if (_pending[key]) return _pending[key];
  if (key in _data && !isStale(key)) return Promise.resolve(_data[key]);
  return run(key);
}

/** Refetch now, whatever the cache holds. The Refresh button, and only it. */
export function refresh(keys = FEED_KEYS) {
  return Promise.all(keys.map((k) => _pending[k] || run(k)));
}

/**
 * Load anything not already loaded, and quietly re-read anything gone stale.
 * The screen keeps showing what it has throughout — see rule 2.
 */
export function revalidate(keys = FEED_KEYS) {
  return Promise.all(keys.map((k) => ensure(k)));
}

/**
 * START EVERYTHING, at the moment of authentication.
 *
 * Called from two places and no others: `Login` the instant a token is written,
 * and the shell on boot when a token is already in storage. Both are BEFORE the
 * dashboard exists, which is the entire reason the delay went away.
 *
 * `counts` is the `TokenResponse.counts` hint when we have just signed in;
 * omitted on a reload, where it is read back out of storage instead.
 */
export function prefetch({ email, counts } = {}) {
  const who = email || api.getEmail();
  if (!who || !api.getToken()) return;

  // A different account than the one this cache holds — start clean rather than
  // mixing two libraries together.
  if (_owner && _owner !== who) reset();
  _owner = who;

  if (counts && typeof counts === "object") {
    _counts = counts;
    api.rememberWorkCounts(who, counts);
  } else {
    // A reload: no login call happened, so fall back to what the last sign-in
    // recorded. Possibly a few projects out of date, which is fine — it is only
    // ever used to choose between "empty state" and "skeletons", and the real
    // lists overwrite whatever it led us to draw. `hint()` does the reading.
    hint();
  }

  // ⚠ SEEDED, AND DELIBERATELY WITHOUT A TIMESTAMP. `_at` is what `isStale`
  // reads, so leaving it unset means this copy is ALWAYS considered stale and
  // `ensure` goes and re-reads it — while `read()` can hand it to the very
  // first render in the meantime. Shown at once, believed only until the server
  // says otherwise, which is the whole contract for a remembered answer.
  if (!("entitlements" in _data)) {
    const last = api.getRememberedEntitlements(who);
    if (last) _data.entitlements = last;
  }

  // ⚠ AN ACCOUNT WE KNOW IS EMPTY GETS ITS LISTS SEEDED, NOT FETCHED. The
  // point is the FIRST PAINT: with `[]` already in hand the dashboard draws its
  // real empty state on frame one, instead of shimmering for a second and a
  // half to confirm what the server told us at sign-in.
  //
  // ⚠ IT IS A HEAD START, NOT A BELIEF. The hint can be stale — it is read
  // back from storage on a reload, and an account is only "new" until the
  // moment it isn't. So nothing here is final: Home re-reads every list on
  // mount regardless, and a seeded `[]` is overwritten by the truth a moment
  // later. Trusting the hint permanently would be how somebody's first project
  // goes missing from their own dashboard.
  if (isNewAccount()) {
    for (const key of LIST_KEYS) {
      if (!(key in _data)) {
        _data[key] = [];
        _at[key] = Date.now();
      }
    }
    ensure("me");
    ensure("entitlements");
    emit();
    return;
  }

  revalidate();
}

/**
 * Throw the whole cache away. Logging out, switching account, adding one.
 *
 * ⚠ BUMPING `_epoch` IS THE IMPORTANT LINE, not the emptying. Requests already
 * in the air still resolve; the epoch is what stops their answers being written
 * into the account that is on screen now.
 */
export function reset() {
  _epoch += 1;
  _owner = null;
  _data = {};
  _at = {};
  _error = {};
  _pending = {};
  _counts = null;
  emit();
}

// ⚠ AN ADMIN CHANGE INVALIDATES THE REMEMBERED RAIL IMMEDIATELY. Registered at
// module load, called by the admin panel's mutations (see
// `api.onEntitlementsChanged`). Without it, the administrator who has just
// hidden a workflow keeps a remembered answer that still contains it — and sees
// it flash once more on their next reload, which is the complaint this whole
// mechanism exists to answer.
//
// The stored copy is dropped FIRST and re-read second, so that even if the
// re-read fails, the next reload waits for the server rather than drawing a
// list we already know to be wrong.
api.onEntitlementsChanged(() => {
  api.forgetEntitlements(_owner || api.getEmail());
  if (api.getToken()) refresh(["entitlements"]);
});

/**
 * Fold a locally-known change into a cached list without a round trip.
 *
 * Renaming or deleting a project inside a workflow leaves the dashboard's copy
 * saying the old thing until it goes stale. Callers that already know the new
 * truth can write it here instead of forcing everyone to refetch.
 */
export function patchList(key, fn) {
  if (!(key in _data) || !Array.isArray(_data[key])) return;
  _data[key] = fn(_data[key]);
  emit();
}

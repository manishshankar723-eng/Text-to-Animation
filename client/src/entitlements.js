// What THIS account may USE — the browser's half of `server/features.py`.
//
// THE PROBLEM THIS SOLVES: the server has refused an off capability since
// Phase 2 — `require_feature('cap.veo-render')` and twenty-four others — but
// the app never asked. A customer whose Veo is off still saw ✨ Animate, still
// wrote a motion prompt, still pressed the button, and got a 403 for their
// trouble. The workflows in the rail were handled (hidden, badged or locked);
// the capabilities INSIDE them were not, and that was the largest gap left in
// the panel work.
//
// ⚠ THIS IS NOT A SECOND GATE. Nothing here decides anything: the answer is
// computed once, on the server, by the one resolver, and shipped down on the
// boot call. This module holds that answer and shapes it for a button. Editing
// it in a debugger turns a control back on and the route still answers 403 —
// which is the correct division and the reason the guards stay where they are.
//
// ⚠ AND IT FAILS OPEN, LIKE EVERY OTHER READER OF THE RESOLVER. Until the call
// answers, everything is on. A cold start that greyed out ✨ Animate for a
// second — on every page load, for every account, including the ones who have
// paid for it — would be a worse bug than the one this fixes.
//
// THREE STATES, NOT TWO, and they are the whole design:
//
//   gone    the capability is not in the list at all → don't draw the control
//   locked  visible, off, `locked` → draw it disabled, wearing the reason
//   on      draw it exactly as before
//
// "Gone" is a kill switch or a rollout the account is not in; there is nothing
// to say about it and nothing to sell, so the control simply is not there.
// "Locked" is one purchase away, and hiding it would mean nobody ever discovers
// what they are missing — the same argument the sidebar's 🔒 row is built on.

// ⚠ THE FALLBACK CATALOGUE — a copy of `_CAPABILITIES` in `server/features.py`,
// same ids, same labels, same icons, same order. Exactly like `WORKFLOWS` in
// `Sidebar.jsx`, it is what the app knows when the entitlements call has not
// answered or has failed, and for the same reason: an app that draws nothing is
// a worse outage than one that is briefly out of date. Keep the two in step.
export const CAPABILITIES = [
  { id: "veo-render", label: "Veo video renders", icon: "🎥" },
  { id: "image-generate", label: "Image generation", icon: "🖼️" },
  { id: "tts-voiceover", label: "Voiceover (text to speech)", icon: "🗣️" },
  { id: "captions", label: "Automatic captions", icon: "💬" },
  { id: "director", label: "🎬 Make Video (the auto-editor)", icon: "🎬" },
  { id: "3d-meshy", label: "3D models (Meshy / Tripo)", icon: "🧊" },
];

// What a control gets when nobody has said otherwise. Frozen and shared: it is
// returned by reference on the hot path (every render of every gated control
// before the call lands), and a fresh object each time would re-fire the
// `useMemo`s that hang off it.
const OPEN = Object.freeze({
  on: true,
  visible: true,
  locked: false,
  status: "live",
  reason: "",
  minTier: null,
  known: false,
});

/**
 * The decision, as a pure function of the server's answer.
 *
 * @param {object|null} entitlements  what `/auth/me/entitlements` returned, or
 *   null/undefined if it has not answered yet.
 * @param {string} id  a capability id — "veo-render", not "cap.veo-render".
 * @returns {{on, visible, locked, status, reason, minTier, known}}
 *
 * ⚠ `known` IS "HAS THE SERVER ANSWERED", NOT "IS IT ON". A capability missing
 * from a list that never arrived means nothing at all; missing from one that
 * did means hidden. Collapsing those two is how a cold start greys out a
 * button somebody is paying for. Same distinction as `entitled` in App.jsx.
 */
export function capabilityState(entitlements, id) {
  const list = entitlements?.capabilities;
  if (!Array.isArray(list)) return OPEN;
  const found = list.find((c) => c && c.id === id);
  if (!found) {
    // The server answered and did not mention it: hidden, or a rollout this
    // account is not in. Nothing to draw and nothing to sell.
    return {
      on: false,
      visible: false,
      locked: false,
      status: "hidden",
      reason: "",
      minTier: null,
      known: true,
    };
  }
  return {
    on: !!found.on,
    visible: true,
    locked: !!found.locked,
    status: found.status || "live",
    // ⚠ THE SERVER'S OWN SENTENCE, not one written again here. It is the same
    // string `require_feature` would have put in the 403 — see `refusal()`.
    reason: found.on ? "" : found.reason || `${found.label} isn't enabled for your account.`,
    minTier: found.min_tier || null,
    known: true,
  };
}

// ---------------------------------------------------------------------------
// The store
// ---------------------------------------------------------------------------
// ⚠ A MODULE-LEVEL CACHE, NOT A CONTEXT, and that is a deliberate match to how
// this client already works: `api.js` holds the session the same way and the
// editor's state is three custom hooks. A Context here would mean threading a
// provider through the shell and a prop through eight components to reach one
// button inside a properties pane.
//
// One object, replaced (never mutated) so the snapshot comparison in
// `useCapability` is an identity check.
let _current = null;
const _subscribers = new Set();

/** Hand the boot call's answer to every gated control. */
export function setEntitlements(next) {
  _current = next && typeof next === "object" ? next : null;
  for (const fn of _subscribers) fn();
}

/** Sign-out and account-switch. ⚠ Back to fail-open, never to "all off". */
export function clearEntitlements() {
  setEntitlements(null);
}

/** The whole last answer, by reference. Stable between `setEntitlements` calls. */
export function getEntitlements() {
  return _current;
}

export function subscribe(fn) {
  _subscribers.add(fn);
  return () => _subscribers.delete(fn);
}

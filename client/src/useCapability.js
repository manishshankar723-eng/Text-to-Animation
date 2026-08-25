// `useCapability("veo-render")` — the one line a control adds to know whether
// it may be pressed. The rules and the store are in `entitlements.js`, which is
// plain JS on purpose: it is driven by node in `tests/capability_check.py`, and
// importing React there would have made that impossible.
//
// USE IT AT THE CONTROL, not at the top of a screen. A pane does not spend
// money; the button in it does, and the button is what has to say why it is
// greyed out. It reads a module-level store, so there is no prop to thread and
// no provider to wrap — see the note in `entitlements.js`.
import { useMemo, useSyncExternalStore } from "react";
import { capabilityState, getEntitlements, subscribe } from "./entitlements.js";

export default function useCapability(id) {
  // ⚠ THE SNAPSHOT IS THE WHOLE ANSWER OBJECT, NOT THIS CAPABILITY'S SLICE.
  // `useSyncExternalStore` compares snapshots by identity and re-renders until
  // two agree, so a getter that built a fresh `{on, reason, …}` on every call
  // would loop forever. The store replaces one object; the slice is derived
  // below, in render, where a new object is harmless.
  const entitlements = useSyncExternalStore(subscribe, getEntitlements, getEntitlements);
  return useMemo(() => capabilityState(entitlements, id), [entitlements, id]);
}

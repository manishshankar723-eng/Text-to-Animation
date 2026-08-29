// `const brand = useBranding()` — the one line a screen adds to print the app's
// name or draw its mark. `{name, logoUrl, stamp}`.
//
// USE IT WHERE THE NAME IS PRINTED, not at the top of a tree and threaded down.
// It reads a module store (`branding.js`), so there is no provider to wrap and
// no prop to pass — which is what lets the LOGGED-OUT landing page, the sign-in
// card and the public storyboard viewer all use it, none of which sit inside the
// app shell.
//
// ⚠ THE SNAPSHOT IS THE WHOLE BRAND OBJECT, and `branding.js` replaces it rather
// than mutating it, so the identity comparison `useSyncExternalStore` does is
// the right test. Same shape and same reasoning as `useCapability`.
import { useSyncExternalStore } from "react";
import { getBrand, subscribe } from "./branding.js";

export default function useBranding() {
  return useSyncExternalStore(subscribe, getBrand, getBrand);
}

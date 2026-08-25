import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173. The API base is configured via VITE_API_BASE (see .env),
// defaulting to http://127.0.0.1:8000 in src/api.js. The backend already sends
// permissive CORS headers, so no proxy is required.
//
// ⚠ `strictPort` IS THE POINT, NOT THE PORT. Without it Vite treats 5173 as a
// PREFERENCE: if a stale dev server (or anything else) is holding it, Vite
// quietly starts on 5174 instead and prints the real address in a terminal
// nobody is looking at. That address then lives on in the browser's history and
// in bookmarks, and the next time the app is started NORMALLY — on 5173 — the
// remembered 5174 answers ERR_CONNECTION_REFUSED and the app looks broken. That
// has now cost a debugging session. With `strictPort` the drift cannot happen:
// either the app is at 5173 or it refuses to start and SAYS the port is taken,
// which is a message you can act on.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
});

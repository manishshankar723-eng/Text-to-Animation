import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173. The API base is configured via VITE_API_BASE (see .env),
// defaulting to http://127.0.0.1:8000 in src/api.js. The backend already sends
// permissive CORS headers, so no proxy is required.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});

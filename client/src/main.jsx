import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { applyToDocument, loadBranding } from "./branding.js";
import { applyTheme, getTheme } from "./theme.js";
import "./styles/index.css";

// Set the theme BEFORE the first render, otherwise a light-mode user gets a
// flash of the dark palette on every page load.
//
// ⚠ LIGHT-vs-DARK AND THE COLOUR PALETTE ARE TWO DIFFERENT CHOICES BY TWO
// DIFFERENT PEOPLE, and both are stamped before the first paint. This one is
// the VIEWER's, kept in their own browser; the palette below is the
// ADMINISTRATOR's, kept on the server and remembered here. They compose —
// `palette.js` writes a `:root` block and a `:root[data-theme="light"]` block —
// so the viewer keeps their light mode in whatever colours the deployment is
// wearing.
applyTheme(getTheme());

// The app's NAME and MARK, on exactly the same principle and in the same breath:
// the tab title and the favicon are stamped from what this browser remembered
// LAST time, before anything is drawn, and the server is asked in the
// background. Without the first line the tab would say the built-in name for as
// long as the round trip takes, on every single page load.
//
// ⚠ THE FETCH IS NOT AWAITED AND ITS FAILURE IS SILENT. Rendering must not wait
// on a cosmetic call, and a customer whose network dropped should still get the
// app — wearing the last name it knew. See branding.js.
// ⚠ THIS ALSO PAINTS THE PALETTE (see `applyToDocument`), which is why it runs
// before `createRoot` and not inside a component.
applyToDocument();
loadBranding();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

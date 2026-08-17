import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import { applyTheme, getTheme } from "./theme.js";
import "./styles/index.css";

// Set the theme BEFORE the first render, otherwise a light-mode user gets a
// flash of the dark palette on every page load.
applyTheme(getTheme());

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

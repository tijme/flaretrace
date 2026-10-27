/** @file Implement theme interactions without third-party browser runtime dependencies. */
"use strict";
(() => {
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  let mode = "system";
  try { mode = localStorage.getItem("flaretrace-theme") || "system"; } catch { /* Storage may be blocked. */ }
  if (!["system", "light", "dark"].includes(mode)) mode = "system";
  /** Apply the selected theme and update the toggle’s visible and accessible labels. */
  function apply() {
    document.documentElement.dataset.theme = mode === "system" ? (preference.matches ? "dark" : "light") : mode;
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.textContent = `Theme: ${mode}`;
      button.setAttribute("aria-label", `Theme: ${mode}. Switch to ${mode === "system" ? "light" : mode === "light" ? "dark" : "system"}.`);
    }
  }
  apply();
  preference.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.getElementById("theme-toggle")?.addEventListener("click", () => {
      mode = mode === "system" ? "light" : mode === "light" ? "dark" : "system";
      try { localStorage.setItem("flaretrace-theme", mode); } catch { /* Keep the choice for this page. */ }
      apply();
    });
  });
})();

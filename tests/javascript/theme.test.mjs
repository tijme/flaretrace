/** @file Exercise real browser scripts using isolated DOM fixtures and synthetic devices. */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { environment } from "./harness.mjs";

describe("Themes", () => {
  for (const mode of ["system", "light", "dark", "invalid", null]) test("theme preference: " + mode, async () => {
    const testEnvironment = await environment('<button id="theme-toggle"></button>');
    const preference = { matches: true, /** Capture the test’s theme-change callback without a real system preference.
     * @param {unknown} _name - _name supplied by the current synthetic test scenario.
     * @param {Function} callback - Callback receiving the synthetic encoded image or null.
     */
    addEventListener(_name, callback) { this.change = callback; } };
    /** Expose a synthetic system color-scheme preference to the theme script.
     * @returns {object} Synthetic color-scheme preference and change-listener hooks.
     */
    testEnvironment.window.matchMedia = () => preference;
    if (mode) testEnvironment.window.localStorage.setItem("flaretrace-theme", mode);
    testEnvironment.load("theme.js");
    testEnvironment.document.dispatchEvent(new testEnvironment.window.Event("DOMContentLoaded"));
    assert.equal(testEnvironment.document.documentElement.dataset.theme, mode === "light" ? "light" : "dark");
    const button = testEnvironment.document.getElementById("theme-toggle");
    for (let index = 0; index < 3; index++) button.click();
    assert.match(button.getAttribute("aria-label"), /Switch to/);
    preference.matches = false; preference.change();
    assert.equal(testEnvironment.document.documentElement.dataset.theme, mode === "dark" ? "dark" : "light");
    await testEnvironment.close();
  });

  test("theme works without controls or storage access", async () => {
    const testEnvironment = await environment();
    /** Expose a synthetic system color-scheme preference to the theme script.
     * @returns {object} Synthetic color-scheme preference and change-listener hooks.
     */
    testEnvironment.window.matchMedia = () => ({ matches: false, /** Capture the test’s theme-change callback without a real system preference. */
    addEventListener() {} });
    Object.defineProperty(testEnvironment.window, "localStorage", { /** Simulate access to a browser property during capability-failure testing. */
    get() { throw new Error("Blocked"); } });
    testEnvironment.load("theme.js");
    testEnvironment.document.dispatchEvent(new testEnvironment.window.Event("DOMContentLoaded"));
    assert.equal(testEnvironment.document.documentElement.dataset.theme, "light"); await testEnvironment.close();
    const blockedWrite = await environment('<button id="theme-toggle"></button>');
    /** Expose a synthetic system color-scheme preference to the theme script.
     * @returns {object} Synthetic color-scheme preference and change-listener hooks.
     */
    blockedWrite.window.matchMedia = () => ({ matches: false, /** Capture the test’s theme-change callback without a real system preference. */
    addEventListener() {} });
    blockedWrite.patch(blockedWrite.window, "localStorage", { /** Return the test’s persisted theme preference or simulate blocked storage.
     * @returns {string|null} Persisted theme string, or null when no preference is saved.
     */
    getItem: () => "system", /** Record a theme preference or simulate blocked browser storage. */
    setItem() { throw new Error("Blocked"); } });
    blockedWrite.load("theme.js"); blockedWrite.document.dispatchEvent(new blockedWrite.window.Event("DOMContentLoaded"));
    blockedWrite.document.getElementById("theme-toggle").click();
    assert.equal(blockedWrite.document.documentElement.dataset.theme, "light"); await blockedWrite.close();
  });
});

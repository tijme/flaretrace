/** @file Exercise real browser scripts using isolated DOM fixtures and synthetic devices. */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { environment } from "./harness.mjs";

describe("Administration", () => {
  /** Build an expandable click-group fixture with deterministic identifiers.
   * @param {unknown} total - total supplied by the current synthetic test scenario.
   * @returns {string} HTML markup for a deterministic expandable click-group fixture.
   */
  const group = (total = 3) => `<button id="toggle-g" data-group-toggle="g" aria-expanded="false">Clicks</button><div id="clicks-g" class="group-children" hidden><div data-group-body="g" data-group-url="/group" data-version="${total}:v1"></div></div>`;
  /** Build a live-region page fixture for administrator refresh tests.
   * @param {unknown} ids - ids supplied by the current synthetic test scenario.
   * @param {unknown} more - more supplied by the current synthetic test scenario.
   * @returns {string} HTML markup containing the synthetic live-region fixture.
   */
  const page = (ids, more = false) => `<div class="group-page"><div class="table-scroll"><table><tbody data-click-rows>${ids.map((id) => `<tr data-click-id="${id}" data-href="/visit/${id}"><td>${id}</td></tr>`).join("")}</tbody></table></div><div data-group-pagination>${more ? '<a data-group-more href="/older">Older</a>' : ""}<span class="group-feedback"></span></div></div>`;
  /** Build a deterministic HTTP response for the requested browser-test scenario.
   * @param {string} html - Synthetic initial document markup.
   * @param {unknown} extra - extra supplied by the current synthetic test scenario.
   * @returns {object} HTTP-style response exposing configured status and body readers.
   */
  const response = (html, extra = {}) => ({ ok: true, redirected: false, status: 200, /** Return the synthetic HTML fragment expected by the administrator script.
   * @returns {Promise<string>} Synthetic response body as HTML text.
   */
  text: async () => html, ...extra });

  test("rows support mouse/keyboard navigation while preserving controls and selections", async () => {
    const testEnvironment = await environment('<table><tr id="row" data-href="/visit"><td id="cell">Visit</td><td><button id="control">Action</button></td></tr></table>');
    testEnvironment.load("admin.js");
    const row = testEnvironment.document.getElementById("row"), cell = testEnvironment.document.getElementById("cell");
    assert.equal(row.tabIndex, 0);
    cell.click(); assert.equal(testEnvironment.navigations.at(-1), "/visit");
    row.dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    cell.dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    row.dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    testEnvironment.document.getElementById("control").click();
    assert.equal(testEnvironment.navigations.length, 2);
    row.dispatchEvent(new testEnvironment.window.MouseEvent("click", { ctrlKey: true, bubbles: true }));
    row.dispatchEvent(new testEnvironment.window.MouseEvent("auxclick", { button: 1, bubbles: true }));
    row.dispatchEvent(new testEnvironment.window.MouseEvent("auxclick", { button: 2, bubbles: true }));
    assert.deepEqual(testEnvironment.navigations.at(-1), ["/visit", "_blank", "noopener"]);
    /** Simulate a browser text selection used to protect live interactions.
     * @returns {object} Synthetic selection object inspected by row-navigation and live-refresh guards.
     */
    testEnvironment.window.getSelection = () => ({ /** Expose the selected text used by the row-navigation guard.
     * @returns {string} function toString() { [native code] }
     */
    toString: () => "Selected" }); cell.click();
    assert.equal(testEnvironment.navigations.length, 4); await testEnvironment.close();
  });

  test("clipboard success and fallback retain a usable field", async () => {
    for (const works of [true, false]) {
      const testEnvironment = await environment('<input id="url" value="https://example.com"><button data-copy="url">Copy link</button>');
      const copied = [];
      testEnvironment.patch(testEnvironment.window.navigator, "clipboard", works ? { /** Simulate clipboard writes or a configured clipboard permission failure.
       * @param {boolean|number|string} value - Value being normalized or applied by this helper.
       * @returns {Promise<void>} Promise settled when the synthetic clipboard write succeeds.
       */
      writeText: async (value) => copied.push(value) } : undefined);
      testEnvironment.load("admin.js"); const button = testEnvironment.document.querySelector("button");
      button.click(); await testEnvironment.flush();
      assert.equal(button.textContent, works ? "Copied" : "Select & copy");
      if (works) { assert.equal(copied[0], "https://example.com"); await testEnvironment.flush(1800); assert.equal(button.textContent, "Copy link"); }
      else assert.equal(testEnvironment.document.activeElement.id, "url");
      await testEnvironment.close();
    }
  });

  test("destructive form confirmation can cancel submission", async () => {
    const testEnvironment = await environment('<form data-confirm="Delete?"></form><form id="plain"></form>');
    testEnvironment.load("admin.js"); /** Return the test’s configured answer to the destructive-action confirmation.
     * @returns {boolean} Configured response to the confirmation prompt.
     */
    testEnvironment.window.confirm = () => false;
    const cancel = new testEnvironment.window.Event("submit", { bubbles: true, cancelable: true });
    testEnvironment.document.querySelector("form").dispatchEvent(cancel); assert.equal(cancel.defaultPrevented, true);
    /** Return the test’s configured answer to the destructive-action confirmation.
     * @returns {boolean} Configured response to the confirmation prompt.
     */
    testEnvironment.window.confirm = () => true;
    const allow = new testEnvironment.window.Event("submit", { bubbles: true, cancelable: true });
    testEnvironment.document.querySelector("form").dispatchEvent(allow); assert.equal(allow.defaultPrevented, false);
    testEnvironment.document.getElementById("plain").dispatchEvent(new testEnvironment.window.Event("submit", { bubbles: true })); await testEnvironment.close();
  });

  test("groups load, append, deduplicate, collapse, and preserve scroll", async () => {
    const testEnvironment = await environment(group()); let count = 0;
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(++count === 1 ? page(["a", "b"], true) : page(["b", "c"]));
    testEnvironment.load("admin.js"); testEnvironment.document.getElementById("toggle-g").click(); await testEnvironment.flush();
    assert.equal(testEnvironment.document.querySelectorAll("[data-click-id]").length, 2);
    testEnvironment.document.querySelector("[data-group-more]").click(); await testEnvironment.flush();
    assert.equal(testEnvironment.document.querySelectorAll("[data-click-id]").length, 3);
    assert.match(testEnvironment.document.querySelector("[data-group-pagination]").textContent, /All clicks/);
    testEnvironment.document.querySelector(".table-scroll").scrollTop = 30;
    testEnvironment.window.saveGroups(testEnvironment.document); testEnvironment.window.toggleGroup("g"); testEnvironment.window.toggleGroup("g");
    assert.equal(testEnvironment.document.querySelector(".table-scroll").scrollTop, 30);
    testEnvironment.window.toggleGroup("missing"); await testEnvironment.window.loadGroup("missing", "/missing");
    await testEnvironment.close();
  });

  test("groups refresh every loaded page and stop at a missing next-page link", async () => {
    const testEnvironment = await environment(group(120)); let calls = 0;
    const ids = Array.from({ length: 100 }, (_, index) => String(index));
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(++calls % 2 ? page(ids.slice(0, 50), true) : page(ids.slice(50), true));
    testEnvironment.load("admin.js"); testEnvironment.window.toggleGroup("g"); await testEnvironment.flush();
    await testEnvironment.window.loadGroup("g", "/older", true); assert.equal(testEnvironment.document.querySelectorAll("[data-click-id]").length, 100);
    await testEnvironment.window.loadGroup("g", "/group", false, true); assert.equal(calls, 4);
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(page(["new"]));
    await testEnvironment.window.loadGroup("g", "/group", false, true);
    assert.equal(testEnvironment.document.querySelector("[data-click-id]").dataset.clickId, "new"); await testEnvironment.close();
  });

  for (const failure of ["network", "redirect", "invalid", "status"]) test("group failure is retryable: " + failure, async () => {
    const testEnvironment = await environment(group());
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => {
      if (failure === "network") throw new Error("offline");
      return response(failure === "invalid" ? "<p>Invalid</p>" : page(["a"]), { ok: failure !== "status", redirected: failure === "redirect" });
    };
    testEnvironment.load("admin.js"); testEnvironment.window.toggleGroup("g"); await testEnvironment.flush();
    assert.ok(testEnvironment.document.querySelector("[data-group-retry]"));
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(page(["a", "b", "c"]));
    testEnvironment.document.querySelector("[data-group-retry]").click(); await testEnvironment.flush();
    assert.equal(testEnvironment.document.querySelectorAll("[data-click-id]").length, 3);
    /** Simulate an HTTP response and record the submitted evidence for assertions. */
    testEnvironment.window.fetch = async () => { throw new Error("offline"); };
    await testEnvironment.window.loadGroup("g", "/group");
    assert.match(testEnvironment.document.querySelector(".group-feedback")?.textContent || testEnvironment.document.body.textContent, /Could not load clicks/); await testEnvironment.close();
  });

  test("group fetch tolerates removed containers and aborts on timeout/pagehide", async () => {
    const testEnvironment = await environment(group()); let resolve;
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @param {unknown} _url - _url supplied by the current synthetic test scenario.
     * @param {object} settings - Camera settings, recorder configuration, or HTTP request options.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = (_url, settings) => new Promise((done, reject) => {
      resolve = done; settings.signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
    testEnvironment.load("admin.js"); testEnvironment.window.toggleGroup("g"); await testEnvironment.flush();
    testEnvironment.window.toggleGroup("g"); await testEnvironment.window.loadGroup("g", "/same");
    testEnvironment.document.getElementById("clicks-g").remove(); resolve(response(page(["a"]))); await testEnvironment.flush();
    testEnvironment.document.body.innerHTML = group(); testEnvironment.window.toggleGroup("g"); await testEnvironment.flush();
    await testEnvironment.flush(8000); assert.match(testEnvironment.document.body.textContent, /Could not load clicks/);
    await testEnvironment.close();
  });

  test("group rows expand with keyboard, preserve cached state through DOM replacement", async () => {
    const testEnvironment = await environment('<div id="expand" tabindex="0" data-expand-group="g">Expand</div>' + group());
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(page(["a", "b", "c"]));
    testEnvironment.load("admin.js");
    testEnvironment.document.getElementById("expand").dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: " ", bubbles: true })); await testEnvironment.flush();
    testEnvironment.window.saveGroups(testEnvironment.document);
    testEnvironment.document.getElementById("expand").click(); testEnvironment.document.getElementById("expand").click();
    const region = new testEnvironment.window.DOMParser().parseFromString(group(), "text/html");
    testEnvironment.window.restoreGroups(region, false);
    assert.equal(region.getElementById("toggle-g").getAttribute("aria-expanded"), "true");
    assert.equal(region.querySelectorAll("[data-click-id]").length, 3);
    testEnvironment.window.restoreGroups(testEnvironment.document); await testEnvironment.flush(); await testEnvironment.close();
  });

  /** Start the administrator script with synthetic live-page responses.
   * @param {unknown} content - content supplied by the current synthetic test scenario.
   * @returns {Promise<object>} Initialized DOM harness running the shipped administrator script.
   */
  const live = (content) => '<span data-live-status>Live</span><div id="region" data-live-region>' + content + '</div>';

  test("live refresh updates regions and preserves disclosures, focus, and scroll", async () => {
    const original = '<details id="details" open><summary>Details</summary>Old</details><div class="table-scroll">Old table</div>';
    const updated = original.replaceAll("Old", "New");
    const testEnvironment = await environment(live(original));
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(live(updated)); testEnvironment.load("admin.js");
    testEnvironment.document.querySelector("summary").focus(); testEnvironment.document.querySelector(".table-scroll").scrollLeft = 25;
    await testEnvironment.flush(2000);
    assert.match(testEnvironment.document.getElementById("region").textContent, /New/);
    assert.equal(testEnvironment.document.getElementById("details").open, true);
    assert.equal(testEnvironment.document.activeElement.tagName, "SUMMARY");
    assert.equal(testEnvironment.document.querySelector(".table-scroll").scrollLeft, 25);
    await testEnvironment.flush(2000); assert.match(testEnvironment.document.querySelector("[data-live-status]").textContent, /every 2 seconds/);
    await testEnvironment.close();
  });

  for (const activity of ["input", "selection", "video"]) test("live refresh defers replacement while interacting: " + activity, async () => {
    const testEnvironment = await environment(live('<input id="editing"><video></video><p>Old</p>'));
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response(live("<p>New</p>")); testEnvironment.load("admin.js");
    if (activity === "input") testEnvironment.document.getElementById("editing").focus();
    if (activity === "selection") /** Simulate a browser text selection used to protect live interactions.
     * @returns {object} Synthetic selection object inspected by row-navigation and live-refresh guards.
     */
    testEnvironment.window.getSelection = () => ({ /** Expose the selected text used by the row-navigation guard.
     * @returns {string} function toString() { [native code] }
     */
    toString: () => "selected", anchorNode: testEnvironment.document.querySelector("p").firstChild });
    if (activity === "video") testEnvironment.patch(testEnvironment.document.querySelector("video"), "paused", false);
    await testEnvironment.flush(2000); assert.match(testEnvironment.document.getElementById("region").textContent, /Old/); await testEnvironment.close();
  });

  for (const status of [401, 403, 404, "redirect"]) test("live updates stop when access ends: " + status, async () => {
    const testEnvironment = await environment(live("Old")); let calls = 0;
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => { calls++; return response("", { status, redirected: status === "redirect" }); };
    testEnvironment.load("admin.js"); await testEnvironment.flush(2000);
    assert.match(testEnvironment.document.querySelector("[data-live-status]").textContent, /stopped/);
    await testEnvironment.flush(10000); assert.equal(calls, 1); await testEnvironment.close();
  });

  test("live failures back off, recover, and pause/resume with visibility", async () => {
    const testEnvironment = await environment(live("Old")); let calls = 0;
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => { calls++; if (calls === 1) return response("", { ok: false }); if (calls === 2) return response("invalid page"); return response(live("New")); };
    testEnvironment.load("admin.js"); await testEnvironment.flush(2000);
    assert.match(testEnvironment.document.querySelector("[data-live-status]").textContent, /Retrying/);
    await testEnvironment.flush(4000); await testEnvironment.flush(8000);
    assert.match(testEnvironment.document.getElementById("region").textContent, /New/);
    testEnvironment.patch(testEnvironment.document, "hidden", true); testEnvironment.document.dispatchEvent(new testEnvironment.window.Event("visibilitychange"));
    const before = calls; await testEnvironment.flush(10000); assert.equal(calls, before);
    testEnvironment.patch(testEnvironment.document, "hidden", false); testEnvironment.document.dispatchEvent(new testEnvironment.window.Event("visibilitychange")); await testEnvironment.flush();
    const event = new testEnvironment.window.Event("pageshow"); testEnvironment.patch(event, "persisted", true); testEnvironment.window.dispatchEvent(event); await testEnvironment.flush();
    assert.ok(calls > before); await testEnvironment.close();
  });

  test("live refresh skips absent regions and preserves expanded group focus", async () => {
    const testEnvironment = await environment(live(group(1)));
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @param {string} url - Same-origin request or navigation target.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async (url) => response(url === "/group" ? page(["a"]) : live(group(1) + "Updated"));
    testEnvironment.load("admin.js"); testEnvironment.document.getElementById("toggle-g").click(); await testEnvironment.flush();
    testEnvironment.document.getElementById("toggle-g").focus(); await testEnvironment.flush(2000);
    assert.equal(testEnvironment.document.activeElement.id, "toggle-g");
    assert.equal(testEnvironment.document.getElementById("toggle-g").getAttribute("aria-expanded"), "true");
    /** Simulate an HTTP response and record the submitted evidence for assertions.
     * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
     */
    testEnvironment.window.fetch = async () => response('<span data-live-status>Live</span>');
    await testEnvironment.flush(2000); assert.ok(testEnvironment.document.getElementById("region")); await testEnvironment.close();
  });
});

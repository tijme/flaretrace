/** @file Exercise the deliberately no-op service worker in an isolated DOM. */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { environment } from "./harness.mjs";

describe("Service worker", () => {
  test("registers a fetch listener without calling respondWith or touching the network", async () => {
    const testEnvironment = await environment();
    const registered = [];
    const original = testEnvironment.window.addEventListener.bind(testEnvironment.window);
    /** Record every event name the loaded script listens for.
     * @param {string} name - Event name passed to addEventListener.
     * @param {...unknown} rest - Remaining addEventListener arguments, forwarded unchanged.
     * @returns {void} Nothing; delegates to the original addEventListener.
     */
    testEnvironment.window.addEventListener = (name, ...rest) => {
      registered.push(name);
      original(name, ...rest);
    };
    testEnvironment.load("sw.js");
    assert.deepEqual(registered, ["fetch"]);
    testEnvironment.window.dispatchEvent(new testEnvironment.window.Event("fetch"));
    await testEnvironment.close();
  });
});

/** @file Exercise real browser scripts using isolated DOM fixtures and synthetic devices. */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { capture } from "./harness.mjs";

describe("Visitor sharing", () => {
  test("capture requires an explicit action and preview controls never collect", async () => {
    for (const options of [{ noConfig: true }, { preview: true }, {}, { runtimeConfig: true, preview: true }, { runtimeConfig: true, noConfig: true }]) {
      const testEnvironment = await capture(options);
      assert.equal(testEnvironment.calls.length, 0);
      if (options.preview) assert.equal(testEnvironment.document.getElementById("enable-button").disabled, true);
      await testEnvironment.close();
    }
  });

  test("appended runtime configuration works without editing the authored body", async () => {
    const testEnvironment = await capture({ runtimeConfig: true, html: '<base href="https://assets.example.invalid/"><button id="enable-button">Share</button><button id="stop-button">Stop</button>' });
    assert.equal(testEnvironment.document.body.dataset.config, undefined);
    assert.equal(testEnvironment.calls.length, 0);
    testEnvironment.start(); await testEnvironment.flush(10000);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.ok(testEnvironment.calls.every((call) => call.url.startsWith("https://localhost/api/visits/")));
    await testEnvironment.close();
  });

  test("location precedes dual camera uploads and every stream is closed", async () => {
    const testEnvironment = await capture();
    testEnvironment.start(); await testEnvironment.flush(10000);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.equal(testEnvironment.document.body.dataset.mediaCount, "4");
    assert.ok(testEnvironment.streams.every((stream) => stream.getTracks()[0].readyState === "ended"));
    const kinds = testEnvironment.events().map((event) => event.kind);
    assert.ok(kinds.indexOf("location") < kinds.indexOf("capture_started"));
    assert.equal(testEnvironment.events().at(-1).data.reason, "complete");
    assert.ok(testEnvironment.calls.every((call) => call.settings.headers["X-Visit-Token"] === "test-token"));
    await testEnvironment.close();
  });

  test("decline and stop actions close collection with distinct reasons", async () => {
    const decline = await capture(); decline.stop(); await decline.flush();
    assert.equal(decline.events()[0].data.reason, "declined");
    assert.equal(decline.streams.length, 0); await decline.close();
    const stop = await capture(); stop.start(); await stop.flush(800); stop.stop(); await stop.flush();
    assert.equal(stop.events().at(-1).data.reason, "stopped");
    assert.ok(stop.streams.every((stream) => stream.getTracks()[0].readyState === "ended"));
    assert.equal(stop.calls.filter((call) => call.url.includes("kind=video")).length, 0);
    await stop.close();
  });

  for (const options of [
    { noLocation: true, noCamera: true }, { noRecorder: true }, { noFormat: true },
    { formatError: true }, { noRecorderFormatMethod: true }, { enumerationError: true },
    { noEnumeration: true }, { rearFirst: true }, { sameCamera: true },
    { noEnumeration: true, unidentified: true }, { constraintsError: true },
  ]) test("capture degrades safely: " + JSON.stringify(options), async () => {
    const testEnvironment = await capture(options); testEnvironment.start(); await testEnvironment.flush(15000);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.ok(testEnvironment.events().some((event) => event.kind === "finished"));
    await testEnvironment.close();
  });

  for (const code of [1, 2, 3, 99]) test("location failure code " + code + " does not prevent teardown", async () => {
    const testEnvironment = await capture({ locationError: code, permissionState: "denied", noCamera: true });
    testEnvironment.start(); await testEnvironment.flush(100);
    assert.equal(testEnvironment.events().find((event) => event.kind === "permission").data.state, code === 1 ? "denied" : "error");
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true"); await testEnvironment.close();
  });

  test("unknown permission state permits one gesture retry", async () => {
    const testEnvironment = await capture({ locationError: 1, cameraError: "NotAllowedError", permissionQueryError: true });
    testEnvironment.start(); await testEnvironment.flush(100);
    assert.equal(testEnvironment.document.body.dataset.phase, "awaiting-gesture");
    testEnvironment.start(); await testEnvironment.flush(100);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.equal(testEnvironment.events().filter((event) => event.kind === "browser").length, 1);
    await testEnvironment.close();
  });

  for (const name of ["NotAllowedError", "TimeoutError", "NotReadableError", "CustomError"]) test("first camera failure is reported as a bounded code: " + name, async () => {
    const testEnvironment = await capture({ cameraError: name, permissionState: "denied" }); testEnvironment.start(); await testEnvironment.flush(100);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.ok(testEnvironment.events().some((event) => event.kind === "permission" && event.data.feature === "camera")); await testEnvironment.close();
  });

  for (const name of ["NotFoundError", "NotReadableError", "CustomError"]) test("second camera failure: " + name, async () => {
    const testEnvironment = await capture({ secondCameraError: name }); testEnvironment.start(); await testEnvironment.flush(10000);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.equal(testEnvironment.document.body.dataset.mediaCount, "2"); await testEnvironment.close();
  });

  for (const option of ["noFrame", "encodeError", "playError", "recorderConstructorError", "recorderStartError", "recorderError", "trackEnded", "invalidVideoFormat", "emptyVideo"]) test("camera processing failure closes streams: " + option, async () => {
    const testEnvironment = await capture({ [option]: true }); testEnvironment.start(); await testEnvironment.flush(10000);
    assert.ok(testEnvironment.events().some((event) => event.kind === "capture_error"));
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    assert.ok(testEnvironment.streams.every((stream) => stream.getTracks()[0].readyState === "ended")); await testEnvironment.close();
  });

  test("timeouts and backgrounding stop pending capture", async () => {
    for (const options of [{ locationHangs: true, noCamera: true }, { cameraHangs: true }]) {
      const testEnvironment = await capture(options); testEnvironment.start(); await testEnvironment.flush(31000);
      assert.equal(testEnvironment.document.body.dataset.serverClosed, "true"); await testEnvironment.close();
    }
    const testEnvironment = await capture({ locationHangs: true }); testEnvironment.start(); await testEnvironment.flush();
    testEnvironment.patch(testEnvironment.document, "hidden", true); testEnvironment.document.dispatchEvent(new testEnvironment.window.Event("visibilitychange")); await testEnvironment.flush();
    assert.equal(testEnvironment.events().at(-1).data.reason, "page_hidden"); await testEnvironment.close();
  });

  test("insecure contexts report blocked permissions without sensor calls", async () => {
    const testEnvironment = await capture({ secure: false }); testEnvironment.start(); await testEnvironment.flush();
    assert.equal(testEnvironment.streams.length, 0);
    assert.equal(testEnvironment.document.body.dataset.locationState, "HTTPS required");
    assert.equal(testEnvironment.events().filter((event) => event.kind === "permission").length, 2);
    await testEnvironment.close();
  });

  test("upload failures are sanitized and finish failures do not retain camera access", async () => {
    for (const network of [false, true]) {
      const testEnvironment = await capture({ /** Build a deterministic HTTP response for the requested browser-test scenario.
       * @param {unknown} call - call supplied by the current synthetic test scenario.
       * @returns {object} HTTP-style response exposing configured status and body readers.
       */
      response: async (call) => {
        if (call.url.includes("/media")) {
          if (network) throw new TypeError("private URL must not be sent");
          return { ok: false, status: 413, /** Decode the synthetic JSON response expected by the shipped browser script.
           * @returns {Promise<object>} Synthetic decoded JSON body expected by the capture script.
           */
          json: async () => ({}) };
        }
        return { ok: true, /** Decode the synthetic JSON response expected by the shipped browser script. */
        json: async () => { throw new Error("bad JSON"); } };
      }});
      testEnvironment.start(); await testEnvironment.flush(10000);
      assert.ok(testEnvironment.events().some((event) => event.kind === "diagnostic" && event.data.error === (network ? "NetworkError" : "UploadError")));
      assert.ok(!JSON.stringify(testEnvironment.events()).includes("private URL")); await testEnvironment.close();
    }
    const testEnvironment = await capture({ /** Build a deterministic HTTP response for the requested browser-test scenario. */
    response: async () => { throw new TypeError("offline"); } });
    testEnvironment.stop(); await testEnvironment.flush(); assert.equal(testEnvironment.document.body.dataset.serverClosed, "error"); await testEnvironment.close();
  });

  test("custom controls support keyboard activation, disabled state, and destination continuation", async () => {
    const testEnvironment = await capture({ destination: "https://example.com/", noCamera: true, noLocation: true,
      html: '<span id="enable-button">Share</span><a id="stop-button">Stop</a><input id="disable-button">' });
    const button = testEnvironment.document.getElementById("enable-button");
    assert.equal(button.getAttribute("role"), "button");
    button.dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: "Tab" }));
    button.dispatchEvent(new testEnvironment.window.KeyboardEvent("keydown", { key: "Enter" })); await testEnvironment.flush(100);
    assert.equal(testEnvironment.document.body.dataset.serverClosed, "true");
    button.click(); testEnvironment.stop(); await testEnvironment.flush(3000);
    assert.ok(testEnvironment.navigations.includes("https://example.com/")); await testEnvironment.close();
  });
});

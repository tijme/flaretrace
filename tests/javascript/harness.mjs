/** @file Exercise real browser scripts using isolated DOM fixtures and synthetic devices. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Script } from "node:vm";
import { Window } from "happy-dom";
import FakeTimers from "@sinonjs/fake-timers";

/** Isolated browser-test context; no real camera, location, or external HTTP service is used.
 * @typedef {object} BrowserHarness
 * @property {Window} window - Synthetic browser global used by shipped scripts.
 * @property {Document} document - DOM owned by the synthetic window.
 * @property {object} clock - Fake timers controlling scheduled and asynchronous work.
 * @property {Array<string|Array>} navigations - Captured navigation and new-tab requests.
 * @property {Function} patch - Replace a browser API within this isolated context.
 * @property {Function} execute - Evaluate a snippet in the synthetic browser global.
 * @property {Function} load - Execute an actual shipped browser script by filename.
 * @property {Function} flush - Advance fake time and settle pending work.
 * @property {Function} close - Dispatch teardown and release the DOM and timers.
 * @property {object[]} [calls] - Recorded signed visitor HTTP submissions.
 * @property {object[]} [streams] - Synthetic streams opened by capture tests.
 * @property {Function} [events] - Read recorded event payloads without media submissions.
 * @property {Function} [start] - Activate the authored sharing control.
 * @property {Function} [stop] - Activate the authored stop control.
 */

/** Run shipped browser scripts in an isolated DOM with deterministic time.
 * @param {string} html - Synthetic initial document markup.
 * @param {object} options - Explicit synthetic browser capabilities and failure switches.
 * @returns {Promise<BrowserHarness>} Isolated window, deterministic timers, script loader, and teardown helpers.
 */
export async function environment(html = "", options = {}) {
  const window = new Window({ url: "https://localhost/admin", settings: {
    enableJavaScriptEvaluation: true, disableJavaScriptFileLoading: true,
    suppressInsecureJavaScriptEnvironmentWarning: true,
  }});
  window.document.body.innerHTML = html;
  await window.happyDOM.whenAsyncComplete();
  const clock = FakeTimers.withGlobal(window).install({
    now: 100000, toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"],
  });
  const navigations = [];
  /** Record a requested browser navigation without leaving the test window.
   * @param {string} url - Same-origin request or navigation target.
   * @returns {number} Number of captured navigation requests.
   */
  window.location.assign = (url) => navigations.push(url);
  /** Record a requested new tab without opening an external browser.
   * @param {...unknown} args - Additional arguments forwarded to the simulated browser API.
   * @returns {number} Number of captured new-tab requests.
   */
  window.open = (...args) => navigations.push(args);
  /** Return the test’s configured answer to the destructive-action confirmation.
   * @returns {boolean} Configured response to the confirmation prompt.
   */
  window.confirm = () => true;
  /** Supply synthetic performance entries for browser metadata tests.
   * @returns {PerformanceEntry[]} Synthetic performance entries; empty unless the test supplies them.
   */
  window.performance.getEntriesByType = () => [];
  Object.defineProperty(window.document, "hidden", { configurable: true, value: false });
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: options.secure !== false });
  /** Replace a configurable browser API only inside the isolated test window.
   * @param {object} object - Isolated browser object whose API is replaced for a test.
   * @param {string} name - Permission, property, or script name being queried.
   * @param {unknown} value - Synthetic implementation or value replacing the browser property.
   * @returns {object} Original browser object after replacing the requested property.
   */
  const patch = (object, name, value) => Object.defineProperty(object, name, { configurable: true, writable: true, value });
  /** Evaluate a browser-script snippet in the isolated test window.
   * @param {string} script - Browser JavaScript source evaluated inside the isolated DOM.
   * @returns {unknown} Result of evaluating the supplied snippet in the isolated browser window.
   */
  const execute = (script) => new Script(script).runInContext(window);
  /** Execute the actual shipped JavaScript file rather than a copied implementation.
   * @param {string} name - Permission, property, or script name being queried.
   */
  const load = (name) => {
    const filename = resolve("src/flaretrace/static/" + name);
    new Script(readFileSync(filename, "utf8"), { filename }).runInContext(window);
  };
  return { window, document: window.document, clock, navigations, patch, execute, load,
    /** Advance deterministic timers and settle the resulting asynchronous work.
     * @param {number} milliseconds - Delay in milliseconds, advanced by fake timers in unit tests.
     * @returns {Promise<void>} Promise settled after pending timer-triggered browser work completes.
     */
    flush: (milliseconds = 0) => clock.tickAsync(milliseconds),
    /** Dispatch page teardown and release the isolated DOM and fake timers. */
    async close() {
      window.dispatchEvent(new window.Event("pagehide"));
      clock.reset();
      clock.uninstall();
      await window.happyDOM.close();
    },
  };
}

/** Create synthetic camera and location APIs for permission and upload regression tests.
 * @param {object} options - Explicit synthetic browser capabilities and failure switches.
 * @returns {Promise<BrowserHarness>} Browser harness with recorded submissions, synthetic streams, and capture controls.
 */
export async function capture(options = {}) {
  const testEnvironment = await environment(options.html ?? '<button id="enable-button">Share</button><button id="stop-button">Stop</button>', options);
  const { window, document, patch } = testEnvironment;
  if (!options.noConfig) document.body.dataset.config = JSON.stringify({ id: "synthetic", token: "test-token", duration: 1, destination: options.destination || "" });
  if (options.preview) { delete document.body.dataset.config; document.body.dataset.preview = "1"; }
  if (options.runtimeConfig) {
    const runtime = document.createElement("script");
    runtime.dataset.flaretraceRuntime = "";
    Object.assign(runtime.dataset, document.body.dataset);
    delete document.body.dataset.config; delete document.body.dataset.preview;
    document.body.append(runtime);
  }
  const calls = [], streams = [];
  let opening = 0;
  /** Create a named browser-style exception for a deterministic failure scenario.
   * @param {string} name - Permission, property, or script name being queried.
   * @returns {Error} Named error instance used by the configured failure scenario.
   */
  const error = (name) => Object.assign(new Error(name), { name });
  patch(window.navigator, "permissions", { /** Simulate a permission query with the configured state or failure.
   * @returns {Promise<object>} Synthetic permission descriptor containing its configured state.
   */
  query: async () => {
    if (options.permissionQueryError) throw error("NotSupportedError");
    return { state: options.permissionState || "granted" };
  }});
  patch(window.navigator, "geolocation", options.noLocation ? undefined : {
    /** Return synthetic coordinates or the configured permission failure.
     * @param {Function} success - Callback receiving synthetic coordinates.
     * @param {Function} failure - Callback receiving the configured geolocation error.
     */
    getCurrentPosition(success, failure) {
      if (options.locationHangs) return;
      window.setTimeout(() => options.locationError ? failure({ code: options.locationError }) : success({
        coords: { latitude: 51.5, longitude: 4.5, accuracy: 10, altitude: null, altitudeAccuracy: null, heading: null, speed: null }, timestamp: 1000,
      }), options.locationDelay || 0);
    },
  });
  patch(window.navigator, "mediaDevices", options.noCamera ? undefined : {
    /** Advertise the synthetic camera’s supported constraint names.
     * @returns {object} Supported synthetic video-constraint names.
     */
    getSupportedConstraints() { if (options.constraintsError) throw error("Error"); return { facingMode: true }; },
    /** List synthetic front and rear cameras without accessing real devices.
     * @returns {Promise<object[]>} Synthetic input-device descriptors, or an empty list when configured.
     */
    async enumerateDevices() {
      if (options.enumerationError) throw error("Error");
      return options.noEnumeration ? [] : [{ kind: "videoinput", deviceId: "front", label: "Front" }, { kind: "videoinput", deviceId: "rear", label: "Rear" }, { kind: "audioinput" }];
    },
    /** Return a synthetic camera stream or the configured device failure.
     * @param {object} constraints - Requested video-only camera constraints.
     * @returns {Promise<object>} Synthetic video-only stream whose tracks can be inspected and stopped.
     */
    async getUserMedia(constraints) {
      opening += 1;
      if (options.cameraHangs) return new Promise((resolve) => { testEnvironment.resolveCamera = resolve; });
      if (options.cameraError || (opening === 2 && options.secondCameraError)) throw error(options.cameraError || options.secondCameraError);
      const rear = options.rearFirst ? opening === 1 : opening === 2;
      const track = new window.EventTarget();
      track.readyState = "live";
      /** Stop the synthetic capture or recording and deliver its completion callbacks. */
      track.stop = () => { track.readyState = "ended"; };
      /** Report synthetic stream dimensions and facing metadata.
       * @returns {object} Synthetic track settings containing camera direction, device identifier, and dimensions.
       */
      track.getSettings = () => ({ facingMode: options.unidentified ? undefined : rear ? "environment" : "user",
        deviceId: options.sameCamera ? "same" : rear ? "rear" : "front", width: 1280, height: 720, frameRate: 30 });
      const stream = { /** Return the synthetic stream’s owned video track.
       * @returns {object[]} Single synthetic video track owned by the stream.
       */
      getTracks: () => [track], /** Return the synthetic stream’s video-only track list.
       * @returns {object[]} Single synthetic video track owned by the stream.
       */
      getVideoTracks: () => [track], constraints };
      streams.push(stream);
      return stream;
    },
  });
  const create = document.createElement.bind(document);
  /** Create a DOM element and supply deterministic video or canvas APIs.
   * @param {string} name - Permission, property, or script name being queried.
   * @param {...unknown} args - Additional arguments forwarded to the simulated browser API.
   * @returns {HTMLElement} Created element with deterministic video or canvas methods when relevant.
   */
  document.createElement = (name, ...args) => {
    const node = create(name, ...args);
    if (name === "video") {
      patch(node, "srcObject", null);
      patch(node, "videoWidth", options.noFrame ? 0 : 1920);
      patch(node, "videoHeight", options.noFrame ? 0 : 1080);
      /** Simulate preview playback or the configured device-read failure. */
      node.play = async () => { if (options.playError) throw error("NotReadableError"); };
    }
    if (name === "canvas") {
      /** Return a synthetic canvas context without a real graphics device.
       * @returns {object} Synthetic drawing context with a no-op drawImage method.
       */
      node.getContext = () => ({ /** Accept a synthetic video frame without rendering private image data. */
      drawImage() {} });
      /** Return synthetic encoded image bytes or a configured encoding failure.
       * @param {Function} callback - Callback receiving the synthetic encoded image or null.
       * @returns {unknown} Simulated browser result consumed by the current operation or assertion.
       */
      node.toBlob = (callback) => callback(options.encodeError ? null : new window.Blob(["photo"], { type: "image/jpeg" }));
    }
    return node;
  };
  /** Simulate a silent MediaRecorder using synthetic data and configurable failures.
   * @property {MediaStream} stream - Synthetic video-only stream owned by this recorder.
   * @property {string} mimeType - Selected synthetic recording MIME type.
   * @property {string} state - Recorder lifecycle state: inactive or recording.
   */
  class Recorder {
    /** Simulate MIME-type negotiation and configured recorder capability failures.
     * @param {string} type - Requested media MIME type.
     * @returns {boolean} Whether the configured synthetic recorder supports the requested MIME type.
     */
    static isTypeSupported(type) { if (options.formatError) throw error("Error"); return !options.noFormat && type.includes("webm"); }
    /** Initialize the synthetic recorder without accessing camera hardware.
     * @param {MediaStream} stream - Owned video-only stream to capture or release.
     * @param {object} settings - Camera settings, recorder configuration, or HTTP request options.
     */
    constructor(stream, settings) {
      if (options.recorderConstructorError) throw error("NotSupportedError");
      this.stream = stream;
      this.mimeType = options.invalidVideoFormat ? "video/unknown" : settings.mimeType || "video/webm";
      this.state = "inactive";
    }
    /** Start the synthetic capture or recording operation for this test. */
    start() {
      if (options.recorderStartError) throw error("NotSupportedError");
      this.state = "recording";
      if (options.recorderError) window.setTimeout(() => this.onerror(), 0);
      if (options.trackEnded) window.setTimeout(() => {
        this.stream.getVideoTracks()[0].readyState = "ended";
        this.stream.getVideoTracks()[0].dispatchEvent(new window.Event("ended"));
      }, 0);
    }
    /** Stop the synthetic capture or recording and deliver its completion callbacks. */
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      this.ondataavailable?.({ data: new window.Blob([options.emptyVideo ? "" : "video"]) });
      this.onstop?.();
    }
  }
  patch(window, "MediaRecorder", options.noRecorder ? undefined : Recorder);
  if (options.noRecorderFormatMethod) Recorder.isTypeSupported = undefined;
  /** Simulate an HTTP response and record the submitted evidence for assertions.
   * @param {string} url - Same-origin request or navigation target.
   * @param {object} settings - Camera settings, recorder configuration, or HTTP request options.
   * @returns {Promise<object>} Configured HTTP-style response after recording request arguments.
   */
  window.fetch = async (url, settings) => {
    const call = { url, settings, body: settings.headers["Content-Type"] === "application/json" ? JSON.parse(settings.body) : settings.body };
    calls.push(call);
    if (options.response) return options.response(call, calls);
    return { ok: true, status: 201, /** Decode the synthetic JSON response expected by the shipped browser script.
     * @returns {Promise<object>} Synthetic decoded JSON body expected by the capture script.
     */
    json: async () => ({ ok: true }) };
  };
  if (options.beforeLoad) options.beforeLoad(testEnvironment);
  testEnvironment.load("capture.js");
  return Object.assign(testEnvironment, { calls, streams, /** Read only JSON event submissions recorded by the capture harness.
   * @returns {object[]} Ordered JSON event payloads recorded by the synthetic HTTP handler.
   */
  events: () => calls.filter((call) => call.url.endsWith("/events")).map((call) => call.body),
    /** Start the synthetic capture or recording operation for this test. */
    start() { document.getElementById("enable-button")?.click(); }, /** Stop the synthetic capture or recording and deliver its completion callbacks. */
    stop() { document.getElementById("stop-button")?.click(); } });
}

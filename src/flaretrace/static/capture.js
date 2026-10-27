/** @file Implement capture interactions without third-party browser runtime dependencies. */
"use strict";

/** Signed configuration rendered by Flask; never contains administrator credentials.
 * @typedef {object} CaptureConfiguration
 * @property {string} id - Visit identifier used by the submission endpoints.
 * @property {string} token - Time-limited signature binding submissions to this visit.
 * @property {number} duration - Maximum silent-video duration in seconds.
 * @property {string} destination - Optional HTTP(S) destination after sharing finishes.
 * @property {number} retention - Legacy retention field; zero means explicit deletion only.
 */

(() => {
  const root = document.body;
  const runtime = document.querySelector("script[data-flaretrace-runtime]");
  const settings = runtime?.dataset || root?.dataset;
  if (!root || (!settings.config && !settings.preview)) return;
  /** @type {CaptureConfiguration|null} Null on administrator preview pages. */
  const config = settings.config ? JSON.parse(settings.config) : null;
  root.dataset.phase = config ? "idle" : "preview";
  /** Find a reserved capture control by its document identifier.
   * @param {string} id - DOM control or click-group identifier.
   * @returns {HTMLElement|null} Matching element, or null when the control is absent.
   */
  const element = (id) => document.getElementById(id);
  const shareButton = element("enable-button");
  const stopButtons = [element("disable-button"), element("stop-button")].filter(Boolean);
  const stopButton = stopButtons[0] || null;
  /** Update native and accessible disabled states for an optional control.
   * @param {HTMLElement|null} control - Optional authored sharing control.
   * @param {boolean|number|string} value - Value being normalized or applied by this helper.
   */
  function disabled(control, value) {
    if (!control) return;
    control.disabled = value;
    control.setAttribute("aria-disabled", String(value));
  }
  /** Bind pointer and keyboard activation to an authored capture control.
   * @param {HTMLElement|null} control - Optional authored sharing control.
   * @param {Function} action - Capture operation invoked after valid control activation.
   */
  function bind(control, action) {
    if (!control) return;
    control.addEventListener("click", (event) => {
      event.preventDefault();
      if (!control.disabled && control.getAttribute("aria-disabled") !== "true") action();
    });
    if (!["BUTTON", "INPUT"].includes(control.tagName)) {
      control.setAttribute("role", "button"); control.tabIndex = 0;
      control.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); control.click(); }
      });
    }
  }
  const preview = document.createElement("video");
  preview.autoplay = true;
  preview.muted = true;
  preview.playsInline = true;
  preview.hidden = true;
  const states = { location: "Waiting", front: "Waiting", rear: "Waiting" };
  /** @type {Set<MediaStream>} Owned cameras; all tracks are stopped during teardown. */
  const streams = new Set();
  /** @type {Set<AbortController>} In-flight requests cancelled when sharing stops. */
  const requests = new Set();
  /** @type {Set<string>} Permission categories eligible for one visitor-gesture retry. */
  const retries = new Set();
  let stopped = false;
  let running = false;
  let recording = null;
  let mediaCount = 0;
  let hasBegun = false;
  let hasAttempted = false;
  let metadataReady = Promise.resolve();
  let redirectTimer;

  /** Publish a capture feature’s current state on the document body.
   * @param {string} feature - Location, front-camera, or rear-camera capture category.
   * @param {string} label - Human-readable capture state exposed to the template.
   * @param {string} tone - Legacy status tone; authored templates retain their own styling.
   */
  function status(feature, label, tone = "neutral") {
    states[feature] = label;
    root.dataset[`${feature}State`] = label;
  }

  /** Create the standard cancellation error for a stopped capture.
   * @returns {DOMException} AbortError DOMException identifying visitor cancellation.
   */
  const abortError = () => new DOMException("Capture stopped", "AbortError");
  /** Prevent capture work from continuing after the visitor stops sharing. */
  const checkActive = () => { if (stopped) throw abortError(); };
  /** Wait for a bounded interval without blocking the browser event loop.
   * @param {number} milliseconds - Delay in milliseconds, advanced by fake timers in unit tests.
   * @returns {Promise<void>} Promise settled after the requested delay.
   */
  const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const errorCodes = new Set([
    "NotAllowedError", "NotFoundError", "NotReadableError", "OverconstrainedError",
    "SecurityError", "InvalidStateError", "NotSupportedError", "AbortError",
    "TimeoutError", "NetworkError", "UnknownError", "UploadError",
  ]);
  /** Reduce browser exceptions to the server’s accepted public error codes.
   * @param {Error} error - Browser or network exception whose public category is recorded.
   * @returns {string} Allowlisted public error code, or UnknownError.
   */
  const errorCode = (error) => errorCodes.has(error?.name) ? error.name : "UnknownError";
  /** Round finite diagnostic timings and cap them at five minutes.
   * @param {boolean|number|string} value - Value being normalized or applied by this helper.
   * @returns {number|null} Rounded bounded milliseconds, or null for invalid input.
   */
  const roundedMs = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.min(300000, Math.round(value / 10) * 10) : null;
  /** Keep a rounded duration only when the operation actually completed.
   * @param {boolean|number|string} value - Value being normalized or applied by this helper.
   * @returns {number|null} Rounded positive duration, or null before completion.
   */
  const completedMs = (value) => value > 0 ? roundedMs(value) : null;

  if (!config) {
    // Preview pages show the authored template but never request permissions.
    disabled(shareButton, true);
    stopButtons.forEach((control) => disabled(control, true));
    return;
  }

  /** Choose a supported silent-video MIME type without assuming recorder support.
   * @returns {string|null} Supported video MIME type, or null when recording is unavailable.
   */
  function recordingFormat() {
    if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return null;
    try {
      return ["video/webm;codecs=vp8", "video/mp4", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type)) || null;
    } catch { return null; }
  }

  /** Submit signed visit evidence with bounded timeouts and cancellation tracking.
   * @param {string} path - Visitor API suffix relative to the current signed visit.
   * @param {object|Blob} body - JSON event details or encoded media bytes.
   * @param {string} contentType - MIME type used to encode the request body.
   * @param {boolean} allowClosed - Whether the final event may be sent after local capture stops.
   * @returns {Promise<object>} Decoded server JSON; HTTP and network failures reject the request.
   */
  async function api(path, body, contentType = "application/json", allowClosed = false) {
    if (stopped && !allowClosed) throw abortError();
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), allowClosed ? 2500 : 15000);
    try {
      const response = await fetch(`${window.location.origin}/api/visits/${config.id}/${path}`, {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": contentType, "X-Visit-Token": config.token },
        body: contentType === "application/json" ? JSON.stringify(body) : body,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const failure = new Error("The server could not accept this submission.");
        failure.name = "UploadError";
        failure.httpStatus = response.status;
        throw failure;
      }
      return result;
    } catch (error) {
      if (error.name === "TypeError") {
        const failure = new Error("The request could not reach the server.");
        failure.name = "NetworkError";
        throw failure;
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      requests.delete(controller);
    }
  }

  /** Submit one typed event to the signed visitor API.
   * @param {string} kind - Validated event or media category sent to the server.
   * @param {object} data - JSON-compatible details belonging to the event.
   * @param {boolean} allowClosed - Whether the final event may be sent after local capture stops.
   * @returns {Promise<object>} Server acknowledgement after the event submission completes.
   */
  const event = (kind, data, allowClosed = false) => api("events", { kind, data }, "application/json", allowClosed);

  /** Record the outcome of a browser permission request.
   * @param {string} feature - Location, front-camera, or rear-camera capture category.
   * @param {object|string} state - Current permission state or retained click-group request state.
   * @param {string} reason - Bounded permission or capture-completion reason.
   */
  async function permission(feature, state, reason = "") {
    if (!stopped) await event("permission", { feature, state, reason });
  }

  /** Read a permission state while tolerating unsupported permission queries.
   * @param {string} name - Permission, property, or script name being queried.
   * @returns {Promise<string>} Permission state string, or unknown when querying is unsupported.
   */
  async function permissionState(name) {
    try { return (await navigator.permissions.query({ name })).state; }
    catch { return "unknown"; }
  }

  /** Collect non-secret browser capabilities and bounded performance metadata.
   * @returns {object} JSON-compatible browser capabilities and diagnostic metadata.
   */
  function browserDetails() {
    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    let navigation, firstPaint;
    try { navigation = performance.getEntriesByType("navigation")[0]; } catch { /* Optional API. */ }
    try { firstPaint = performance.getEntriesByType("paint").find((entry) => entry.name === "first-contentful-paint"); } catch { /* Optional API. */ }
    let facingMode = null;
    try { facingMode = Boolean(navigator.mediaDevices?.getSupportedConstraints?.().facingMode); } catch { /* Optional API. */ }
    return {
      userAgent: navigator.userAgent, platform: navigator.platform,
      language: navigator.language, languages: Array.from(navigator.languages || []),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      timezoneOffset: new Date().getTimezoneOffset(),
      viewport: { width: innerWidth, height: innerHeight },
      screen: { width: screen.width, height: screen.height, pixelRatio: devicePixelRatio,
        colorDepth: screen.colorDepth, orientation: screen.orientation?.type || null },
      touchPoints: navigator.maxTouchPoints || 0, cpuThreads: navigator.hardwareConcurrency || null,
      deviceMemoryGiB: navigator.deviceMemory || null,
      browserBrands: navigator.userAgentData?.toJSON?.() || null,
      connection: connection ? { effectiveType: connection.effectiveType, downlinkMbps: connection.downlink,
        rttMs: connection.rtt, saveData: connection.saveData } : null,
      capabilities: { secureContext: isSecureContext, camera: Boolean(navigator.mediaDevices?.getUserMedia),
        location: Boolean(navigator.geolocation), video: typeof MediaRecorder !== "undefined" },
      diagnostics: {
        // Select numeric fields; complete entries can contain page URLs.
        pageTiming: {
          responseMs: completedMs(navigation?.responseEnd),
          domReadyMs: completedMs(navigation?.domContentLoadedEventEnd),
          loadMs: completedMs(navigation?.loadEventEnd),
          firstContentfulPaintMs: completedMs(firstPaint?.startTime),
        },
        onlineHint: typeof navigator.onLine === "boolean" ? navigator.onLine : null,
        facingModeConstraint: facingMode,
        selectedRecordingFormat: recordingFormat(),
      },
    };
  }

  /** Request location once, recording permission outcomes and allowing a gesture retry.
   * @param {boolean} fromGesture - Whether a visitor activation permits retrying a blocked permission.
   */
  async function locate(fromGesture) {
    if (!navigator.geolocation) {
      status("location", "Not supported");
      await permission("location", "unsupported");
      return;
    }
    status("location", "Requesting…", "amber");
    await new Promise((resolve) => {
      let settled = false;
      /** Settle this location request once and cancel its deadline timer. */
      const finish = () => { if (!settled) { settled = true; clearTimeout(deadline); resolve(); } };
      const deadline = setTimeout(() => {
        if (!settled && !stopped) status("location", "Timed out", "amber");
        finish();
      }, 30000);
      navigator.geolocation.getCurrentPosition(async (position) => {
        if (stopped || settled) { finish(); return; }
        try {
          await metadataReady;
          await permission("location", "granted");
          const coordinates = position.coords;
          await event("location", { latitude: coordinates.latitude, longitude: coordinates.longitude, accuracy: coordinates.accuracy,
            altitude: coordinates.altitude, altitudeAccuracy: coordinates.altitudeAccuracy, heading: coordinates.heading,
            speed: coordinates.speed, timestamp: position.timestamp });
          if (!stopped) status("location", "Uploaded", "green");
        } catch (error) {
          if (!stopped) status("location", "Upload failed", "red");
        } finally { finish(); }
      }, async (error) => {
        if (stopped || settled) { finish(); return; }
        try {
          const state = await permissionState("geolocation");
          if (error.code === 1 && state !== "denied" && !fromGesture) {
            retries.add("location"); status("location", "Tap to enable", "amber");
          } else {
            status("location", error.code === 1 ? "Declined" : error.code === 3 ? "Timed out" : "Unavailable", "amber");
          }
          const reason = { 1: "permission_denied", 2: "position_unavailable", 3: "location_timeout" }[error.code] || "UnknownError";
          await permission("location", error.code === 1 ? "denied" : "error", reason);
        } catch { /* Keep the permission result visible even if reporting fails. */ }
        finally { finish(); }
      }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
    });
  }

  /** Open a video-only stream and stop tracks that arrive after cancellation.
   * @param {object} video - Video-only camera constraints; audio is never requested.
   * @returns {Promise<MediaStream>} Opened stream after cancellation checks; failures reject the promise.
   */
  async function openCamera(video) {
    checkActive();
    let expired = false;
    let timeout;
    const opening = navigator.mediaDevices.getUserMedia({ video, audio: false }).then((stream) => {
      if (expired || stopped) { stream.getTracks().forEach((track) => track.stop()); throw abortError(); }
      streams.add(stream);
      return stream;
    });
    try {
      return await Promise.race([opening, new Promise((_, reject) => {
        timeout = setTimeout(() => { expired = true; reject(new DOMException("Camera request timed out", "TimeoutError")); }, 30000);
      })]);
    } finally { clearTimeout(timeout); }
  }

  /** Stop every track in a stream and remove it from active capture state.
   * @param {MediaStream} stream - Owned video-only stream to capture or release.
   */
  function closeStream(stream) {
    stream.getTracks().forEach((track) => track.stop());
    streams.delete(stream);
    if (preview.srcObject === stream) preview.srcObject = null;
  }

  /** Upload one media blob and record bounded submission diagnostics.
   * @param {Blob} blob - Encoded still image or silent video to upload.
   * @param {string} kind - Validated event or media category sent to the server.
   * @param {string} facing - Front or rear camera designation attached to the evidence.
   */
  async function upload(blob, kind, facing) {
    checkActive();
    const started = performance.now();
    const format = blob.type.split(";")[0];
    try {
      await api(`media?kind=${kind}&facing=${facing}`, blob, format);
    } catch (error) {
      if (!stopped) {
        const data = { stage: "upload", outcome: "error", mediaKind: kind, facing, format,
          durationMs: roundedMs(performance.now() - started), bytes: blob.size, error: errorCode(error) };
        if (Number.isInteger(error.httpStatus)) data.httpStatus = error.httpStatus;
        await event("diagnostic", data).catch(() => {});
      }
      throw error;
    }
    mediaCount += 1;
    root.dataset.mediaCount = String(mediaCount);
    if (!stopped) await event("diagnostic", { stage: "upload", outcome: "success", mediaKind: kind,
      facing, format, durationMs: roundedMs(performance.now() - started), bytes: blob.size, httpStatus: 201 }).catch(() => {});
  }

  /** Encode the current preview frame as a JPEG without retaining a visible canvas.
   * @returns {Promise<Blob>} Encoded JPEG blob for the current preview frame.
   */
  async function photo() {
    if (!preview.videoWidth || !preview.videoHeight) throw new Error("The camera has not produced an image.");
    const canvas = document.createElement("canvas");
    const scale = Math.min(1, 1280 / preview.videoWidth);
    canvas.width = Math.round(preview.videoWidth * scale);
    canvas.height = Math.round(preview.videoHeight * scale);
    canvas.getContext("2d").drawImage(preview, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    if (!blob) throw new Error("Could not encode the camera photo.");
    return blob;
  }

  /** Record a short silent clip and release timers and listeners on completion.
   * @param {MediaStream} stream - Owned video-only stream to capture or release.
   * @param {string} facing - Front or rear camera designation attached to the evidence.
   * @returns {Promise<Blob|null>} Encoded silent-video blob after the recorder finishes.
   */
  async function video(stream, facing) {
    if (typeof MediaRecorder === "undefined") return null;
    const mimeType = recordingFormat();
    const options = { videoBitsPerSecond: 1000000 };
    if (mimeType) options.mimeType = mimeType;
    return new Promise((resolve, reject) => {
      const recorder = new MediaRecorder(stream, options);
      recording = recorder;
      const chunks = [];
      let end;
      const track = stream.getVideoTracks()[0];
      /** Finish the current recording when its camera track ends. */
      const trackEnded = () => { if (recorder.state !== "inactive") recorder.stop(); };
      /** Release recording timers and listeners after success, failure, or cancellation. */
      const clear = () => {
        clearTimeout(end);
        track.removeEventListener("ended", trackEnded);
        if (!stopped) {
          root.dataset.phase = "running";
        }
        if (recording === recorder) recording = null;
      };
      /** Keep nonempty recorder chunks for the current silent clip.
       * @param {Event} event - Browser input, lifecycle, or recorder event.
       */
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      /** Reject the pending recording when the recorder reports an error. */
      recorder.onerror = () => { clear(); reject(new Error("The browser could not record video.")); };
      /** Resolve the completed clip or reject an empty recording. */
      recorder.onstop = () => {
        clear();
        if (stopped) { reject(abortError()); return; }
        if (track.readyState === "ended") { reject(new Error("Camera access ended during recording.")); return; }
        const type = recorder.mimeType.split(";")[0];
        if (!["video/webm", "video/mp4"].includes(type)) { reject(new Error("This browser's recording format is unsupported.")); return; }
        const blob = new Blob(chunks, { type });
        if (!blob.size) { reject(new Error("The camera produced an empty video.")); return; }
        resolve(blob);
      };
      track.addEventListener("ended", trackEnded, { once: true });
      try { recorder.start(250); }
      catch (error) { clear(); reject(error); return; }
      root.dataset.phase = "recording";
      end = setTimeout(() => { if (recorder.state !== "inactive") recorder.stop(); }, config.duration * 1000);
    });
  }

  /** Collect and upload a still image and supported silent clip from one camera.
   * @param {MediaStream} stream - Owned video-only stream to capture or release.
   * @param {string} facing - Front or rear camera designation attached to the evidence.
   */
  async function captureCamera(stream, facing) {
    checkActive();
    status(facing, "Capturing…", "amber");
    preview.srcObject = stream;
    // This video element is always muted and inline, including on iOS.
    await preview.play();
    await pause(650);
    checkActive();
    const settings = stream.getVideoTracks()[0].getSettings();
    await event("capture_started", { facing, width: settings.width, height: settings.height,
      frameRate: settings.frameRate, notice_version: 3 });
    await upload(await photo(), "photo", facing);
    checkActive();
    const movie = await video(stream, facing);
    if (movie) {
      await event("diagnostic", { stage: "recording", outcome: "success", mediaKind: "video", facing, format: movie.type }).catch(() => {});
      status(facing, "Uploading…", "amber");
      await upload(movie, "video", facing);
      if (!stopped) status(facing, "Photo + video uploaded", "green");
    } else if (!stopped) {
      status(facing, "Photo only; video unsupported", "amber");
      await event("diagnostic", { stage: "recording", outcome: "unsupported", mediaKind: "video", facing }).catch(() => {});
    }
  }

  /** Record a bounded camera failure without exposing raw browser messages.
   * @param {Error} error - Browser or network exception whose public category is recorded.
   * @param {string} facing - Front or rear camera designation attached to the evidence.
   */
  async function cameraError(error, facing) {
    if (stopped) return;
    status(facing, "Capture failed", "red");
    await event("capture_error", { facing, reason: errorCode(error) }).catch(() => {});
  }

  /** Request the first camera and track whether a gesture retry is possible.
   * @param {boolean} fromGesture - Whether a visitor activation permits retrying a blocked permission.
   * @returns {Promise<MediaStream|null>} Opened first stream, or null after a handled camera failure.
   */
  async function requestFirstCamera(fromGesture) {
    status("front", "Requesting…", "amber");
    try {
      return await openCamera({ facingMode: { ideal: "user" }, width: { ideal: 1280 }, height: { ideal: 720 } });
    } catch (error) {
      if (stopped) return null;
      const state = await permissionState("camera");
      const denied = error.name === "NotAllowedError";
      if (denied && state !== "denied" && !fromGesture) {
        retries.add("camera");
        status("front", "Tap to enable", "amber");
      } else {
        status("front", denied ? "Declined" : error.name === "TimeoutError" ? "Timed out" : "Unavailable", "amber");
      }
      status("rear", "Not accessed");
      await permission("camera", denied ? "denied" : "error", errorCode(error)).catch(() => {});
      return null;
    }
  }

  /** Capture the opened camera and identify the opposite facing direction.
   * @param {MediaStream} stream - Owned video-only stream to capture or release.
   * @returns {Promise<object>} First-camera settings, enumerated video devices, and opposite facing direction.
   */
  async function captureFirstCamera(stream) {
    await metadataReady;
    await permission("camera", "granted");
    const settings = stream.getVideoTracks()[0].getSettings();
    const facing = settings.facingMode === "environment" ? "rear" : "front";
    let devices = [];
    try {
      devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput");
    } catch { /* Facing-mode constraints still work without device enumeration. */ }
    try { await captureCamera(stream, facing); }
    catch (error) { await cameraError(error, facing); }
    finally { closeStream(stream); }
    return { settings, devices, secondFacing: facing === "front" ? "rear" : "front" };
  }

  /** Try a distinct opposite-facing camera without duplicating the first device.
   * @param {object} settings - Camera settings, recorder configuration, or HTTP request options.
   * @param {MediaDeviceInfo[]} devices - Enumerated devices used to select a distinct second camera.
   * @param {string} facing - Front or rear camera designation attached to the evidence.
   */
  async function captureSecondCamera(settings, devices, facing) {
    status(facing, "Checking…", "amber");
    const labelPattern = facing === "rear" ? /back|rear|environment/i : /front|user|facetime/i;
    const candidate = devices.find((device) => device.deviceId !== settings.deviceId && labelPattern.test(device.label));
    let stream;
    try {
      stream = await openCamera(candidate
        ? { deviceId: { exact: candidate.deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
        : { facingMode: { exact: facing === "rear" ? "environment" : "user" }, width: { ideal: 1280 }, height: { ideal: 720 } });
      const secondSettings = stream.getVideoTracks()[0].getSettings();
      const sameDevice = settings.deviceId && secondSettings.deviceId === settings.deviceId;
      const expectedMode = facing === "rear" ? "environment" : "user";
      if (sameDevice || !(candidate || secondSettings.facingMode === expectedMode)) {
        status(facing, "Not available");
        return;
      }
      await captureCamera(stream, facing);
    } catch (error) {
      if (!stopped) {
        if (["OverconstrainedError", "NotFoundError", "NotAllowedError", "NotReadableError"].includes(error.name)) status(facing, "Not available");
        else await cameraError(error, facing);
      }
    } finally { if (stream) closeStream(stream); }
  }

  /** Coordinate sequential camera access while preserving visitor cancellation.
   * @param {boolean} fromGesture - Whether a visitor activation permits retrying a blocked permission.
   */
  async function cameras(fromGesture) {
    if (!navigator.mediaDevices?.getUserMedia) {
      status("front", "Not supported");
      status("rear", "Not supported");
      await permission("camera", "unsupported");
      return;
    }
    const firstStream = await requestFirstCamera(fromGesture);
    if (!firstStream) return;
    try {
      const first = await captureFirstCamera(firstStream);
      checkActive();
      await captureSecondCamera(first.settings, first.devices, first.secondFacing);
    } finally {
      if (streams.has(firstStream)) closeStream(firstStream);
    }
  }

  /** Stop capture devices, cancel pending requests, and clear preview state. */
  function release() {
    if (recording && recording.state !== "inactive") recording.stop();
    streams.forEach(closeStream);
    requests.forEach((controller) => controller.abort());
    preview.srcObject = null;
  }

  /** Close capture once, record the outcome, and optionally schedule the destination.
   * @param {string} reason - Bounded permission or capture-completion reason.
   */
  async function finish(reason = "complete") {
    if (stopped) return;
    stopped = true;
    release();
    root.dataset.phase = reason === "complete" ? "finished" : "stopped";
    ["location", "front", "rear"].forEach((feature) => {
      if (/Waiting|Requesting|Capturing|Uploading|Checking|Tap to/.test(states[feature])) status(feature, "Stopped");
    });
    disabled(shareButton, true);
    if (config.destination && stopButton) {
      redirectTimer = setTimeout(() => { window.location.assign(config.destination); }, 2500);
    }
    await event("finished", { reason }, true).then(() => {
      root.dataset.serverClosed = "true";
    }).catch(() => {
      // Camera teardown never depends on a successful network request.
      root.dataset.serverClosed = "error";
    });
  }

  /** Submit browser metadata once before permission-derived evidence. */
  function beginMetadataSubmission() {
    if (hasBegun) return;
    metadataReady = event("browser", browserDetails());
    metadataReady.catch(() => {});
    hasBegun = true;
  }

  /** Record that permission-dependent capture requires HTTPS or localhost. */
  async function reportInsecureContext() {
    ["location", "front", "rear"].forEach((feature) => status(feature, "HTTPS required", "amber"));
    await permission("location", "blocked", "insecure_context");
    await permission("camera", "blocked", "insecure_context");
  }

  /** Run location and camera collection while retaining independent failures. */
  async function collectRequestedEvidence() {
    const retryAttempt = hasAttempted;
    const needed = hasAttempted ? new Set(retries) : new Set(["location", "camera"]);
    hasAttempted = true;
    retries.clear();
    // Location resolves first; stopping prevents the next permission request.
    if (needed.has("location")) await locate(retryAttempt);
    checkActive();
    if (needed.has("camera")) await cameras(retryAttempt);
    await metadataReady;
  }

  /** Re-enable sharing only when a browser requires a visitor gesture. */
  function offerPermissionRetry() {
    root.dataset.phase = "awaiting-gesture";
    disabled(shareButton, false);
  }

  /** Start one capture attempt and coordinate its completion and retry state. */
  async function run() {
    if (running || stopped) return;
    running = true;
    root.dataset.phase = "running";
    disabled(shareButton, true);
    try {
      beginMetadataSubmission();
      checkActive();
      if (!isSecureContext) {
        await reportInsecureContext();
        return;
      }
      await collectRequestedEvidence();
      if (stopped) return;
      if (retries.size) offerPermissionRetry();
      else await finish();
    } catch {
      if (!stopped) {
        release();
      }
    } finally { running = false; }
  }

  bind(shareButton, () => { void run(); });
  /** Handle the visitor’s stop control or continue to the configured destination. */
  const stopAction = () => {
    if (!stopped) void finish(hasBegun ? "stopped" : "declined");
    else if (config.destination) { clearTimeout(redirectTimer); window.location.assign(config.destination); }
  };
  stopButtons.forEach((control) => bind(control, stopAction));
  window.addEventListener("pagehide", () => { stopped = true; release(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && hasBegun && !stopped) void finish("page_hidden");
  });
  // A previous browser grant is not treated as a new capture action.
  // No sensor API is called until the explicit sharing button is pressed.
})();

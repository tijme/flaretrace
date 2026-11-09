/** @file Implement admin interactions without third-party browser runtime dependencies. */
"use strict";

// No caching: every admin response already sets Cache-Control: no-store, and
// pages here can include IP addresses, locations, and submitted media. This
// registers a deliberately no-op service worker (see static/sw.js) only so
// the browser's installability criteria are met and the workspace can be
// added to a phone's home screen.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/admin/" }).catch(() => {});
}

/** Retained state for one lazily loaded click group.
 * @typedef {object} ClickGroupState
 * @property {boolean} open - Whether the group’s click rows are expanded.
 * @property {string} html - Last successfully rendered group-body markup.
 * @property {string|null} version - Server version belonging to the cached markup.
 * @property {boolean} loading - Whether a group request is currently in flight.
 * @property {AbortController} [controller] - Cancellation handle for the current request.
 * @property {object} [scrollPosition] - Saved horizontal and vertical scroll offsets.
 */

/** Make navigable evidence rows reachable through keyboard focus.
 * @param {Document|Element} scope - Document fragment containing navigable evidence rows.
 */
function prepareRows(scope) {
  scope.querySelectorAll("tr[data-href]").forEach((row) => { row.tabIndex = 0; });
}
prepareRows(document);
// Delegation also covers rows received by background refresh.
/** Open a navigable row without intercepting selections or embedded controls.
 * @param {Event} event - Browser input, lifecycle, or recorder event.
 */
function openRow(event) {
  const row = event.target.closest("tr[data-href]");
  if (!row || event.target.closest("a,button,input,select,textarea,label,summary,[contenteditable]")) return;
  if (window.getSelection()?.toString()) return;
  if (event.type === "keydown") {
    if (event.target !== row || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
  }
  if (event.type === "auxclick") event.preventDefault();
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1) {
    window.open(row.dataset.href, "_blank", "noopener");
  } else window.location.assign(row.dataset.href);
}
document.addEventListener("click", openRow);
document.addEventListener("auxclick", (event) => {
  if (event.button === 1) openRow(event);
});
document.addEventListener("keydown", openRow);

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const input = document.getElementById(button.dataset.copy);
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(input.value);
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy link"; }, 1800);
    } catch {
      input.focus();
      input.select();
      button.textContent = "Select & copy";
    }
  });
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.dataset.confirm)) event.preventDefault();
});

/** @type {Map<string, ClickGroupState>} Cached groups keyed by their persisted identifiers. */
const clickGroups = new Map();
/** Find the expandable click-group body in the current document.
 * @param {string} id - DOM control or click-group identifier.
 * @returns {Element|undefined} Current group body, or undefined when the group is absent.
 */
function currentGroupBody(id) {
  return document.getElementById("clicks-" + id)?.querySelector("[data-group-body]");
}
/** Cache expanded group markup and scroll positions before a live update.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 */
function saveGroups(region) {
  region.querySelectorAll("[data-group-body]").forEach((body) => {
    const state = clickGroups.get(body.dataset.groupBody);
    if (state && body.querySelector("[data-click-rows]")) {
      state.html = body.innerHTML;
      const scroll = body.querySelector(".table-scroll");
      if (scroll) state.scrollPosition = {left: scroll.scrollLeft, top: scroll.scrollTop};
    }
  });
}
/** Fetch and validate an authenticated click-group HTML fragment.
 * @param {string} pageUrl - Absolute click-group pagination URL.
 * @param {AbortSignal} signal - Cancellation signal shared by related page requests.
 * @returns {Promise<Element>} Validated detached group-page element.
 */
async function fetchGroupPage(pageUrl, signal) {
  const response = await fetch(pageUrl, {
    credentials: "same-origin", cache: "no-store", signal,
    headers: { "X-Group-Fragment": "1" },
  });
  if (!response.ok || response.redirected) throw new Error("Could not load clicks");
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  const fragment = page.querySelector(".group-page");
  if (!fragment) throw new Error("Invalid click response");
  prepareRows(fragment);
  return fragment;
}

/** Append older click rows while skipping identifiers already displayed.
 * @param {Element} existingRows - Current click-row container to preserve and extend.
 * @param {Element} incomingRows - Fetched click-row container containing older records.
 */
function appendUniqueRows(existingRows, incomingRows) {
  const known = new Set(Array.from(existingRows.children).map((row) => row.dataset.clickId));
  Array.from(incomingRows.children).forEach((row) => {
    if (!known.has(row.dataset.clickId)) existingRows.appendChild(row);
  });
}

/** Refetch enough group pages to preserve the visitor’s loaded history.
 * @param {string} url - Same-origin request or navigation target.
 * @param {AbortSignal} signal - Cancellation signal shared by related page requests.
 * @param {number} pageCount - Number of loaded history pages to preserve during refresh.
 * @returns {Promise<Element>} Group-page element containing the requested history depth.
 */
async function fetchLoadedGroupPages(url, signal, pageCount) {
  const incoming = await fetchGroupPage(url, signal);
  const rows = incoming.querySelector("[data-click-rows]");
  for (let pageNumber = 1; pageNumber < pageCount; pageNumber += 1) {
    const more = incoming.querySelector("[data-group-more]");
    if (!more) break;
    const pageUrl = new URL(more.getAttribute("href"), window.location.href).href;
    const olderPage = await fetchGroupPage(pageUrl, signal);
    appendUniqueRows(rows, olderPage.querySelector("[data-click-rows]"));
    incoming.querySelector("[data-group-pagination]").innerHTML = olderPage.querySelector("[data-group-pagination]").innerHTML;
  }
  return incoming;
}

/** Apply click-group content while retaining scroll and pagination state.
 * @param {string} id - DOM control or click-group identifier.
 * @param {Element} incoming - Validated detached HTML fragment returned by the server.
 * @param {boolean} append - Whether older rows should be appended instead of replacing the group.
 * @param {object|string} state - Current permission state or retained click-group request state.
 * @param {string} version - Server-provided click-group version captured before the request.
 */
function applyGroupPage(id, incoming, append, state, version) {
  const body = currentGroupBody(id);
  if (!body) return;
  const scroll = body.querySelector(".table-scroll");
  const position = { left: scroll?.scrollLeft || 0, top: scroll?.scrollTop || 0 };
  const existingRows = body.querySelector("[data-click-rows]");
  if (append && existingRows) {
    appendUniqueRows(existingRows, incoming.querySelector("[data-click-rows]"));
    body.querySelector("[data-group-pagination]").innerHTML = incoming.querySelector("[data-group-pagination]").innerHTML;
  } else body.innerHTML = incoming.outerHTML;
  const refreshedScroll = body.querySelector(".table-scroll");
  if (refreshedScroll) {
    refreshedScroll.scrollLeft = position.left;
    refreshedScroll.scrollTop = position.top;
  }
  const total = Number(body.dataset.version.split(":")[0]);
  if (body.querySelectorAll("[data-click-rows] tr[data-click-id]").length >= total) {
    body.querySelector("[data-group-pagination]").innerHTML = '<span class="muted">All clicks in this group are loaded.</span>';
  }
  state.html = body.innerHTML;
  state.version = version;
}

/** Show an accessible retry control after a group request fails.
 * @param {string} id - DOM control or click-group identifier.
 */
function reportGroupFailure(id) {
  const body = currentGroupBody(id);
  if (!body) return;
  const feedback = body.querySelector(".group-feedback");
  if (feedback) {
    feedback.textContent = "Could not load clicks. Try again.";
    return;
  }
  body.innerHTML = '<p class="group-loading">Could not load clicks. <button type="button" class="group-toggle" data-group-retry>Try again</button></p>';
  body.querySelector("[data-group-retry]").dataset.groupRetry = id;
}

/** Load one group operation with timeout, cancellation, and duplicate-request protection.
 * @param {string} id - DOM control or click-group identifier.
 * @param {string} url - Same-origin request or navigation target.
 * @param {boolean} append - Whether older rows should be appended instead of replacing the group.
 * @param {boolean} refreshing - Whether already-loaded history should be refetched.
 */
async function loadGroup(id, url, append = false, refreshing = false) {
  const state = clickGroups.get(id);
  if (!state || state.loading) return;
  state.loading = true;
  const version = currentGroupBody(id)?.dataset.version;
  const controller = new AbortController();
  state.controller = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const loadedCount = currentGroupBody(id)?.querySelectorAll("[data-click-rows] tr[data-click-id]").length || 0;
    const pageCount = refreshing ? Math.max(1, Math.ceil(loadedCount / 50)) : 1;
    const incoming = await fetchLoadedGroupPages(url, controller.signal, pageCount);
    applyGroupPage(id, incoming, append, state, version);
  } catch {
    reportGroupFailure(id);
  } finally {
    clearTimeout(timeout);
    state.loading = false;
  }
}

/** Restore expanded groups and refresh cached content when its version changes.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 * @param {boolean} fetchChanges - Whether changed group versions should trigger an HTTP request.
 */
function restoreGroups(region, fetchChanges = true) {
  region.querySelectorAll("[data-group-body]").forEach((body) => {
    const id = body.dataset.groupBody;
    const state = clickGroups.get(id);
    if (!state?.open) return;
    body.closest(".group-children").hidden = false;
    Array.from(region.querySelectorAll("[data-group-toggle]")).find((button) => button.dataset.groupToggle === id)?.setAttribute("aria-expanded", "true");
    if (state.html) {
      body.innerHTML = state.html;
      const scroll = body.querySelector(".table-scroll");
      if (scroll && state.scrollPosition) {
        scroll.scrollLeft = state.scrollPosition.left;
        scroll.scrollTop = state.scrollPosition.top;
      }
    }
    if (fetchChanges && (!state.html || state.version !== body.dataset.version)) {
      void loadGroup(id, body.dataset.groupUrl, false, Boolean(state.html));
    }
  });
}
/** Expand or collapse one click group and lazily load its contents.
 * @param {string} id - DOM control or click-group identifier.
 */
function toggleGroup(id) {
  const body = currentGroupBody(id);
  if (!body) return;
  let state = clickGroups.get(id);
  if (!state) {
    state = {open: false, html: "", version: null, loading: false};
    clickGroups.set(id, state);
  }
  state.open = !state.open;
  document.getElementById("clicks-" + id).hidden = !state.open;
  document.getElementById("toggle-" + id)?.setAttribute("aria-expanded", String(state.open));
  if (state.open && state.html) {
    body.innerHTML = state.html;
    const scroll = body.querySelector(".table-scroll");
    if (scroll && state.scrollPosition) {
      scroll.scrollLeft = state.scrollPosition.left;
      scroll.scrollTop = state.scrollPosition.top;
    }
  }
  if (state.open && (!state.html || state.version !== body.dataset.version)) {
    void loadGroup(id, body.dataset.groupUrl, false, Boolean(state.html));
  }
}
document.addEventListener("click", (event) => {
  const retry = event.target.closest("[data-group-retry]");
  if (retry) { event.preventDefault(); const body = currentGroupBody(retry.dataset.groupRetry); if (body) void loadGroup(retry.dataset.groupRetry, body.dataset.groupUrl); return; }
  const more = event.target.closest("[data-group-more]");
  if (more) {
    const body = more.closest("[data-group-body]");
    if (body) { event.preventDefault(); void loadGroup(body.dataset.groupBody, more.href, true); }
    return;
  }
  const button = event.target.closest("[data-group-toggle]");
  if (button) { event.preventDefault(); toggleGroup(button.dataset.groupToggle); return; }
  const row = event.target.closest("[data-expand-group]");
  if (row && !event.target.closest("a,button,input,select,textarea,summary") && !window.getSelection()?.toString()) toggleGroup(row.dataset.expandGroup);
});
document.addEventListener("keydown", (event) => {
  const row = event.target.closest("[data-expand-group]");
  if (row && event.target === row && ["Enter", " "].includes(event.key)) {
    event.preventDefault(); toggleGroup(row.dataset.expandGroup);
  }
});
window.addEventListener("pagehide", () => clickGroups.forEach((state) => state.controller?.abort()));

/** Determine whether a live update would interrupt focus, selection, or playback.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 * @returns {object} Focus targets and a flag indicating whether replacement is unsafe.
 */
function regionInteraction(region) {
  const focused = document.activeElement;
  const inside = region.contains(focused);
  const focusedGroup = inside && focused.matches("[data-group-toggle]") ? focused.id : null;
  const focusedDisclosure = inside && focused.matches("summary") ? focused.closest("details[id]")?.id : null;
  const selection = window.getSelection();
  const blocked = (inside && !focusedGroup && !focusedDisclosure)
    || (selection?.toString() && region.contains(selection.anchorNode))
    || Array.from(region.querySelectorAll("video")).some((video) => !video.paused);
  return { focusedGroup, focusedDisclosure, blocked };
}

/** Capture scroll positions and open disclosures before replacing live content.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 * @returns {object} Scroll-position and disclosure-state maps keyed by stable identifiers.
 */
function regionSnapshot(region) {
  const scroll = new Map(Array.from(region.querySelectorAll(".table-scroll")).map((item, index) => [
    item.closest("[data-group-body]")?.dataset.groupBody || "root-" + index,
    { left: item.scrollLeft, top: item.scrollTop },
  ]));
  const expanded = new Map(Array.from(region.querySelectorAll("details[id]")).map((item) => [item.id, item.open]));
  return { scroll, expanded };
}

/** Restore saved scroll, disclosure, and keyboard-focus state after refresh.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 * @param {object} snapshot - Saved scroll positions and disclosure states.
 * @param {object} interaction - Focus and selection metadata captured before DOM replacement.
 */
function restoreRegionSnapshot(region, snapshot, interaction) {
  if (interaction.focusedGroup) document.getElementById(interaction.focusedGroup)?.focus({ preventScroll: true });
  region.querySelectorAll(".table-scroll").forEach((item, index) => {
    const position = snapshot.scroll.get(item.closest("[data-group-body]")?.dataset.groupBody || "root-" + index);
    if (position) {
      item.scrollLeft = position.left;
      item.scrollTop = position.top;
    }
  });
  region.querySelectorAll("details[id]").forEach((item) => {
    if (snapshot.expanded.has(item.id)) item.open = snapshot.expanded.get(item.id);
  });
  if (interaction.focusedDisclosure) document.getElementById(interaction.focusedDisclosure)?.querySelector("summary")?.focus({ preventScroll: true });
}

/** Replace changed live content only when existing interactions remain safe.
 * @param {Element} region - Live administrator region being inspected or refreshed.
 * @param {Element} incoming - Validated detached HTML fragment returned by the server.
 */
function reconcileLiveRegion(region, incoming) {
  saveGroups(region);
  restoreGroups(incoming, false);
  incoming.querySelectorAll("details[id]").forEach((item) => {
    const current = region.querySelector("#" + CSS.escape(item.id));
    if (current) item.open = current.open;
  });
  if (incoming.innerHTML === region.innerHTML) {
    restoreGroups(region);
    return;
  }
  const interaction = regionInteraction(region);
  if (interaction.blocked) return;
  const snapshot = regionSnapshot(region);
  saveGroups(region);
  region.innerHTML = incoming.innerHTML;
  restoreGroups(region);
  restoreRegionSnapshot(region, snapshot, interaction);
}

(() => {
  const status = document.querySelector("[data-live-status]");
  const regions = Array.from(document.querySelectorAll("[data-live-region]"));
  if (!status || !regions.length) return;
  let timer;
  let controller;
  let fetching = false;
  let active = true;
  let delay = 2000;
  /** Schedule the next refresh only while the page remains active and visible. */
  const schedule = () => {
    clearTimeout(timer);
    if (active && !document.hidden) timer = setTimeout(refresh, delay);
  };
  /** Fetch authenticated page fragments with retry backoff and access-loss handling. */
  async function refresh() {
    if (!active || document.hidden || fetching) return;
    fetching = true;
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(window.location.href, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: { "Accept": "text/html" },
      });
      if (response.redirected || [401, 403, 404].includes(response.status)) {
        active = false;
        status.textContent = "Live updates stopped. Reload to check access to this page.";
        return;
      }
      if (!response.ok) throw new Error("Refresh unavailable");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!page.querySelector("[data-live-status]")) throw new Error("Invalid refresh response");
      prepareRows(page);
      for (const region of regions) {
        const incoming = page.getElementById(region.id);
        if (incoming) reconcileLiveRegion(region, incoming);
      }
      delay = 2000;
      status.textContent = "Live updates every 2 seconds.";
      status.title = "Last checked: " + new Date().toLocaleTimeString();
    } catch {
      if (!document.hidden && active) {
        delay = Math.min(delay * 2, 30000);
        status.textContent = "Live updates unavailable. Retrying automatically.";
      }
    } finally {
      clearTimeout(timeout);
      fetching = false;
      schedule();
    }
  }

  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (document.hidden) controller?.abort();
    else { delay = 2000; void refresh(); }
  });
  window.addEventListener("pagehide", () => {
    active = false;
    clearTimeout(timer);
    controller?.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { active = true; delay = 2000; void refresh(); }
  });
  schedule();
})();

/** @file Deliberately no-op service worker that only enables home-screen install. */
"use strict";

// Never cache or intercept a response: every admin page already sets
// Cache-Control: no-store, and anything shown there can include IP
// addresses, locations, and submitted media. A fetch handler that never
// calls event.respondWith() lets every request pass straight through to
// the network, byte-for-byte unmodified, exactly as if this file did not
// exist at all.
self.addEventListener("fetch", () => {});

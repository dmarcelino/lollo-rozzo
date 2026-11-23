// The vault's old service worker, replaced by this one so that devices that
// installed it drop it: a 404 does not remove an installed service worker, a
// changed script does. Notifications now come from the lollo's own service
// worker (/notifications/sw.js, scope /), and no vault page registers this one.
// A later release removes this file and its route.
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.registration.unregister()));

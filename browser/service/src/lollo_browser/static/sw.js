// The browser's pages no longer register a service worker: notifications come from the lollo's
// notifications service, whose own worker is registered on /notifications/. This one stays only to
// remove the worker earlier releases installed at /browser/: a 404 does not remove an installed
// service worker, a changed script does. A later release removes this file.
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.registration.unregister()));

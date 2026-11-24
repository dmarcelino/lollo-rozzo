// The lollo host's one service worker, registered by /notifications/ at scope /.
// It shows the lollo's notifications and, on a tap, opens the page a notification
// names, only when that page is a lollo page it knows, else the landing page. It
// reuses an open window only when that window is already on the same lollo page,
// or on the landing page or /notifications/, so a tap never navigates away from
// work on another page (a browser take-over, a half-filled vault form). It
// has no fetch handler: nothing is cached, so every page load goes through
// Cloudflare Access. A notification has no action buttons: a tap only opens a
// page, where the person answers.
"use strict";

// The pages a tap may open; anything else opens the landing page.
const PREFIXES = ["/vault/", "/browser/", "/notifications/"];

// The URL a tap on a notification linking to `link` opens, on `origin`.
function tapTarget(link, origin) {
  const root = new URL("/", origin).href;
  let target;
  try {
    target = new URL(typeof link === "string" && link ? link : "/", origin);
  } catch {
    return root;
  }
  if (target.origin !== new URL(origin).origin) return root;
  if (!PREFIXES.some((prefix) => target.pathname.startsWith(prefix))) return root;
  return target.href;
}

// True when a tap that opens `url` may navigate the window at `windowUrl`: it is
// on this origin and on the landing page, under /notifications/, or under the
// prefix `url` is under.
function reusable(windowUrl, url, origin) {
  let at;
  let target;
  try {
    at = new URL(windowUrl);
    target = new URL(url);
  } catch {
    return false;
  }
  const own = new URL(origin).origin;
  if (at.origin !== own || target.origin !== own) return false;
  if (at.pathname === "/" || at.pathname.startsWith("/notifications/")) return true;
  const prefix = PREFIXES.find((p) => target.pathname.startsWith(p));
  return prefix !== undefined && at.pathname.startsWith(prefix);
}

// Navigates an open window that may be reused to `url` and focuses it, else
// opens a new one.
async function openPage(clients, url, origin) {
  const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of windows) {
    if (!reusable(client.url, url, origin) || !("navigate" in client)) continue;
    try {
      // navigate() works only in a window this worker controls; others are skipped.
      const navigated = await client.navigate(url);
      if (navigated) return await navigated.focus();
    } catch {
      // Not ours (the code editor's pages have their own worker): try the next.
    }
  }
  return clients.openWindow(url);
}

if (typeof self !== "undefined" && self.registration) {
  const ICON = new URL("/notifications/icon-192.png", self.location.origin).href;

  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

  self.addEventListener("push", (event) => {
    let data = {};
    try {
      data = event.data ? event.data.json() : {};
    } catch {
      data = {};
    }
    event.waitUntil(
      self.registration.showNotification(typeof data.title === "string" && data.title ? data.title : "Lollo", {
        body: typeof data.body === "string" ? data.body : "",
        tag: typeof data.tag === "string" && data.tag ? data.tag : "lollo",
        icon: ICON,
        badge: ICON,
        requireInteraction: true,
        data: { url: typeof data.url === "string" ? data.url : "/" },
      }),
    );
  });

  self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const origin = self.location.origin;
    event.waitUntil(openPage(self.clients, tapTarget(event.notification.data?.url, origin), origin));
  });
}

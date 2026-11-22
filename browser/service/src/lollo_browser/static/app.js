// Registers this page's own service worker (scope: the browser's pages, separate from
// Lettuce's and the vault's) and manages Web Push on the Notifications page. Classic script
// registration: a module worker's script fetch carries no cookies, and
// Cloudflare Access would redirect it to the login page.
"use strict";

const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content ?? "";
// The browser page's path prefix (config.PREFIX), from the page.
const base = meta("base");

function urlBase64ToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": meta("csrf") },
    body: JSON.stringify(body ?? {}),
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function registration() {
  return navigator.serviceWorker.register(`${base}/sw.js`, { scope: `${base}/` });
}

async function setupPush() {
  const box = document.getElementById("push");
  if (!box) return;
  const status = document.getElementById("push-status");
  const enable = document.getElementById("push-enable");
  const disable = document.getElementById("push-disable");
  const test = document.getElementById("push-test");
  const show = (text, subscribed) => {
    status.textContent = text;
    enable.hidden = subscribed;
    disable.hidden = !subscribed;
    test.hidden = !subscribed;
  };
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    status.textContent = "This browser cannot receive notifications here. On iPhone or iPad, open this page from the Home Screen.";
    return;
  }
  const reg = await registration();
  await navigator.serviceWorker.ready;
  const current = await reg.pushManager.getSubscription();
  if (current) {
    // Re-send it: the server may have dropped it, and this is idempotent.
    await post(`${base}/push/subscribe`, current.toJSON()).catch(() => {});
  }
  show(current ? "Notifications are on for this device." : "Notifications are off for this device.", !!current);

  enable.addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        show("Notifications are blocked for this site in the browser settings.", false);
        return;
      }
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToBytes(meta("vapid")),
      });
      await post(`${base}/push/subscribe`, sub.toJSON());
      show("Notifications are on for this device.", true);
    } catch (e) {
      show(`Could not enable notifications: ${e.message}`, false);
    }
  });
  disable.addEventListener("click", async () => {
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await post(`${base}/push/unsubscribe`, { endpoint: sub.endpoint }).catch(() => {});
      await sub.unsubscribe();
    }
    show("Notifications are off for this device.", false);
  });
  test.addEventListener("click", async () => {
    try {
      const { delivered } = await post(`${base}/push/test`);
      status.textContent = `Test sent to ${delivered} device(s).`;
    } catch (e) {
      status.textContent = `Test failed: ${e.message}`;
    }
  });
}

if ("serviceWorker" in navigator) {
  registration().catch(() => {});
}
// Confirmations before ending tasks or forgetting sign-ins (no inline handlers:
// the CSP forbids them).
function setupConfirms() {
  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  setupConfirms();
  setupPush().catch((e) => {
    const status = document.getElementById("push-status");
    if (status) status.textContent = `Notifications unavailable: ${e.message}`;
  });
});

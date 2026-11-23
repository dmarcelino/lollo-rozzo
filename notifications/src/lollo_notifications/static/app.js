// The Notifications page: registers the lollo host's one service worker (scope /,
// separate from Lettuce's) and turns Web Push on or off for this device. Classic
// script registration: a module worker's script fetch carries no cookies, and
// Cloudflare Access would redirect it to the login page.
"use strict";

const BASE = "/notifications";
const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content ?? "";

function urlBase64ToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

// The id the page lists a device by: the SHA-256 of its endpoint, in hex.
async function deviceId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": meta("csrf") },
    body: JSON.stringify(body ?? {}),
    credentials: "same-origin",
  });
  const answer = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(answer.error || `HTTP ${res.status}`);
  return answer;
}

async function setupPush() {
  const status = document.getElementById("push-status");
  const enable = document.getElementById("push-enable");
  const disable = document.getElementById("push-disable");
  const test = document.getElementById("push-test");
  if (!status) return;
  const show = (text, on) => {
    status.textContent = text;
    enable.hidden = on;
    disable.hidden = !on;
    test.hidden = !on;
  };
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    status.textContent =
      "This browser cannot receive notifications here. On iPhone or iPad, add Lollo to the Home Screen and open it from there.";
    return;
  }
  const reg = await navigator.serviceWorker.register(`${BASE}/sw.js`, { scope: "/" });
  await navigator.serviceWorker.ready;
  const current = await reg.pushManager.getSubscription();
  // On only when this service still has it: a subscription it dropped (the push
  // service said it was gone) reaches nothing, so the device shows as off.
  let listed = null;
  if (current) {
    const id = await deviceId(current.endpoint);
    listed = document.querySelector(`[data-device="${id}"]`);
  }
  if (listed) listed.querySelector(".pill").hidden = false;
  show(listed ? "Notifications are on for this device." : "Notifications are off for this device.", !!listed);

  enable.addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        show("Notifications are blocked for this site in the browser settings.", false);
        return;
      }
      const sub =
        (await reg.pushManager.getSubscription()) ??
        (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToBytes(meta("vapid")) }));
      await post("/subscribe", sub.toJSON());
      location.reload();
    } catch (e) {
      show(`Could not enable notifications: ${e.message}`, false);
    }
  });
  disable.addEventListener("click", async () => {
    try {
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await post("/unsubscribe", { endpoint: sub.endpoint });
        await sub.unsubscribe();
      }
      location.reload();
    } catch (e) {
      status.textContent = `Could not disable notifications: ${e.message}`;
    }
  });
  test.addEventListener("click", async () => {
    try {
      const { reached } = await post("/test");
      status.textContent = `Test sent to ${reached} of your device(s).`;
    } catch (e) {
      status.textContent = `Test failed: ${e.message}`;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupPush().catch((e) => {
    const status = document.getElementById("push-status");
    if (status) status.textContent = `Notifications unavailable: ${e.message}`;
  });
});

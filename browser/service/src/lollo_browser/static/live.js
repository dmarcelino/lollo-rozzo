// The live view: noVNC (vendored, served from this page's own path) connected to
// lollo-browser's relay. The relay authenticates to the display itself, so this
// page never holds the display's password, and it drops input unless the person
// has taken over the tab. viewOnly here mirrors that; the relay is what enforces it.
import RFB from "./novnc/core/rfb.js";

const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content ?? "";
const base = meta("base");
const screen = document.getElementById("screen");
const status = document.getElementById("live-status");

async function ticket() {
  const res = await fetch(`${base}/live/ticket`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": meta("csrf") },
    body: JSON.stringify({ task: screen.dataset.task, tab: screen.dataset.tab }),
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function connect() {
  const { ticket: token, path } = await ticket();
  const url = `wss://${location.host}${path}?ticket=${encodeURIComponent(token)}`;
  const rfb = new RFB(screen, url, { shared: true });
  rfb.viewOnly = screen.dataset.controlled !== "true";
  rfb.scaleViewport = true;
  rfb.focusOnClick = !rfb.viewOnly;
  rfb.addEventListener("connect", () => {
    status.textContent = rfb.viewOnly ? "Watching. Take over to click or type." : "Connected. You have control.";
  });
  rfb.addEventListener("disconnect", () => {
    status.textContent = "Disconnected. Reload the page to watch again.";
  });
}

connect().catch((e) => {
  status.textContent = `Could not open the live view: ${e.message}`;
});

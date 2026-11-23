"use strict";

// Copy buttons and delete confirmations (no inline handlers: the CSP forbids them).
function setupPageHelpers() {
  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copy);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent);
        const label = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = label; }, 1500);
      } catch {
        button.textContent = "Select and copy";
      }
    });
  }
  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }
}

// Groups folded away stay so in this browser: only their ids ("form:identity:Address")
// are stored, never a value. Storage may be unavailable (a private window); then
// every group just opens again on the next page.
const CLOSED_GROUPS = "vault-closed-groups";

function setupGroups() {
  let closed;
  try {
    closed = new Set(JSON.parse(localStorage.getItem(CLOSED_GROUPS) || "[]"));
  } catch {
    closed = new Set();
  }
  const save = () => {
    try {
      localStorage.setItem(CLOSED_GROUPS, JSON.stringify([...closed]));
    } catch {}
  };
  for (const group of document.querySelectorAll("details[data-group]")) {
    if (closed.has(group.dataset.group)) group.open = false;
    group.addEventListener("toggle", () => {
      if (group.open) closed.delete(group.dataset.group);
      else closed.add(group.dataset.group);
      save();
    });
  }
  // A required field in a closed group: open the group so the browser can point at it.
  document.addEventListener("invalid", (event) => {
    const group = event.target.closest?.("details[data-group]");
    if (group) group.open = true;
  }, true);
}

// Like Bitwarden's eye button: shows what was typed into a masked box, and masks it
// again. It changes only how the box draws its text.
function setupMaskToggles() {
  for (const button of document.querySelectorAll(".mask-toggle")) {
    const box = button.previousElementSibling;
    if (!box) continue;
    button.addEventListener("click", () => {
      const shown = button.getAttribute("aria-pressed") !== "true";
      if (box.tagName === "TEXTAREA") box.classList.toggle("masked", !shown);
      else box.type = shown ? "text" : "password";
      button.setAttribute("aria-pressed", String(shown));
      button.textContent = shown ? "Hide" : "Show";
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  setupPageHelpers();
  setupGroups();
  setupMaskToggles();
});

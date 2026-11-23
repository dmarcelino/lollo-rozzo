// The browser's pages' own script. It registers no service worker: notifications are the lollo's
// own page, /notifications/ (sw.js only removes the worker earlier releases installed here).
"use strict";

// Confirmations before ending tasks or forgetting sign-ins (no inline handlers:
// the CSP forbids them).
function setupConfirms() {
  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }
}

document.addEventListener("DOMContentLoaded", setupConfirms);

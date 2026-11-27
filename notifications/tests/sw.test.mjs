// The lollo host's service worker (src/lollo_notifications/static/sw.js), run in
// a VM context: what a tap opens, and where. tests/run-sidecar.sh notifications
// runs it with node --test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const SOURCE = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "src", "lollo_notifications", "static", "sw.js"),
  "utf8",
);
const ORIGIN = "https://alfred-lollo.example.com";

// The worker with a fake `self`: its listeners, the windows open and what it did.
function worker(windows = []) {
  const listeners = {};
  const opened = [];
  const shown = [];
  const self = {
    location: new URL(`${ORIGIN}/notifications/sw.js`),
    registration: {
      scope: `${ORIGIN}/`,
      showNotification: async (title, options) => shown.push({ title, options }),
    },
    clients: {
      matchAll: async () => windows,
      openWindow: async (url) => opened.push(url),
      claim: async () => {},
    },
    addEventListener: (type, fn) => {
      listeners[type] = fn;
    },
    skipWaiting: () => {},
  };
  const context = vm.createContext({ self, URL });
  vm.runInContext(SOURCE, context);
  return { context, listeners, opened, shown };
}

async function tap(w, url) {
  let done;
  w.listeners.notificationclick({
    notification: { data: { url }, close: () => {} },
    waitUntil: (p) => {
      done = p;
    },
  });
  await done;
}

function openWindow(url, { controlled = true } = {}) {
  const win = { url, navigated: null, focused: false };
  win.navigate = async (to) => {
    if (!controlled) throw new TypeError("not controlled");
    win.navigated = to;
    return win;
  };
  win.focus = async () => {
    win.focused = true;
    return win;
  };
  return win;
}

test("a link under a known lollo prefix opens as it is", () => {
  const { context } = worker();
  for (const link of ["/vault/r/abc", "/browser/h/42?x=1#top", "/gmail-triggers/r/1", "/news-radar/#m-3", "/notifications/"]) {
    assert.equal(context.tapTarget(link, ORIGIN), `${ORIGIN}${link}`);
  }
});

test("any other link opens the landing page", () => {
  const { context } = worker();
  for (const link of [
    "/",
    "/code-server/",
    "/gmail-triggers",
    "/news-radarx/",
    "/vaultx/",
    "/vault",
    "/vault/../code-server/",
    "/vault/%2e%2e/code-server/",
    "https://evil.example.com/vault/r/1",
    "//evil.example.com/vault/",
    "javascript:alert(1)",
    "http://alfred-lollo.example.com/vault/",
    "",
    undefined,
    null,
    42,
    "https://[",
  ]) {
    assert.equal(context.tapTarget(link, ORIGIN), `${ORIGIN}/`, String(link));
  }
});

test("a tap on a link outside the prefixes opens the landing page in a new window", async () => {
  const w = worker();
  await tap(w, "https://evil.example.com/");
  assert.deepEqual(w.opened, [`${ORIGIN}/`]);
});

test("a tap navigates an open window on the same lollo page first and focuses it", async () => {
  const lettuce = openWindow("https://alfred.example.com/chat");
  const lollo = openWindow(`${ORIGIN}/browser/t/7`);
  const w = worker([lettuce, lollo]);
  await tap(w, "/browser/h/42");
  assert.equal(lettuce.navigated, null);
  assert.equal(lollo.navigated, `${ORIGIN}/browser/h/42`);
  assert.ok(lollo.focused);
  assert.deepEqual(w.opened, []);
});

test("a tap while working in another lollo page opens a new window", async () => {
  // Taking over a tab in /browser/, then a vault request's notification.
  const takeOver = openWindow(`${ORIGIN}/browser/h/42`);
  const w = worker([takeOver]);
  await tap(w, "/vault/r/abc");
  assert.equal(takeOver.navigated, null);
  assert.equal(takeOver.focused, false);
  assert.deepEqual(w.opened, [`${ORIGIN}/vault/r/abc`]);
});

test("the landing page and the notifications page are reused for any link", async () => {
  for (const at of [`${ORIGIN}/`, `${ORIGIN}/notifications/`]) {
    const win = openWindow(at);
    const w = worker([win]);
    await tap(w, "/vault/r/abc");
    assert.equal(win.navigated, `${ORIGIN}/vault/r/abc`, at);
    assert.deepEqual(w.opened, [], at);
  }
});

test("no other lollo page is taken over", async () => {
  for (const [at, link] of [
    [`${ORIGIN}/vault/`, "/browser/h/42"],
    [`${ORIGIN}/vaultx/`, "/vault/r/abc"],
    [`${ORIGIN}/vault`, "/vault/r/abc"],
    [`${ORIGIN}/code-server/`, "/vault/r/abc"],
    [`${ORIGIN}/gmail-triggers/`, "/notifications/"],
    [`${ORIGIN}/gmail-triggers/r/1`, "/news-radar/"],
    [`${ORIGIN}/news-radar/`, "/vault/r/abc"],
    // A link outside the prefixes opens the landing page, never in a vault window.
    [`${ORIGIN}/vault/r/1`, "https://evil.example.com/vault/"],
    ["https://alfred.example.com/vault/", "/vault/r/abc"],
  ]) {
    const win = openWindow(at);
    const w = worker([win]);
    await tap(w, link);
    assert.equal(win.navigated, null, `${at} ${link}`);
    assert.equal(w.opened.length, 1, `${at} ${link}`);
  }
});

test("a window the worker does not control is skipped", async () => {
  const editor = openWindow(`${ORIGIN}/vault/`, { controlled: false });
  const w = worker([editor]);
  await tap(w, "/vault/r/1");
  assert.equal(editor.navigated, null);
  assert.deepEqual(w.opened, [`${ORIGIN}/vault/r/1`]);
});

test("a push shows its title, body and tag, and no action", async () => {
  const w = worker();
  let done;
  w.listeners.push({
    data: { json: () => ({ title: "Vault request", body: "Visa: number", url: "/vault/r/1", tag: "vault-request" }) },
    waitUntil: (p) => {
      done = p;
    },
  });
  await done;
  assert.equal(w.shown.length, 1);
  const { title, options } = w.shown[0];
  assert.equal(title, "Vault request");
  assert.equal(options.body, "Visa: number");
  assert.equal(options.tag, "vault-request");
  assert.equal(options.data.url, "/vault/r/1");
  assert.equal(options.actions, undefined);
  assert.equal(options.icon, `${ORIGIN}/notifications/icon-192.png`);
});

test("a push without readable data still shows a notification", async () => {
  const w = worker();
  let done;
  w.listeners.push({
    data: {
      json: () => {
        throw new SyntaxError("bad");
      },
    },
    waitUntil: (p) => {
      done = p;
    },
  });
  await done;
  assert.equal(w.shown[0].title, "Lollo");
  assert.equal(w.shown[0].options.data.url, "/");
});

test("the worker has no fetch handler", () => {
  const w = worker();
  assert.deepEqual(Object.keys(w.listeners).sort(), ["activate", "install", "notificationclick", "push"]);
});

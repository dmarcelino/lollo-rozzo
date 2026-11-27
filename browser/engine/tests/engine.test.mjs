// The engine's tests, run inside a running engine container by
// browser/engine/tests/run.sh (`docker exec ... node --test`), with the
// hardening compose/browser.yml gives it: read-only root, /tmp on tmpfs, every
// capability dropped, no-new-privileges, seccomp.json, and no network, so the
// proxy (BROWSER_PROXY) is unreachable.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright-core';

const secret = process.env.BROWSER_ENGINE_SECRET;
const POLICY_FILE = '/etc/opt/chrome_for_testing/policies/managed/lollo.json';
let browser;
let context;

// Text of a page, shadow roots included (chrome:// pages are web components).
const deepText = () => {
  const out = [];
  const walk = (node) => {
    if (node.nodeType === 3) {
      const text = node.textContent.trim();
      if (text) out.push(text);
      return;
    }
    if (node.shadowRoot) walk(node.shadowRoot);
    for (const child of node.childNodes) walk(child);
  };
  walk(document.body);
  return out.join(' | ');
};

before(async () => {
  browser = await chromium.connect(`ws://127.0.0.1:3000/${secret}`, { timeout: 30000 });
  context = await browser.newContext();
});
after(async () => {
  await browser?.close();
});

test('the endpoint refuses a connection without the secret path', async () => {
  for (const path of ['', 'wrong', secret.slice(0, -1)]) {
    await assert.rejects(chromium.connect(`ws://127.0.0.1:3000/${path}`, { timeout: 5000 }));
  }
});

test("Chromium's sandbox is on", async () => {
  const page = await context.newPage();
  await page.goto('chrome://sandbox');
  const text = await page.evaluate(deepText);
  for (const line of ['PID namespaces | Yes', 'Network namespaces | Yes', 'Seccomp-BPF sandbox | Yes', 'You are adequately sandboxed.']) {
    assert.ok(text.includes(line), `chrome://sandbox lacks "${line}": ${text}`);
  }
  await page.close();
});

test('renderers run with no capabilities, no new privileges and seccomp-BPF, in their own user namespace', async () => {
  const page = await context.newPage();
  await page.setContent('<p>renderer</p>');
  const mine = fs.readlinkSync('/proc/self/ns/user');
  const renderers = [];
  for (const pid of fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
    let argv;
    try {
      // Chromium rewrites its children's command lines as one string.
      argv = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split(/[\0 ]/);
    } catch {
      continue;
    }
    if (!argv[0].endsWith('/chrome') || !argv.includes('--type=renderer')) continue;
    const status = fs.readFileSync(`/proc/${pid}/status`, 'utf8');
    const field = (key) => (status.match(new RegExp(`^${key}:\\s*(.*)$`, 'm')) || [])[1];
    let userns = null;
    try {
      userns = fs.readlinkSync(`/proc/${pid}/ns/user`);
    } catch {
      // another user namespace's process may not be readable: also not ours
    }
    renderers.push({ pid, seccomp: field('Seccomp'), noNewPrivs: field('NoNewPrivs'), capEff: field('CapEff'), userns });
  }
  assert.ok(renderers.length > 0, 'no renderer process found');
  for (const r of renderers) {
    assert.equal(r.seccomp, '2', `renderer ${r.pid} without seccomp-BPF`);
    assert.equal(r.noNewPrivs, '1', `renderer ${r.pid} without no-new-privileges`);
    assert.match(r.capEff, /^0+$/, `renderer ${r.pid} has capabilities`);
    assert.notEqual(r.userns, mine, `renderer ${r.pid} shares the container's user namespace`);
  }
  await page.close();
});

test('every managed policy shows as applied in chrome://policy', async () => {
  const policies = JSON.parse(fs.readFileSync(POLICY_FILE, 'utf8'));
  const expected = [
    'WebRtcIPHandling', 'DnsOverHttpsMode', 'PasswordManagerEnabled', 'AutofillAddressEnabled',
    'AutofillCreditCardEnabled', 'ExtensionInstallBlocklist', 'DownloadRestrictions', 'URLBlocklist',
  ];
  assert.deepEqual(Object.keys(policies).sort(), [...expected].sort());
  const page = await context.newPage();
  await page.goto('chrome://policy');
  let text = '';
  for (let i = 0; i < 50 && !text.includes('| URLBlocklist |'); i++) {
    await page.waitForTimeout(200);
    text = await page.evaluate(deepText);
  }
  const table = text.slice(text.indexOf('Policy name | Policy value'));
  for (const name of expected) {
    const start = table.indexOf(`| ${name} |`);
    assert.ok(start >= 0, `${name} is not listed`);
    const row = table.slice(start, table.indexOf('| Error | Warning | Info', start));
    assert.ok(row.includes('| Machine |') && row.includes('| Mandatory |'), `${name} is not a mandatory machine policy: ${row}`);
    // Chromium 159 marks AutofillAddressEnabled and AutofillCreditCardEnabled
    // "Deprecated" (AutofillSettings succeeds them); a deprecated policy still
    // applies, an unknown or ignored one shows "Error".
    assert.match(row, /\| Status \| (OK|Deprecated)\s*(\||$)/, `${name} is not applied: ${row}`);
    assert.doesNotMatch(row, /Unknown policy|Ignored/, `${name} is not applied: ${row}`);
  }
  // The flags chrome://policy lists: never one that turns the sandbox off.
  assert.ok(text.includes('remote-debugging-pipe'), 'the command line flags are not listed');
  assert.doesNotMatch(text, /no-sandbox|disable-[a-z-]*sandbox/);
  await page.close();
});

test('file://, view-source: and devtools:// are blocked', async () => {
  const page = await context.newPage();
  for (const url of ['file:///etc/hostname', 'view-source:http://example.com/', 'devtools://devtools/bundled/inspector.html']) {
    await assert.rejects(page.goto(url), /ERR_BLOCKED_BY_ADMINISTRATOR/, url);
  }
  await page.close();
});

test("the stack's denied hosts are blocked by policy, their siblings are not", async () => {
  // run.sh gives the engine LOLLO_DENY_HOSTS=alfred-lollo.example.com,...
  const policies = JSON.parse(fs.readFileSync(POLICY_FILE, 'utf8'));
  assert.ok(policies.URLBlocklist.includes('.alfred-lollo.example.com'), JSON.stringify(policies.URLBlocklist));
  assert.ok(policies.URLBlocklist.includes('file://*'), 'the image policies are kept');
  const page = await context.newPage();
  for (const url of ['https://alfred-lollo.example.com/vault/', 'https://ALFRED-LOLLO.example.com./', 'https://team.cloudflareaccess.com/cdn-cgi/access/login']) {
    await assert.rejects(page.goto(url), /ERR_BLOCKED_BY_ADMINISTRATOR/, url);
  }
  // A sibling on the same domain goes to the (unreachable) proxy instead.
  await assert.rejects(page.goto('https://www.example.com/'), /ERR_PROXY_CONNECTION_FAILED/);
  await page.close();
});

test('every request goes to the proxy, loopback included', async () => {
  const page = await context.newPage();
  // Direct, this would answer "Running": the engine's own Playwright port.
  for (const url of ['http://127.0.0.1:3000/', 'http://localhost:5900/', 'http://example.com/']) {
    await assert.rejects(page.goto(url, { timeout: 15000 }), /ERR_PROXY_CONNECTION_FAILED|ERR_TUNNEL_CONNECTION_FAILED/, url);
  }
  await page.close();
});

test('the only TCP listeners are 3000 (Playwright) and 5900 (VNC)', () => {
  const ports = new Set();
  for (const file of ['/proc/net/tcp', '/proc/net/tcp6']) {
    for (const line of fs.readFileSync(file, 'utf8').split('\n').slice(1)) {
      const cols = line.trim().split(/\s+/);
      if (cols[3] === '0A') ports.add(parseInt(cols[1].split(':').pop(), 16));
    }
  }
  assert.deepEqual([...ports].sort((a, b) => a - b), [3000, 5900]);
});

// One RFB handshake up to the security result; `respond` answers the challenge.
async function vncHandshake(respond) {
  const socket = net.connect(5900, '127.0.0.1');
  let buffer = Buffer.alloc(0);
  let waiting = null;
  const check = () => {
    if (waiting && buffer.length >= waiting.n) {
      const { n, resolve } = waiting;
      waiting = null;
      const out = buffer.subarray(0, n);
      buffer = buffer.subarray(n);
      resolve(out);
    }
  };
  socket.on('data', (data) => {
    buffer = Buffer.concat([buffer, data]);
    check();
  });
  const read = (n) => new Promise((resolve) => {
    waiting = { n, resolve };
    check();
  });
  try {
    const version = (await read(12)).toString();
    assert.match(version, /^RFB 003\.00[38]\n$/);
    socket.write('RFB 003.008\n');
    const count = (await read(1))[0];
    const types = [...(await read(count))];
    socket.write(Buffer.from([2]));
    const challenge = await read(16);
    socket.write(respond(challenge));
    return { types, result: (await read(4)).readUInt32BE(0) };
  } finally {
    socket.destroy();
  }
}

// VNC authentication: DES-ECB of the challenge, keyed with the password's
// first 8 bytes, each byte's bits reversed. DES needs OpenSSL's legacy
// provider, so a child node computes it; the key goes on its stdin.
function vncResponse(password, challenge) {
  const code = `
    const input = require('fs').readFileSync(0);
    const key = Buffer.alloc(8);
    input.subarray(0, 8).forEach((b, i) => {
      let r = 0;
      for (let bit = 0; bit < 8; bit++) if (b & (1 << bit)) r |= 1 << (7 - bit);
      key[i] = r;
    });
    const des = require('crypto').createCipheriv('des-ecb', key, null);
    des.setAutoPadding(false);
    process.stdout.write(Buffer.concat([des.update(input.subarray(8)), des.final()]).toString('hex'));`;
  const key = Buffer.alloc(8);
  Buffer.from(password).copy(key, 0, 0, 8);
  const out = execFileSync(process.execPath, ['--openssl-legacy-provider', '-e', code], {
    input: Buffer.concat([key, challenge]),
  });
  return Buffer.from(out.toString(), 'hex');
}

test('VNC offers only VNC authentication and refuses a wrong response', async () => {
  const { types, result } = await vncHandshake(() => Buffer.alloc(16));
  assert.deepEqual(types, [2], 'security types other than VNC authentication');
  assert.notEqual(result, 0, 'a wrong response was accepted');
});

test("VNC accepts the secret's first 8 characters", async () => {
  // TigerVNC delays a client after a failed attempt; wait it out.
  let outcome;
  for (let i = 0; i < 20; i++) {
    outcome = await vncHandshake((challenge) => vncResponse(secret.slice(0, 8), challenge)).catch((e) => e);
    if (!(outcome instanceof Error) && outcome.result === 0) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  assert.ok(!(outcome instanceof Error), String(outcome));
  assert.equal(outcome.result, 0, 'the right password was refused');
});

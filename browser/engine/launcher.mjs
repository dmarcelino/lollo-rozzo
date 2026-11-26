// lollo-browser-engine's launcher: starts Chromium once, with its sandbox on and
// fixed launch options, and serves Playwright's WebSocket endpoint at a path
// made from the stack's engine secret (browser/README.md, Rules). A client
// cannot change these options; the only way in is the secret path.
//
// Exit codes: 2 refused to start (bad environment, an argument, or anything
// that would turn the sandbox off), 3 Chromium's sandbox failed, 1 Chromium
// did not start for another reason or exited, 0 stopped by SIGTERM/SIGINT.
// Never logs the secret or the endpoint.
import { chromium } from 'playwright-core';
import { writePolicies } from './policy.mjs';

const NAME = 'lollo-browser-engine';
const SECRET_VAR = 'BROWSER_ENGINE_SECRET';
// --no-sandbox, --disable-setuid-sandbox, --disable-namespace-sandbox,
// --disable-seccomp-filter-sandbox, NO_SANDBOX=1 and the like.
const SANDBOX_OFF = /no[-_]?sandbox|disable[-_][a-z_-]*sandbox/i;
// What Chromium logs when it cannot set up its sandbox (namespaces, chroot or
// seccomp-BPF refused by the host or the container's seccomp profile).
const SANDBOX_FAILED = /no usable sandbox|zygote|sandbox_host_linux|namespace_sandbox|credentials\.cc|setuid_sandbox|Failed to move to new namespace/i;

function die(code, message) {
  console.error(`${NAME}: ${message}`);
  process.exit(code);
}

for (const arg of process.argv.slice(2)) {
  if (SANDBOX_OFF.test(arg)) die(2, "refusing to start: an argument would turn off Chromium's sandbox");
}
for (const [key, value] of Object.entries(process.env)) {
  if (SANDBOX_OFF.test(key) || (key !== SECRET_VAR && SANDBOX_OFF.test(value ?? ''))) {
    die(2, `refusing to start: the environment variable ${key} would turn off Chromium's sandbox`);
  }
}
if (process.argv.length > 2) die(2, 'refusing to start: the launcher takes no arguments, its launch options are fixed');

const secret = process.env[SECRET_VAR] ?? '';
if (!/^[A-Za-z0-9_-]{32,}$/.test(secret)) {
  die(2, `${SECRET_VAR} must be at least 32 characters of A-Z, a-z, 0-9, _ and -`);
}
const proxy = process.env.BROWSER_PROXY ?? '';
if (!/^http:\/\/[A-Za-z0-9.-]+:[0-9]{1,5}$/.test(proxy)) {
  die(2, 'BROWSER_PROXY must be http://<host>:<port> (pipelock)');
}
// The managed policies with the stack's denied hosts: Chromium never starts
// without them (its password manager and autofill would be on too).
try {
  writePolicies(process.env);
} catch (error) {
  die(2, `refusing to start: ${error?.message ?? error}`);
}

let server;
try {
  server = await chromium.launchServer({
    headless: false,
    chromiumSandbox: true,
    // Every request goes to pipelock, loopback included: without
    // <-loopback> Chromium sends loopback and link-local addresses direct.
    proxy: { server: proxy, bypass: '<-loopback>' },
    args: ['--disable-quic', '--window-position=0,0', '--window-size=1280,900'],
    host: '0.0.0.0',
    port: 3000,
    wsPath: `/${secret}`,
  });
} catch (error) {
  const text = String(error?.message ?? error).replaceAll(secret, '<secret>');
  const details = text.split('\n').filter((line) => line.trim()).slice(0, 40);
  if (SANDBOX_FAILED.test(text)) {
    console.error(`${NAME}: Chromium's sandbox failed`);
    for (const line of details) console.error(`${NAME}:   ${line}`);
    process.exit(3);
  }
  console.error(`${NAME}: Chromium did not start`);
  for (const line of details) console.error(`${NAME}:   ${line}`);
  process.exit(1);
}

let stopping = false;
server.process().on('exit', (code, signal) => {
  if (!stopping) die(1, `Chromium exited (${signal ?? `code ${code}`})`);
});
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, async () => {
    stopping = true;
    await server.close().catch(() => {});
    process.exit(0);
  });
}
console.log(`${NAME}: Chromium started with its sandbox on; Playwright on port 3000, VNC on port 5900`);

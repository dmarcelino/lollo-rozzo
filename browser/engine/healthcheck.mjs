// The engine's health check: passes once Playwright's WebSocket endpoint
// accepts a connection at the secret path, then closes it without a request.
const secret = process.env.BROWSER_ENGINE_SECRET ?? '';
const timer = setTimeout(() => process.exit(1), 4000);
const ws = new WebSocket(`ws://127.0.0.1:3000/${secret}`);
ws.addEventListener('open', () => {
  clearTimeout(timer);
  ws.close();
  process.exit(0);
});
ws.addEventListener('error', () => process.exit(1));

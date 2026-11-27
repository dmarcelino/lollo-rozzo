# notifications

The `lollo-notifications` service sends every lollo notification to the person's devices. It holds the stack's one VAPID pair and the devices' push subscriptions, takes notifications from the senders (the vault, the browser, Gmail triggers and News Radar) at `/notify/v1/send` on the stack network, and serves the person's page at `/notifications/` on the stack's lollo host (`<name>-lollo.<domain>`), where a device turns notifications on once for every sender. It also serves the lollo host's one service worker. It is Python (Starlette, pywebpush) and has no profile of its own: it runs whenever `lollo-vault`, `lollo-browser`, `lollo-gmail-triggers` or `lollo-news-radar` is on, as `landing` does for every lollo page. For users and admins: [docs/notifications.md](../docs/notifications.md).

![A sender hands a notification to the service, which pushes it to every enrolled device; a tap opens a lollo page](../docs/diagrams/notifications-flow.svg)

An interactive version, [notifications-flow.html](../docs/diagrams/notifications-flow.html), opens in a browser from a clone of this repo.

## Files
| Path | What it does |
|---|---|
| `Dockerfile` | `base` installs `lollo-common` from its build context, then this package; `test` adds pytest and `tests/`; the final stage runs as uid 10005, which owns `/data` |
| `pyproject.toml` | The package and its pinned dependencies (the vault's pins for what both use, plus `pywebpush`, which the vault no longer has) |
| `src/lollo_notifications/__main__.py` | Entry point: reads the settings, builds the store, the pusher and the app, runs uvicorn on port 8000 without an access log |
| `src/lollo_notifications/config.py` | `PREFIX` (`/notifications`), the limits (`SEND_PER_MINUTE`, `SEND_PER_HOUR`, `TEST_PER_MINUTE`, `MAX_SUBSCRIPTIONS`, `MAX_QUEUED` per sender, `PUSH_CONCURRENCY`, `MAX_PUSH_FAILURES`) and `Settings` from the environment, with `read_tokens` (a source per `NOTIFY_TOKEN_<SOURCE>`) |
| `src/lollo_notifications/store.py` | `Subscriptions`: `subscriptions.json` on `/data` (version 2: the VAPID public key they were made with, then each email, endpoint, `p256dh`, `auth`, `added_at`), at most 50, the oldest dropped first, rewritten atomically; at start, every subscription made with another key is deleted |
| `src/lollo_notifications/delivery.py` | `Pusher` (Web Push to every subscription of a person in `ALLOWED_USERS`, all at once; a subscription gone, or timing out too often in a row, is deleted), `Queue` (a queue and a worker per sender, delivering in the background), `RateLimiter` (`check`, then `record` once queued) |
| `src/lollo_notifications/app.py` | The routes: `/notify/v1/send` behind `guard_sources`, `/healthz`, and the page, its calls and its assets under `/notifications/`; `with_sender` puts the sender's label in front of a title |
| `src/lollo_notifications/icons.py` | The page's icon (a bell), also the notifications' icon, drawn as PNG at first use |
| `src/lollo_notifications/logs.py` | Logging to stdout; a notification's title and body are never logged |
| `src/lollo_notifications/templates/` | `base.html` (the Lollo manifest link, the top bar, the link back to `/`), `notifications.html`, `error.html` |
| `src/lollo_notifications/static/` | `app.js` (registers the service worker, enables, disables and tests this device, marks it in the list), `sw.js` (the service worker), `app.css` |
| `tests/` | pytest: settings, the store, the send API and delivery, the page; `sw.test.mjs` (`node --test`) runs `sw.js` in a VM context with a fake `self` |

## The send API
- `POST /notify/v1/send` takes a JSON object with exactly `title`, `body`, `url` and `tag`, at most `lollo_common.notify.MAX_SEND_BODY` bytes, with `Authorization: Bearer <the sender's token>`. The guard (`guard_sources` in `lollo-common`) refuses a request with an `Origin` or any of Cloudflare's tunnel headers (403) and one without a listed token (401), and tells the handler which sender called.
- `check_payload` (`lollo_common.notify`), the same check the senders' client runs, holds the sender to its own: the `url` a path under `/<source>/`, the `tag` starting with `<source>-`, every field one line, the title and body shortened. Anything else is 400.
- The service then puts the sender's label in front of the title (`with_sender`): `Vault: `, `Browser: `, `Gmail triggers: `, `News Radar: `, `Lollo: ` for its own test, and for any other source its name capitalised. The whole title stays within `MAX_TITLE`: what is cut is the sender's text, never the label.
- Each sender may hand over at most 30 notifications a minute and 300 an hour (`SEND_PER_MINUTE`, `SEND_PER_HOUR`); past that it gets 429 and the refusal is logged. Each sender counts alone, and only what was queued counts: a 400 or a 503 does not.
- The answer is 202 `{"queued": true}` once the notification is queued. Each sender has its own queue, and a worker of its own started in the app's lifespan delivers it, one notification after another. More than `MAX_QUEUED` waiting for one sender gives that sender 503, and no other. A queued notification is lost if the service stops before sending it (shutdown waits up to 5 seconds); there is no persistent queue.
- Delivery: pywebpush's `webpush` with VAPID claims `mailto:<PUSH_VAPID_CONTACT_EMAIL>`, a TTL of 600 s and a 10 s timeout, to every subscription whose email is in `ALLOWED_USERS`, all at once (at most `PUSH_CONCURRENCY`, 10, at a time, in worker threads). A 404 or 410 deletes that subscription. A timeout or a network error counts against it, and the `MAX_PUSH_FAILURES`th (3) in a row deletes it; a push its service takes, or any answer from it, resets the count, which is kept in memory only. Any other failure is logged and decides nothing.

## The page
- `/notifications/` shows whether this device has notifications on, **Enable on this device**, **Disable on this device**, **Send a test**, and the person's own devices (when each was added and its push service's host), newest first. The page lists a device by the SHA-256 of its endpoint; `app.js` hashes this device's endpoint the same way to mark it. A device the service no longer holds shows as off.
- `POST /notifications/subscribe`, `/unsubscribe` and `/test` are the page script's calls. `/unsubscribe` removes only the person's own device. `/test` sends a `notifications-test` notification to every device of that person only, at most 5 a minute, and answers how many push services took it.
- `/notifications/sw.js` is served with `Service-Worker-Allowed: /`, and `app.js` registers it at scope `/` (a classic script: a module worker's fetch carries no cookie, and Access would answer with its login page). It shows each push and, on a tap, opens the payload's `url` only when it is under `/vault/`, `/browser/`, `/gmail-triggers/`, `/news-radar/` or `/notifications/`, else `/`. It navigates an open window only when that window is already under the same prefix, or at `/` or under `/notifications/`; otherwise it opens a new one, so a tap never takes over a browser live view or a half-filled vault form. It has no fetch handler and a notification has no actions.
- The page links the lollo host's one manifest, `/assets/manifest.webmanifest` from `landing`, with `crossorigin="use-credentials"`, and `/assets/icon-192.png` as its Home Screen icon, so adding it to the Home Screen gives the same "Lollo" app as any lollo page.

## How it plugs in
- **Overlay:** [compose/notifications.yml](../compose/notifications.yml) defines `lollo-notifications` (image `lollo-notifications:local`, built from this directory with the `lollo-common` build context) under the profiles `lollo-vault` and `lollo-browser`: read-only root, no capabilities, `no-new-privileges`, no ports, on the stack's `default` network only. Its one mount is the named volume `notifications-data` at `/data`.
- **Environment:** `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` (set from `CF_LOLLO_AUD`), `CF_ACCESS_ISSUER`, `ALLOWED_USERS`, `PUBLIC_ORIGIN` (set from `LOLLO_ORIGIN`), `LOLLO_VAPID_PUBLIC_KEY`, `LOLLO_VAPID_PRIVATE_KEY`, `PUSH_VAPID_CONTACT_EMAIL`, `NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`, `NOTIFY_TOKEN_GMAIL_TRIGGERS`, `NOTIFY_TOKEN_NEWS_RADAR`, `TZ`. Each `NOTIFY_TOKEN_<SOURCE>` names a sender (`_` becomes `-`, so `NOTIFY_TOKEN_GMAIL_TRIGGERS` is `gmail-triggers`); an empty one means that sender cannot send, and a malformed or shared one stops the service. Never `COMPOSE_PROFILES` or `LOLLO_PROFILES`: a profile change would recreate it.
- **Senders:** the vault, the browser, Gmail triggers and News Radar each get their own token as `NOTIFY_TOKEN` and post with `lollo_common.notify.Notifier` to `http://lollo-notifications:8000/notify/v1/send`. Gmail triggers notifies of a sender new to a rule anyone may set off (links under `/gmail-triggers/`, tags `gmail-triggers-new-sender-<rule>`); News Radar of a monitor or a source's move waiting for the person's confirmation (links under `/news-radar/`, tags `news-radar-confirm-<monitor>` and `news-radar-move-<source>`).
- **Tunnel:** `lib/cloudflare.sh` sends `^/notifications(/|$)` on the lollo host to `http://lollo-notifications:8000` when `lollo-vault` or `lollo-browser` is on. `/notify/v1/` and `/healthz` match no rule.
- **Requests checked in the app:** every route under `/notifications/` goes through `lollo_common.auth.admit` (Access JWT of the lollo's application; Fetch Metadata: `PAGE` for the page, `FETCH` for its script's calls, none for the assets), and every call also needs the lollo's `Origin` and the page's form token in `X-CSRF-Token`. Every response sets COOP and a CSP that runs only the page's own script. See [lollo-common](../lollo-common/README.md).
- **lolloctl:** `up` makes the VAPID pair (`LOLLO_VAPID_*`) and every sender's token once per stack, whatever the profiles; `backup` copies `notifications-data` read-only and `restore` puts it back (owner 10005).
- **`check_stack`:** with a sender on, `check_notifications` refuses the service when it is not a hardened sidecar with `no-new-privileges` and memory and process limits, on `default` only, mounting only `notifications-data`, or given the profile lists or `NOTIFY_TOKEN_NOTIFICATIONS`. Whatever the profiles, `check_stack` refuses an empty `LOLLO_VAPID_*`, and `notifications-data` mounted by anything else or reached by a `volumes_from`; and `check_notify_secrets` refuses a token that is not 64 lowercase hex characters, equal to another or to `LOLLO_EXECUTOR_TOKEN`, a token's value in any service but this one and its sender, and the VAPID private key in any service but this one. `check_lollo_audience` holds it to the lollo's Access application and origin. Every `check_stack` rule: [CONTRIBUTING.md](../CONTRIBUTING.md#what-check_stack-enforces).

## Build and test
`lolloctl up` builds the image. The tests need only Docker:
```bash
tests/run-sidecar.sh notifications              # pytest in the image's test stage, plus sw.js's tests
                                                # (node --test in tests/run-sidecar.sh's NODE_IMAGE) when no args are given
tests/run-sidecar.sh notifications -k page      # extra args go to pytest
node --test 'notifications/tests/*.test.mjs'    # sw.js's tests on the host
```
Run them before changing this directory or `lollo-common/`.

The tests never use the network: pushes go to `FakeSender` (which can also time out or hold a push on an endpoint), Access tokens come from `make_jwt` with a test RSA key, and a fake `Clock` drives the rate limits and the devices' dates. `Hub` in `tests/conftest.py` runs the app's lifespan; `stop()` leaves it, which waits for queued notifications, before a test looks at what was sent. Distinctive titles and bodies are asserted never to reach the logs. `sw.test.mjs` runs `sw.js` in a VM context with a fake `self`: what a tap opens for each kind of link, which open windows a tap may navigate (the same page's, the landing page, `/notifications/`) and which it never takes over, a window the worker does not control skipped, no fetch handler and no actions. A test in a real browser of a tap on a delivered notification is not automated.

## Rules

### Senders only, each with its own token
`/notify/v1/send` sits outside every tunnelled prefix and needs a sender's token, which only that sender and this service hold, never the app-server, so agents cannot read it as they read the executor's. A request with an `Origin` or a Cloudflare tunnel header is refused even with a valid token. The service reads the sender from the token, never from the payload. Tests: `test_send.py` ("The send API is for senders only"); `check_notifications` holds the tokens' holders.

### A sender says only what is its own
The service runs `check_payload` on what it receives, whatever the sender's client did: a link under the sender's own prefix and a tag starting with its name, so a compromised browser cannot pose as the vault; exactly a title, a body, a link and a tag, so a notification carries no action; every field one line. The service then puts the sender's label in front of every title, so a sender's text (or an agent's, in a hand-off summary) can't read as another sender's notification. Each sender is rate limited on its own, so one in a loop cannot ring every device without end, nor stop another's notifications. Tests: `test_send.py` ("A sender says only what is its own", "Each notification names its sender").

### One sender never delays another
Each sender has its own queue and worker, and a notification goes to every device at once, so neither a busy sender nor a slow device holds up or refuses another sender's notifications: the vault's erase notice must be accepted at once (`Notifier.accept`), or the erase is refused. A device whose push service keeps timing out is deleted after `MAX_PUSH_FAILURES` in a row, so it does not slow every notification for ever. Tests: `test_send.py` ("One sender never delays another"). The page's own test notification uses the source `notifications`, which no sender may take (`read_tokens` refuses `NOTIFY_TOKEN_NOTIFICATIONS`).

### No title or body in the logs
A vault notification names a secret and its fields. Logs carry the source, the tag, the outcome and the push service's host only: never a title, a body, an endpoint's path or a key. `test_logs_show_senders_tags_and_outcomes_never_a_title_or_body` checks it.

### The VAPID key is never rotated
Every subscription is bound to the public key it was made with: a new pair breaks every device's subscription, so the vault's erase notices would stop arriving. `LOLLO_VAPID_*` is made once by `lolloctl up` and only this service holds the private key. The store keeps the public key next to the subscriptions, and at start deletes every subscription made with another (an older `stack.env` restored next to newer subscriptions), so the page shows those devices off rather than on while every push fails; a file without a key, from before, is taken as made with the current one. Tests: `test_store.py` ("Key and subscriptions apart").

### `notifications-data` is this service's alone
It holds every device's subscription. Only this service mounts it, and `backup`, read-only; `check_notifications` refuses any other mount or a `volumes_from` that reaches it.

### A tap only opens a lollo page
The service worker opens a notification's link only under `/vault/`, `/browser/`, `/gmail-triggers/`, `/news-radar/` or `/notifications/`, else `/`, and reuses an open window only when it is already under that prefix, at `/` or under `/notifications/`, so a tap never navigates away from work on another lollo page. It has no notification actions: approving, sharing or declining happens only on the page it opens. It has no fetch handler, so nothing is cached and every page load still goes through Access.

### What the page can't stop
Any script on the lollo origin, a code editor webview say, can call `getRegistration('/')` and unsubscribe a device in the browser. It can't post to this service without the page's form token, and it can't send a notification: a push service takes only pushes signed with this service's key. The device list shows which devices still get notifications; [docs/notifications.md](../docs/notifications.md#security-model) says so to people.

# Design

## Context

See proposal.md for why. Today, on the lollo host:

| | vault | browser |
|---|---|---|
| Sender | `Pusher` in `vault/src/lollo_vault/push.py` | `Pusher` in `browser/service/src/lollo_browser/push.py`, payload scrubbed first |
| Subscriptions | `push_subscriptions` table in `vault-data` (backed up) | `push.json` on `browser-data` (never backed up) |
| Keys | `VAULT_VAPID_*`, made by `push_env_lines` at `add` | `BROWSER_VAPID_*`, made by `ensure_browser_secrets` |
| Page | `/vault/notifications`, `/vault/push/{subscribe,unsubscribe,test}` | the same under `/browser/` |
| Service worker, manifest | `/vault/sw.js` at scope `/vault/`, manifest scope `./` | the same under `/browser/` |

Both senders call `pusher.send({title, body, url, tag})` from a background task and ignore the outcome (`vault/src/lollo_vault/service.py`, `browser/service/src/lollo_browser/tasks.py`), so the sender side swaps cleanly.

Constraints that shaped the design:
- **The landing page stays passive** ([landing/README.md](../../../../landing/README.md#the-landing-page-stays-passive)): no script, no other sidecar, no mounts (`check_landing`).
- **Same-origin script is untrusted** ([lollo-common/README.md](../../../../lollo-common/README.md#rules)): the code editor's webviews share the lollo origin. A sidecar's CSRF token is random per process and readable only from its own rendered page, so one page's script cannot post to another sidecar.
- **Agents share the stack network** and can read the executor's token, so an internal API is only as private as its token's holders.
- **The vault's erase without the passphrase** is safe only because every device with notifications on is told ([vault/README.md](../../../../vault/README.md#rules)).
- **VAPID keys are never rotated**: a new key breaks every subscription made with the old one.
- **The code editor's gate** narrows `Service-Worker-Allowed` to `/code-server/` ([code-server/README.md](../../../../code-server/README.md#rules)), so nothing under it can take a wider scope.

## Goals / Non-Goals

**Goals:**
- One subscription per device for every lollo notification, one Home Screen app on iPhone.
- A sender can only say what is its own: a compromised sender cannot pose as another.
- The vault's erase warning is no weaker than today.

**Non-Goals:**
- Lettuce's notifications (upstream, another origin, its own keys).
- Muting one source while keeping another. If it comes later, the vault's erase notices are never mutable.
- Gmail triggers and News Radar as senders; the design leaves room for them (a token each), a later change adds them.
- A banner or a last push on the old subscriptions telling people that notifications moved: the release notes do.

## Decisions

### A sidecar of its own, not the landing service
`lollo-notifications`, image `lollo-notifications`, on the `default` network, mounting only `notifications-data`, hardened like the other sidecars (read-only root, no capabilities, `no-new-privileges`). Its Compose profiles are `lollo-vault` and `lollo-browser`, so it runs whenever a sender does, with no profile of its own, as `landing` runs for every lollo page. The tunnel sends `^/notifications(/|$)` to it when either sender's profile is on, through constants of its own (`LOLLO_NOTIFICATIONS_PATH`, `LOLLO_NOTIFICATIONS_SERVICE`) next to the landing page's, not a `PROFILE_ROUTES` row: a row has one profile, and its profile also feeds `LOLLO_PROFILES` and the landing page's links. `lollo_services` lists it, so `check_lollo_audience` holds it to the lollo's Access application and origin.

Alternative: inside `landing`. One container fewer, and the switch would be on the landing page itself. Refused because:
- `landing` is the one service that receives `LOLLO_PROFILES`, so every `lolloctl profiles` recreates it; the sender would drop out each time, and the vault would refuse erases meanwhile.
- `landing` takes the agent list over the executor API, whose token agents can read; the VAPID private key and the subscriptions would share a process with agent-supplied input.
- It would break three of the landing page's rules (no script, no mounts, touches no other sidecar) and an AGENTS.md directive.

### A token per sender, held by the sender and the hub only
`ensure_notify_secrets` (in `up`, like `ensure_executor_token`, whatever the profiles, so a sender turned on later finds its token) writes `NOTIFY_TOKEN_VAULT` and `NOTIFY_TOKEN_BROWSER` to `stack.env`: `openssl rand -hex 32`. Compose gives each sender its own as `NOTIFY_TOKEN`, and the hub all of them. Tokens never enter the app-server, so agents cannot read them, unlike `LOLLO_EXECUTOR_TOKEN`. A token may be rotated with a `lolloctl restart`; nothing outlives it.

`check_notifications` refuses: a token that is not 64 lowercase hex characters, two equal tokens or one equal to `LOLLO_EXECUTOR_TOKEN`, and any service other than the hub and its own sender whose rendered environment holds a token's value. Plus the usual: a hardened sidecar on `default` only, `notifications-data` mounted by it alone (and `backup`, read-only), and `check_lollo_audience` (it is a lollo service).

Alternative: reuse `LOLLO_EXECUTOR_TOKEN`. Refused: agents can read it, and anyone holding it could make a phone show "Vault: approve this request".

### The send API and what a sender may say
`POST /notify/v1/send`, outside every tunnelled prefix, behind `guard` from `lollo-common`, generalised from the executor's: a prefix, a map from token to source; refused with an `Origin`, any of Cloudflare's tunnel headers, or an unknown token. Body: JSON with exactly `title`, `body`, `url`, `tag`, read with a size cap. The hub:
- refuses a `url` that is not a path under `/<source>/` and a `tag` that does not start with `<source>-`;
- caps lengths and passes every field through `one_line`;
- rate-limits per source (a stuck browser task must not ring every phone all night); the vault's erase notices are already capped by `ERASE_REQUESTS_PER_HOUR`;
- answers 202 once the notice is queued, then delivers in the background, removing a subscription on 404 or 410 and skipping one whose email is no longer in `ALLOWED_USERS`, as both senders do today;
- logs the source, the tag and the outcome, never a title or a body (vault notices name a secret's labels).

The browser keeps scrubbing values out of its payloads before sending: the hub cannot know what a value is.

The client in `lollo-common` keeps the `send(payload)` the senders call, plus `accept(payload) -> bool`, which waits for the 202 (about 5 s) for callers that need it (below). The payload checks live in `lollo-common` too, so a sender fails in its own tests before the hub would refuse it.

### The vault's erase waits for the hub
Today the "erase scheduled" notice is sent from the vault's own process, and a delivery that fails does not stop the erase. With a hub, the notice can fail earlier, at the hand-over, so:
- `schedule_erase` calls `accept()` with the "scheduled" notice before it stores the schedule. Without the 202, the unlock page says notifications are unavailable, try again later, and nothing is scheduled.
- After a restart, an overdue erase's last notice ("erase in 1 hour") is retried until the hub accepts it, and the grace hour counts from that moment, not from the start.
- The "cancelled" and "done" notices stay best effort, retried; missing them cannot cause a loss.

Acceptance, not delivery: a person with no device enrolled (often the one who forgot the passphrase) must still be able to erase, as today. Requiring a delivered notice would leave them needing the operator to delete the volume.

A notice the hub accepted is lost if the hub dies before sending it, as a notice is lost today if the vault dies; no persistent queue.

### One service worker at `/`, one manifest from `landing`
- The hub serves `/notifications/sw.js` with `Service-Worker-Allowed: /`; its page registers it at scope `/`. It has no fetch handler: nothing is cached, every load still goes through Access. It shows notifications and, on a tap, opens the payload's `url` only when it is under a known lollo prefix (`/vault/`, `/browser/`, `/notifications/`), else `/`, navigating an open Lollo window first. A notification has no action buttons and a tap never answers anything: approving a held checkout is possible only on `/browser/`, never from a notification (`browser/checkout`), and a vault request is answered only on its page; the payload has no field that could carry an action.
- `landing` serves `/assets/manifest.webmanifest` (`id`, `scope` and `start_url` all `/`, name "Lollo") and its icons, as assets; every page a lollo service renders itself (landing, notifications, vault, browser, Gmail triggers, News Radar) links it with `crossorigin="use-credentials"`, as the vault's does today. The code editor's workbench pages are code-server's own HTML, which the gate passes through and does not rewrite, so they carry no lollo manifest; the guide says to add Lollo from another page. `landing` runs whenever any lollo page is on, so the link never dangles, and the landing page stays without script. On iPhone, the Home Screen app opens on the landing page, and the vault and the browser are pages inside it.
- code-server's own service worker keeps `/code-server/`: the more specific scope controls those pages, and the gate keeps it from widening.

Alternative: keep a service worker per sidecar and let one page manage all of them (`getRegistration('/vault/')`). Refused: it keeps two keys and two subscriptions per device, and the page could not post a subscription to another sidecar without that sidecar's CSRF token.

### The page
`/notifications/` behind `admit` (Access JWT, Fetch Metadata, CSRF and Origin on posts, COOP), its script served by the hub with a CSP allowing `'self'` only: enable, disable and test on this device, and the list of the person's devices (when added, which one is this). Subscriptions are stored with the person's email, the endpoint and its keys, and when they were added, on `notifications-data`.

### What leaves the senders
`push.py`, the `/notifications` and `/push/*` routes, `setupPush()`, `manifest.webmanifest`, the icon routes that only the manifest used, and `pywebpush`. Each sender's page gets a "Notifications" link to `/notifications/`. `/vault/sw.js` and `/browser/sw.js` stay for a few releases as a service worker that unregisters itself: a 404 does not remove an installed service worker, a changed script does. Nothing is sent to the old subscriptions, so no device gets a notification twice even before that.

## Risks / Trade-offs

- [Same-origin script, a code-server webview say, can call `getRegistration('/')` and unsubscribe a device, so it would miss an erase notice] → Unchanged from today (it can do the same to `/vault/`). It cannot send: the subscription accepts only pushes signed with the hub's key. The page lists the person's devices so a missing one shows; the docs state the limit.
- [The hub is a new single point for every notification] → It restarts on its own (`restart: unless-stopped`), is not recreated by profile changes, and the vault's erase refuses to start without it.
- [A compromised sender can still ring every device with its own notifications] → Its prefix and tag are enforced and its rate is capped; it cannot pose as another sender.
- [One more container and image per stack] → Small (a Python service like `landing`), and only with the vault or the browser on.
- [Everyone must enable notifications again, and iPhone users replace their Home Screen apps; until then they get none] → The release notes say so; vault requests keep posting their link in the chat, and an erase can still be scheduled (the hub accepts its notice with no device enrolled).

## Migration Plan

All in one release, during `lolloctl up`:
1. `ensure_notify_secrets` adds `LOLLO_VAPID_*` and the tokens to each stack's `stack.env`. `VAULT_VAPID_*` and `BROWSER_VAPID_*` stay there unused; a later release removes them.
2. `check_stack` now requires the new names, not the old.
3. `lollo-notifications` starts with an empty `notifications-data`.
4. The vault and the browser start with the client; on first start each deletes its old subscriptions (the vault's `push_subscriptions` rows, the browser's `push.json`).
5. People open Lollo, then Notifications, then Enable, once per device; on iPhone, they add Lollo to the Home Screen and delete the old apps.

AGENTS.md's "never rotate the VAPID keys" applies to `LOLLO_VAPID_*` from then on; the switch is recorded as a one-time retirement of the old pairs, not a rotation of the new one.

Rollback: the previous release still finds its old keys in `stack.env`, but its subscriptions were deleted, so people enable notifications again on each sidecar's page. `notifications-data` is left as it is.

## Open Questions

- The rate limit's numbers per source, and the length caps: settled with the tests.
- How many releases keep the self-unregistering `sw.js` before it goes.

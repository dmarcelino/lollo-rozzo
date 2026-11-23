# Proposal

## Why

A person who wants every lollo notification turns them on in two places, the vault's and the browser's Notifications pages, on every device; on an iPhone, where push reaches only Home Screen web apps, that means installing two lollo apps, since each page has its own manifest, service worker and VAPID keys. The two implementations are near copies (`push.py`, `sw.js`, `setupPush()`, the `/push/*` routes), and every new lollo page that wants to notify would add a third. One switch per device, and one Home Screen app, is what the person needs; Lettuce's own notifications stay separate, being upstream and on another origin.

## What Changes

- **A new lollo sidecar, `lollo-notifications`**, with no profile of its own: it runs whenever `lollo-vault` or `lollo-browser` is on, as `landing` does for every lollo page. It holds the stack's one VAPID pair (`LOLLO_VAPID_*`, generated once, never rotated), the devices' subscriptions on a volume of its own (`notifications-data`, backed up), and the person's page at `/notifications/`: enable, disable and test this device, and the list of the person's devices.
- **Senders push through it**, never on their own: the vault and the browser post a notification to `/notify/v1/send` on the stack network, each with a token of its own (`NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`) that only it and the hub hold. The hub refuses a request with an `Origin` or Cloudflare's tunnel headers, and a payload whose link or tag is not the sender's own; it caps lengths and rate.
- **One service worker for the lollo host**, at scope `/`, served by the hub, and **one manifest**, served by `landing` as an asset and linked from every lollo page, so "Add to Home Screen" from any lollo page gives the same "Lollo" app. The landing page links to `/notifications/` and keeps no script.
- **The vault's erase keeps its warning:** an erase without the passphrase is scheduled only once the hub has accepted the "erase scheduled" notice, and the extra hour after a restart counts only from when the hub accepted that notice.
- **`lollo-common`** gains the senders' client (the same `send()` the senders call today), the payload checks shared by client and hub, and a `guard` for any internal prefix; the two copies of the push code, the `/push/*` routes, both service workers and both manifests leave the vault and the browser.
- **BREAKING (one time):** the vault's and the browser's subscriptions and their keys (`VAULT_VAPID_*`, `BROWSER_VAPID_*`) are retired. On upgrade every person turns notifications on again in Lollo, once per device, and on iPhone replaces the "Vault" and "Browser" Home Screen apps with "Lollo". The release notes say so; nothing else tells them.

## Capabilities

### New Capabilities
- `notifications/delivery`: who may send a notification and what it may say (the senders' tokens, the payload checks, the rate), the hub's keys and subscriptions, and delivery to the person's devices.
- `notifications/devices`: the person's `/notifications/` page, the one service worker and the one Home Screen app, what tapping a notification may open, and how a device's subscription is protected and shown.
- `vault-erase-notices`: the notices of an erase without the passphrase, and the rule that the hub must accept the "scheduled" notice and the restart notice before the erase's clock runs.

### Modified Capabilities
- `browser/live-view`: its "Push notifications" requirement no longer has the browser hold push keys or subscriptions or a Notifications page; it sends through the hub.
- `browser/isolation`: push keys leave `lollo-browser` ("Pages run apart from what controls them" lists them among what it holds), and push subscriptions leave `browser-data` ("Browser data stays on its own volume").

## Impact

- **New:** `notifications/` (the service, a Dockerfile with a `test` stage, a README with Rules, `AGENTS.md` and its `CLAUDE.md` symlink), `compose/notifications.yml`, `docs/notifications.md` with its flow diagram, a line in `release/images`.
- **Removed or emptied:** `vault/src/lollo_vault/push.py`, `browser/service/src/lollo_browser/push.py`, both `sw.js` (each replaced for a while by one that unregisters itself), both `manifest.webmanifest`, the `/notifications` and `/push/*` routes and `setupPush()` in both `app.js`; `pywebpush` leaves both images.
- **`lollo-common`:** the client, the payload checks, `guard` taking a prefix and a token per sender. Every image that installs it rebuilds.
- **lolloctl and lib:** `ensure_notify_secrets` (the VAPID pair and the tokens, whatever the profiles); `push_env_lines` and `ensure_browser_secrets` stop making VAPID pairs; `check_stack` requires `LOLLO_VAPID_*` and the tokens instead of the old pairs, with `check_notifications` and a rule keeping each token out of every service but the hub and its sender; the tunnel's `/notifications/` route when the vault or the browser is on, and `lollo_services`; `notifications-data` in `backup` and `restore`.
- **Compose:** `compose/landing.yml` (the manifest is an asset; no new profile), `compose/vault.yml` and `compose/browser.yml` (a token instead of VAPID keys).
- **Tests and CI:** `test_check_*` tests, `tests/run-sidecar.sh notifications` and a CI matrix entry; the vault's and the browser's push tests move to the client.
- **Docs:** `docs/user-guide.md` step 4, `docs/vault.md` steps 4 and 7 and its security notes, `docs/browser.md`, `landing/README.md`, `lollo-common/README.md`, `vault/README.md`, `browser/README.md`, `lib/README.md`, `backup/README.md`, CONTRIBUTING (the sidecar list), the AGENTS.md directives (VAPID rotation, a Notifications entry), ARCHITECTURE.md, docs/architecture.md, `lollo-rozzo-overview` and the diagrams.
- **People:** a one-time re-enable on every device; until then, vault requests still post their link in the chat.

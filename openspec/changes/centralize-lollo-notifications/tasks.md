# Tasks

## 1. Shared code in lollo-common

- [x] 1.1 Generalise `guard` in `lollo_common/executor.py` to take a prefix and a map from token to source (the handler learns which source called), keep the executor's routes on it unchanged, and verify `tests/run-sidecar.sh landing`, `gmail-triggers` and `news-radar` pass unchanged
- [x] 1.2 Add the notification payload checks (exactly `title`, `body`, `url`, `tag`; length caps; `url` a path under `/<source>/`; `tag` starting with `<source>-`; every field through `one_line`) as a module of `lollo-common`, and verify with tests in the vault's suite that each refusal in `notifications/delivery` "A sender says only what is its own" holds
- [x] 1.3 Add the senders' client: `send(payload)` (background, never raises, logs the outcome without title or body) and `accept(payload) -> bool` (waits about 5 s for the 202), with the token from `NOTIFY_TOKEN` and the service at `http://lollo-notifications:8000/notify/v1/send`, and an injectable transport for tests; verify with tests against a local stub that a refusal, a timeout and a 202 each give the right result
- [x] 1.4 Update `lollo-common/README.md` (Files, who uses what, a Rule for the send API like the executor API's) and verify `node scripts/check-doc-links.mjs` passes

## 2. The notifications service

- [x] 2.1 Add `notifications/` (`pyproject.toml` with pinned `pywebpush`, a Dockerfile on Python 3.14 slim with `lollo-common` from its build context and a `test` stage, uid of its own, port 8000, `.dockerignore`) and `tests/run-sidecar.sh notifications`, and verify the image builds and an empty test suite runs
- [x] 2.2 Implement settings (the Access settings, `LOLLO_VAPID_*`, `PUSH_VAPID_CONTACT_EMAIL`, a token per sender from `NOTIFY_TOKEN_<SOURCE>`, refusing to start on a malformed or duplicate token) and verify with pytest
- [x] 2.3 Implement the subscription store on `/data` (email, endpoint, keys, when added; capped; atomic writes) and verify with pytest that add, remove and per-person listing work and survive a restart
- [x] 2.4 Implement `POST /notify/v1/send` behind the generalised `guard` and the payload checks, answering 202 once queued, with a rate limit per source, and verify with pytest every scenario of `notifications/delivery` "The send API is for senders only", "A sender says only what is its own" and "Senders are rate limited"
- [x] 2.5 Implement delivery (every subscription of a person in `ALLOWED_USERS`, delete on 404 or 410, log other failures, log source, tag and outcome only) and verify with pytest and a fake sender the scenarios of "Accepted notifications reach every enrolled device" and "No secret value in a notification" (service logs)
- [x] 2.6 Implement `/notifications/` (`admit` with `PAGE`, `FORM`, `FETCH`; CSRF and Origin on posts; COOP; CSP with `script-src 'self'`): enable, disable, test, the person's device list with the device in use marked, and its script, icons and stylesheet; verify with pytest the scenarios of `notifications/devices` "The notifications page" and "The notifications page is a lollo page"
- [x] 2.7 Add `sw.js` (served with `Service-Worker-Allowed: /`, registered at scope `/`; no fetch handler; no notification actions; a tap opens the link only under `/vault/`, `/browser/` or `/notifications/`, else `/`, in an open window first) and verify with pytest that the header and content type are served, and with `node --test` against the real `sw.js` in a `vm` context that the tap target, window reuse, the shown notification (no actions) and the listener set (no fetch handler) are right; a real tap is checked by hand in 7.2
- [x] 2.8 Write `notifications/README.md` (Files, How it plugs in, Rules: senders only with their own token, a sender says only what is its own, no title or body in logs, the key never rotated; Build and test), its `AGENTS.md` pointer and `CLAUDE.md` symlink, and `docs/notifications.md` (enable once per device, the iPhone Home Screen app, the device list, the same-origin unsubscribe limit) with its flow diagram; verify `node scripts/check-doc-links.mjs` passes
- [x] 2.9 Add `notifications` to the `sidecar` matrix in `.github/workflows/ci.yml` and the command to the Commands block in AGENTS.md and CONTRIBUTING.md, and verify `tests/run.sh test_ci_gate_` passes

## 3. Compose wiring and lolloctl

- [x] 3.1 Add `compose/notifications.yml` (profiles `lollo-vault` and `lollo-browser`, `default` network only, `notifications-data` at `/data`, read-only root, no capabilities, `no-new-privileges`, limits, `CF_LOLLO_AUD` and `LOLLO_ORIGIN`, the VAPID pair, both tokens, no `COMPOSE_PROFILES` or `LOLLO_PROFILES`) and add it to the `compose` wrapper's overlay list; verify with `tests/run.sh` that a stack renders it with either sender on and not with neither
- [x] 3.2 Add `ensure_notify_secrets` to `up` (a VAPID pair as `LOLLO_VAPID_*` and `NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`, each made once whatever the profiles, never in argv) and stop `push_env_lines` and `ensure_browser_secrets` making VAPID pairs; verify with `test_up_` tests that existing values are kept and a stack without the browser still gets its token
- [x] 3.3 Give `lollo-vault` and `lollo-browser` their own token as `NOTIFY_TOKEN` in `compose/vault.yml` and `compose/browser.yml` in place of their VAPID keys, and verify the rendered environments with `tests/run.sh`
- [x] 3.4 Route `^/notifications(/|$)` to `http://lollo-notifications:8000` when `lollo-vault` or `lollo-browser` is on (`LOLLO_NOTIFICATIONS_PATH`, `LOLLO_NOTIFICATIONS_SERVICE` in `lib/cloudflare.sh`, before the landing page's rule) and list it in `lollo_services`; verify with `test_tunnel_` tests both cases and that `/notify/v1/send` matches no rule
- [x] 3.5 Add `check_notifications` (a hardened sidecar on `default` only, mounting only `notifications-data`) and the whatever-the-profiles rules (each token 64 lowercase hex, all distinct and not `LOLLO_EXECUTOR_TOKEN`, a token's value in no service but the hub and its sender, `notifications-data` mounted by nothing else and reached by no `volumes_from`), each with a `test_check_*` test
- [x] 3.6 Make `check_stack` require `LOLLO_VAPID_*` and the tokens instead of `VAULT_VAPID_*` and `BROWSER_VAPID_*` (`lib/common.sh`, the push keys list and `check_browser`'s), and verify with `test_check_` tests that an upgraded stack passes once `up` has run
- [x] 3.7 Add `notifications-data` to `BACKUP_VOLUMES` with the service's owner, so `backup` copies it and `restore` puts it back, and make `remove`'s confirmation name it; verify with `test_backup_`, `test_restore_` and `test_remove_` tests
- [x] 3.8 Add the `notifications` image to `release/images` and verify the release tests (`release/README.md`, Build and test) pass
- [x] 3.9 Update `lib/README.md`, `compose/README.md`, `backup/README.md`, CONTRIBUTING (sidecar list, per-profile table, what `check_stack` enforces), the AGENTS.md directives (the VAPID directive names `LOLLO_VAPID_*`; a Notifications entry under Features; a row for `notifications/` in the folder table), ARCHITECTURE.md, `docs/architecture.md` and `lollo-rozzo-overview` with their diagrams; verify `shellcheck` and `node scripts/check-doc-links.mjs` pass

## 4. The landing page and the one Home Screen app

- [x] 4.1 Serve `/assets/manifest.webmanifest` (name "Lollo", `id`, `scope` and `start_url` `/`, display standalone) and a 512 px icon from `landing`, link the manifest from `base.html` with `crossorigin="use-credentials"`, and show a Notifications link to `/notifications/` when `lollo-vault` or `lollo-browser` is in `LOLLO_PROFILES`; verify with pytest, including that every page's CSP still allows no script and `test_every_page_route_is_the_root_or_under_assets` holds
- [x] 4.2 Update `landing/README.md` (the manifest, the link; the page stays passive) and `docs/user-guide.md` step 4 (one Notifications page for the lollo, plus Lettuce's own) with its flow diagram; verify `node scripts/check-doc-links.mjs` passes
- [x] 4.3 Link `/assets/manifest.webmanifest` (`crossorigin="use-credentials"`) and the `/assets/icon-192.png` apple-touch-icon from the Gmail triggers and News Radar pages, with `manifest-src 'self'` in their CSPs, and say in `docs/notifications.md` and `docs/user-guide.md` that Lollo is added from any lollo page but the code editor's; verify with `tests/run-sidecar.sh gmail-triggers`, `tests/run-sidecar.sh news-radar` and `node scripts/check-doc-links.mjs`

## 5. The vault sends through the service

- [x] 5.1 Replace the vault's `Pusher` with the `lollo-common` client and verify with the vault's tests (a fake transport in place of `FakeSender`) that requests, the locked notice and the erase notices are sent with their tags and links, and that no value reaches a payload
- [x] 5.2 Make `schedule_erase` call `accept()` before storing the schedule and the unlock page say notifications are unavailable when it fails; verify with pytest the scenarios of `vault-erase-notices` "An erase is scheduled only once its notice is accepted"
- [x] 5.3 Make the overdue erase's notice retry until accepted and its grace period count from that acceptance, and the "cancelled" and "done" notices retry without holding anything up; verify with pytest and the fake clock the scenarios of "The grace after a restart waits for its notice" and "Later notices never hold anything up"
- [x] 5.4 Remove the vault's `push.py`, the `/notifications` and `/push/*` routes, `setupPush()`, its manifest and the icon routes only the manifest used, link the root manifest and `/notifications/` from its pages, serve a `sw.js` that unregisters itself, delete the `push_subscriptions` rows on first start, and drop `pywebpush`; verify with pytest that the routes are gone, the old `sw.js` unregisters, and the table is empty after start
- [x] 5.5 Update `vault/README.md` (Files, environment, Rules: the erase's notices and the service), `docs/vault.md` (steps 4 and 7, the security notes on the erase and notifications) and the vault diagrams; verify `tests/run-sidecar.sh vault` and `node scripts/check-doc-links.mjs` pass

## 6. The browser sends through the service

- [x] 6.1 Replace the browser's `Pusher` with the `lollo-common` client, keeping the value scrub before sending, and verify with the browser's tests that hand-offs and checkouts are sent with `browser-` tags and `/browser/` links and scrubbed payloads
- [x] 6.2 Remove the browser's `push.py`, `Subscriptions`, the `/notifications` and `/push/*` routes, `setupPush()`, its manifest and the icon routes only the manifest used, link the root manifest and `/notifications/` from its pages, serve a `sw.js` that unregisters itself, delete `push.json` on first start, and drop `pywebpush`; verify with pytest that the routes are gone and `push.json` is removed
- [x] 6.3 Update `browser/README.md`, `docs/browser.md` (step 2's Notifications, the push keys note) and the browser flow diagram; verify `tests/run-sidecar.sh browser`, `tests/run-browser-acceptance.sh` and `node scripts/check-doc-links.mjs` pass

## 7. Integration

- [x] 7.1 Run every check in AGENTS.md's Commands block (all offline tests, shellcheck, every `tests/run-sidecar.sh` target, the executor's tests, the browser acceptance suite, the doc link check) and verify all pass
- [ ] 7.2 On a real stack upgraded from the previous release: `up` adds the new secrets and `check` passes; on a phone, add Lollo to the Home Screen from `/vault/`, enable notifications once, then verify a vault request and a browser hand-off each arrive once and a tap opens the right page; stop `lollo-notifications` and verify an erase is refused with the notice, then start it and verify an erase is scheduled and announced

## 8. Review fixes

- [x] 8.1 Make `up`, when it first creates the notify secrets on a stack with a sender on, say to run `lolloctl profiles` once so the tunnel routes `/notifications/`, and correct `docs/notifications.md` and `docs/operations.md`; verify with a `test_up_` test
- [x] 8.2 Give each source its own queue and worker in the hub, send a notification to a person's devices in parallel, and delete a subscription after several timeouts in a row; verify with pytest the scenarios of "One sender never delays another"
- [x] 8.3 Hand secret values to `jq` through the environment, never `--arg`, in `check_stack` (`holders` and any other), and extend the argv test to cover `jq`; verify with `tests/run.sh test_up_never_puts_the_notify_secrets_in_argv` and a grep that no `--arg` carries a secret
- [x] 8.4 Hold a lock across the vault's erase check, the notice and the schedule, and send the "cancelled" notice when scheduling fails after the notice was accepted; verify with pytest the scenarios "Unlocked while waiting" and "Asked twice at once"
- [x] 8.5 Store the push key's public half with the subscriptions and drop subscriptions made under another key at start; verify with pytest the scenario "Key and subscriptions apart"
- [x] 8.6 Make the service worker reuse a window only under the link's prefix, or on `/` or `/notifications/`, else open a new one; verify with the node tests the scenario "Working in another lollo page"
- [x] 8.7 Put the sender's label in front of every title in the hub, and drop the senders' own "Vault" prefixes from their titles; verify with pytest the scenario "Browser imitates a vault request" and the senders' tests
- [x] 8.8 Count only accepted notifications against the hub's per-source cap, and only scheduled erases against the vault's hourly cap; verify with pytest the scenarios "Service busy" and "Retried while notifications are down"
- [x] 8.9 Make `docs/notifications.md` and `notifications/README.md` claim only what the code holds (labels, separate queues); verify `node scripts/check-doc-links.mjs` passes

## Workflow follow-up

- Say in the pull request's description, for the release notes, that every person enables notifications again on `/notifications/`, once per device, and that iPhone users replace the "Vault" and "Browser" Home Screen apps with "Lollo".
- Archive the change after review, and verify the archived specs.

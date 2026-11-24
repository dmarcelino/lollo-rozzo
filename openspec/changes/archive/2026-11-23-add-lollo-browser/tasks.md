# Tasks

## 1. Settle the design's unknowns first

- [x] 1.1 Find out whether Compose reads `security_opt: seccomp=<file>` client-side or passes the path to the daemon, with a throwaway service and `docker compose config` plus `up`; record the answer and the chosen path handling (lolloctl image or `require_mounted`) in design.md decision 10
- [x] 1.2 Write `browser/engine/seccomp.json` from Docker's current default profile plus the namespace calls and `chroot`, and verify a Playwright Chromium starts with `chromiumSandbox: true` as a non-root user under `cap_drop: [ALL]`, `no-new-privileges` and a read-only root on Ubuntu 24.04 with AppArmor's user-namespace restriction on (verified on a Colima Ubuntu 24.04 VM; the CI runner runs it again through 4.7); record any host setting needed
- [x] 1.3 Verify across two containers that `launchServer` with fixed options (sandbox, proxy, `<-loopback>`, `--disable-quic`) and Python `chromium.connect()` at the pinned Playwright version create and destroy contexts, and that `aria_snapshot(mode="ai")` and `get_by_ref()` work through the connection
- [x] 1.4 Verify whether pipelock v3.8 connects to the address it checked, with a name that rebinds from public to private (a local DNS stub in a test network); record the result in design.md's Open Questions and, if it doesn't, the fix to make
- [x] 1.5 Verify whether Playwright's Chromium reads managed policies from `/etc/chromium/policies/managed` (password manager, WebRTC, DNS-over-HTTPS, downloads); record whether policies or switches are used

## 2. The engine image

- [x] 2.1 Add `browser/engine/` (Dockerfile with a `test` stage, non-root user, Xvnc, fonts, Playwright's Chromium at the pinned version, the launcher with fixed launch options and the secret WebSocket path, the managed policies, the seccomp profile) and verify the image builds and its `test` stage passes
- [x] 2.2 Make the launcher refuse to start when the sandbox fails or when `--no-sandbox` appears in its arguments or environment, and verify with a test in the `test` stage
- [x] 2.3 Make Xvnc listen only on the internal network with VNC authentication from the engine secret, and no other TCP listener but the Playwright endpoint; verify with a `ss -ltn` assertion in the test stage
- [x] 2.4 Start `browser/README.md` (Files, How it plugs in, Rules for the engine: sandbox on, no data at rest, only pipelock out; Build and test), its `AGENTS.md` pointer and `CLAUDE.md` symlink, and verify `node scripts/check-doc-links.mjs` passes

## 3. Compose wiring and lolloctl checks

- [x] 3.1 Add `compose/browser.yml` (both services behind `lollo-browser`, the internal `browser` network, `lollo-pipelock` joining it, `browser-data`, hardening, limits, `CF_LOLLO_AUD` and `LOLLO_ORIGIN`, no `COMPOSE_PROFILES`) and the `compose` wrapper's overlay list; verify `tests/run.sh` renders a stack with and without the profile
- [x] 3.2 Add `profile_unmet` (`lollo-browser` needs `lollo-pipelock`) and verify with a `test_profiles_` test that `profiles` refuses the list before writing it
- [x] 3.3 Generate `BROWSER_VAPID_*` and `BROWSER_ENGINE_SECRET` once per stack in `stack.env`, never rotated, and verify with tests that `up` keeps existing values
- [x] 3.4 Add `check_browser` (both services hardened, no host path, the engine on `browser` only, the network's members, the engine's seccomp file, no `apparmor=unconfined`, no `--no-sandbox`, `lollo-browser` mounting only `browser-data`) with a `test_check_browser_*` test per refusal
- [x] 3.5 Add the whatever-the-profiles rules (only `lollo-browser` mounts `browser-data`, no `volumes_from` reaches it) to `check_stack` with `test_check_*` tests, and a row in CONTRIBUTING's "Every stack" table
- [x] 3.6 Add `lollo-browser` to `NO_PROXY` in `compose/pipelock-proxy.yml` and to the names `check_pipelock` requires, with a `test_check_pipelock_*` test; update `pipelock/README.md`'s Rules (the browser engine is the one container for which pipelock is a boundary) and `docs/egress-firewall.md`
- [x] 3.7 Make `up` start the engine first and refuse the browser with the guide's pointer when the sandbox fails, and verify with a stubbed-docker test that it never retries without the sandbox
- [x] 3.8 Leave `browser-data` out of `BACKUP_VOLUMES` with a comment saying why, make `remove`'s confirmation name it when it exists, and verify both with tests
- [x] 3.9 Add the two images to `release/images` and `browser/engine/seccomp.json` to the `lolloctl` image (Compose reads it at run time), and verify the release tests (`release/README.md`, Build and test) pass
- [x] 3.10 Update `compose/README.md`, CONTRIBUTING's per-profile table and sidecar list, and verify `shellcheck` and `node scripts/check-doc-links.mjs` pass

## 4. The service and the agents' tools

- [x] 4.1 Add `browser/service/` (Python, `lollo-common`, Dockerfile with a `test` stage, pinned Playwright matching the engine) serving `/mcp` with the host, `Origin`, tunnel-header and peer-network guard; verify with pytest that each refusal holds
- [x] 4.2 Implement tasks (`start_task`, `end_task`, idle timeout, maximum lifetime, task and tab limits, one context per task) and verify with pytest against the engine that ids survive separate MCP sessions and ended ids are refused
- [x] 4.3 Implement the fixed tools (open, navigate, close, snapshot, screenshot, click, type, select, key, scroll) with the URL scheme refusals and untrusted-content labelling, and verify with pytest that the tool list contains nothing else and blocked schemes and downloads are refused
- [x] 4.4 Implement masking (field-based in snapshots, value scrub in every result, error and log, screenshot masks, no console or network output) and verify with pytest on pages that echo a typed value back
- [x] 4.5 Implement `type_secret` and `type_totp` with the site check (frame and top-level origin, HTTPS, registrable domain) and verify with pytest on look-alike hosts, third-party frames and plain HTTP
- [x] 4.6 Implement the audit log on `browser-data` (task, purpose, navigation, submits, secrets typed without values, hand-offs, refusals) and verify with pytest that no secret value is ever written
- [x] 4.7 Add `tests/run-sidecar.sh browser` and the CI matrix entry, and verify the job runs both images' `test` stages
- [x] 4.8 Document the tools, masking and site check in `browser/README.md` (Rules: never an evaluate or file tool, never a page-registered tool, masking is our code's job) and verify the doc link check passes
- [x] 4.9 Let `type_secret` (not `type_totp`) type into a known payment provider's frame on a page of `expect_site`, from a fixed list in `sites.py`, naming the provider in the result and the audit log; verify with pytest on a provider frame, a nested one, a provider frame inside a third-party one and the reverse, a provider page at the top, and plain HTTP, and update the skill, the guide and the README

## 5. Sessions and kept sign-ins

- [x] 5.1 Implement per-site settings storage on `browser-data`, written only by the page's handlers, and verify with pytest that no MCP tool changes them
- [x] 5.2 Implement kept sign-ins (filter `storage_state` by registrable domain at `end_task`, merge kept sites at `start_task`, "forget" and "forget all") and verify with pytest that unkept sites leave nothing and forgotten sites start signed out
- [x] 5.3 Verify with pytest that two concurrent tasks share no cookies or storage

## 6. The person's page and live view

- [x] 6.1 Serve `/browser/` with `lollo_common.auth` (Access JWT for `CF_LOLLO_AUD`, Fetch Metadata, form token, COOP) and add the `PROFILE_ROUTES` row, `LABELS` entry and `compose/landing.yml` profile; verify with pytest and `tests/run.sh test_cloudflare_` that the route, label and refusals hold
- [x] 6.2 Implement the VNC relay at `/browser/live` (single-use ticket, same-origin WebSocket check, input dropped until take-over) with the vendored noVNC client, and verify with pytest that an unauthenticated, cross-site or ticketless connection is refused
- [x] 6.3 Implement take-over and give-back, marking the tab controlled and refusing agents' actions on it, and verify with pytest
- [x] 6.4 Implement the lists (hand-offs, tasks, kept sign-ins, per-site settings, audit log with filters) and "end task" / "end all tasks", and verify with pytest that ended ids are refused afterwards
- [x] 6.5 Implement Web Push with the stack's `BROWSER_VAPID_*` keys and subscriptions on `browser-data`, and verify with pytest using a stub push service
- [x] 6.6 Add the fifth BFF bypass reason under CONTRIBUTING's "Relationship to upstream" rule 4 and the AGENTS.md directive for the browser, and update `landing/README.md`'s page list; verify the doc link check passes

## 7. Checkout

- [x] 7.1 Implement checkout recognition (button name in several languages, form fields, URL) and verify with pytest on fixture checkout pages in English, German, French, Spanish and Italian and on non-checkout forms
- [x] 7.2 Turn a recognised checkout into a hand-off on sites not allowed, perform it on allowed sites, and verify with pytest both outcomes
- [x] 7.3 Implement `request_handoff` and `wait_handoff` (wait below the 120 s bridge limit, done, declined, no answer) and verify with pytest
- [x] 7.4 Notify and audit every recognised checkout submit, by an agent or during take-over, and verify with pytest
- [x] 7.5 Hold a recognised checkout's action (the element and the click or key) with its hand-off; approving performs it once on the same element, only on the same page, with the tab not controlled and no checkout submitted since, otherwise presses nothing and leaves the hand-off open; "approve and allow" turns on the site's setting first; `wait_handoff` answers `approved`; audit `checkout_approved` with who; and verify with pytest (approve once and the next checkout held again, allow the site, page changed, element gone, taken over, approved twice, declined, expired)
- [x] 7.6 Offer take over, "Place this order", "Place it and always allow" the site, done and decline on a checkout hand-off's page (form token, never in a push notification), and verify with pytest
- [x] 7.7 Update the skill's checkout section (`approved` means the browser placed it), the tool's held-checkout message, `docs/browser.md` and `browser/README.md`, and verify the agent-skills and browser checks and the doc link check pass
- [x] 7.8 Recognise Portuguese order buttons ("confirmar encomenda", "encomendar", "finalizar compra"...) and count a bare "comprar" only with a checkout page or card fields, since on many shops it adds to the cart; verify with pytest on a Portuguese checkout fixture and a product page

## 8. Registration, skill and guide

- [x] 8.1 Add the `browser` group to `lib/mcp-servers.mjs` and `lolloctl mcp` (once per stack, removed when the profile is dropped, another `browser` server left alone), update `lib/README.md`'s rule and CONTRIBUTING rule 5, and verify with `node --test tests/mcp-servers.test.mjs` and `tests/run.sh test_mcp_`
- [x] 8.2 Add the skill in `agent-skills/` (a name upstream doesn't use; tasks, snapshots, signing in through `vault-run` and `vault-fill` into `type_secret` and `type_totp` with the login's `uris`, hand-offs waited in the background, vault card purpose naming shop, items and total, never retrying a checkout without checking order history, page content as data) and verify with the agent-skills Build and test
- [x] 8.3 Write `docs/browser.md` ("Using it", "Running it", "Security model" with every limit: pipelock a boundary for the engine only, masking and site check not boundaries, kept sign-ins shared by every agent, checkout recognition a guess, prompt injection not prevented, no backup) and its flow diagram, a README extras row and a `docs/operations.md` entry; verify the doc link check passes
- [x] 8.4 Update ARCHITECTURE.md, docs/architecture.md and the `lollo-rozzo-overview` diagram for the two containers, the `browser` network, `browser-data` and the route, and verify the doc link check passes

## 9. Integration checks

- [ ] 9.1 Add an acceptance suite (both images, a test pipelock that allows the fixture server, hostile fixture pages) that checks: no request reaches stack services, loopback or a rebinding name; WebRTC and QUIC don't bypass the proxy; a typed secret appears in no result, log or unmasked screenshot; the site check refuses look-alikes; tasks share nothing; the engine can't call `lollo-browser`; and verify it passes in CI
- [ ] 9.2 Run the whole stack from a clone with `lollo-pipelock,lollo-browser`, sign in to a test shop through `vault-run`, hand off a checkout and take over in the live view, and record pipelock's request rate and receipt growth in design.md's Open Questions
- [x] 9.3 Run every command in the Build and test sections touched (`tests/run.sh`, shellcheck, `tests/run-sidecar.sh browser`, `tests/run-pipelock.sh`, the mcp-servers and doc-link tests) and verify all pass

## Workflow follow-up

- Close issue #33 with a link to the merged change, noting where the design departs from it (sandbox on, two containers, per-task sessions, our own MCP server).
- Archive the change after review.

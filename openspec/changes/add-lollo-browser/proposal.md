# Proposal

## Why

Agents can search and fetch pages, but cannot act online on the person's behalf: sign in to a site, fill a cart, get past a challenge, finish an order. Issue #33 proposes a browser for that. Browsing hostile pages with an agent that can sign in and spend money is the riskiest thing a stack would do, so this change takes the issue's design and the security review done for it (sandbox kept on, secrets masked in our own code, sessions per task, gates the agent cannot change) into one sidecar whose limits are stated plainly.

## What Changes

- A new lollo sidecar behind the profile `lollo-browser`, in two containers: `lollo-browser-engine`, a headed Chromium on a virtual display where pages run, and `lollo-browser`, which drives it with Playwright and holds the agents' MCP server (our own, not Playwright MCP, which says it is not a security boundary and ships code-execution and page-registered tools), the person's page, the settings, the audit log and the kept sign-ins.
- **Agents' tools** at `http://lollo-browser:8000/mcp`: tasks with an explicit id, navigation, snapshots and screenshots with secret values masked, clicks and typing, typing a vault value with a site check, one-time codes from an authenticator key, and hand-offs to the person. No script evaluation, no remote-debugging access, no file access.
- **Sessions per task:** every task gets a new, signed-out browser context, destroyed when the task ends, times out or the service restarts. The person keeps sign-ins only for the sites they choose ("keep me signed in", per site), saved as that site's cookies and storage on a volume only the browser mounts, never as a Chromium profile, never backed up, with a "forget" action.
- **Vault sign-ins** reuse the vault unchanged: the agent pipes `vault-run`/`vault-fill` into the typing tool, so values stay out of the conversation. The browser never calls the vault.
- **Checkout:** by default a recognised checkout submit is held for the person, who takes over, approves it once (the browser then performs the held click), approves it and allows the site from then on, or declines. Per site, the person may allow agents to finish a checkout themselves. Every checkout submitted is notified and audited. Recognising a checkout is a guess, and the docs say so; the real limits on spending are the vault's approval, the bank's confirmation and the card's own limits.
- **`/browser/` on the lollo host:** live view of the whole browser window (address bar included) with take-over, the hand-offs, per-site settings, kept sign-ins and the audit log, behind the lollo's Access application, with push notifications.
- **Isolation:** Chromium's sandbox stays on (non-root, a seccomp profile of ours, every capability dropped). The engine sits on an internal network of its own, shared only with `lollo-browser` and `lollo-pipelock`, so its only way out is pipelock, which refuses private addresses; loopback, QUIC, WebRTC and DNS-over-HTTPS cannot go around it. The app-server is not on that network and no upstream service changes. `lollo-browser` requires `lollo-pipelock`.
- **lolloctl:** `check_browser` and whatever-the-profiles rules for its volume, a `PROFILE_ROUTES` row, push keys and the engine's secret per stack, `lollo-browser` required in the app-server's `NO_PROXY`, and `mcp` owning a fourth kind of entry.
- **Written reasons and docs:** a fifth BFF bypass under CONTRIBUTING's "Relationship to upstream"; pipelock described as a real boundary for this one container only; a skill in `agent-skills/`; a guide with a plain security model; the folder README, ARCHITECTURE and diagrams.

## Capabilities

### New Capabilities
- `browser/isolation`: the browser container's sandbox, hardening, network and egress, storage, and the checks `lolloctl` runs on them.
- `browser/agent-tools`: the MCP tools agents use, the tasks they act in, what tool results may never contain, and the audit of their actions.
- `browser/sessions`: per-task contexts, the sign-ins a person keeps per site, and how sign-ins with vault values work.
- `browser/checkout`: how a checkout is held for the person's answer (take over, approve once, approve and allow the site, decline) or finished by an agent, the per-site permission, and the notification and audit of every checkout.
- `browser/live-view`: the person's `/browser/` page: live view and take-over, hand-offs, settings, kept sign-ins and the audit log, and how it is protected.

### Modified Capabilities
None: the project has no specs yet. The existing rules this change touches (`mcp`'s entry kinds, `check_pipelock`'s required names, the BFF bypasses, pipelock's advisory wording) live in the developer docs and are updated there.

## Impact

- **New:** `browser/` (the service and the engine, each with a Dockerfile and a `test` stage, the seccomp profile, Chromium's policies, a README with Rules), `compose/browser.yml`, `docs/browser.md` and its flow diagram, an `agent-skills/` skill, two lines in `release/images`.
- **lolloctl and lib:** `check_stack` (`check_browser`, `volume_users` rules for `browser-data`, `check_pipelock`'s names), `profile_unmet`, `PROFILE_ROUTES` and `LABELS`, the `compose` wrapper's overlay list, push keys in the stack's state, `mcp` and `mcp-servers.mjs`, `BACKUP_VOLUMES` deliberately left without it.
- **Compose:** `compose/pipelock-proxy.yml` (`NO_PROXY`), `compose/landing.yml` (profile list), `compose/pipelock.yml` (pipelock joins the browser's network), a new internal network per stack for the engine, `lollo-browser` and `lollo-pipelock`.
- **Tests and CI:** `test_check_*` tests, `tests/run-sidecar.sh browser` and a CI matrix entry, an acceptance suite against hostile local pages.
- **Docs:** CONTRIBUTING (bypass reason, `mcp` rule 5, sidecar list, per-profile table), `lib/README.md`, `pipelock/README.md`, `compose/README.md`, `landing/README.md`, AGENTS.md directives, ARCHITECTURE.md, docs/architecture.md, `lollo-rozzo-overview`, README extras table, docs/operations.md.
- **Pipelock:** more traffic (about a hundred requests per page) on its per-domain limit and its receipts, which it never deletes.
- **Hosts:** unprivileged user namespaces must be available for Chromium's sandbox.

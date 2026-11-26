# Design

## Context

See proposal.md for why. The facts that shape the approach:

- The browser's engine is on the internal `browser` network, and `lollo-pipelock` is its only way out (`browser/isolation`). Every request Chromium makes, after redirects, frames and service workers, arrives at pipelock as a host name, already normalised. Pipelock is therefore a real boundary for the engine, which `check_web_url` and Playwright's `context.route` are not (the audit's report 7: percent-encoded hosts pass a `urlsplit` check, and clicks and service workers go around `route`).
- Pipelock v3.8.0 takes `fetch_proxy.monitoring.blocklist`, which **replaces** its shipped list. That list is six wildcards today (`*.pastebin.com`, `*.hastebin.com`, `*.paste.ee`, `*.transfer.sh`, `*.file.io`, `*.requestbin.com`; from `pipelock generate config --preset balanced`). A check against the local image showed that an exact-host entry refuses that host case-insensitively and with a trailing dot, refuses a percent-encoded host as an invalid URL, leaves `www.example.com` alone, and also refuses a URL that names a denied host in a query value (its nested-URL scan).
- Chromium reads managed policies only from `/etc/opt/chrome_for_testing/policies/managed/`, which is in the engine's read-only root. In a `URLBlocklist`, an entry with a leading dot (`.alfred-lollo.example.com`) blocks that host only, not its subdomains.
- `compose_env` already computes values that Compose then prefers over `stack.env` (`PIPELOCK_DIRECT`, the image names), so nothing in `stack.env` can make them stale.
- Every stack's hosts are in its own `stack.env` (`PUBLIC_ORIGIN`, `LOLLO_HOSTNAME`); the team is `CF_TEAM_DOMAIN` in `config.env`.

## Goals / Non-Goals

**Goals:**
- Deterministic refusal of every stack's two hosts and the Access team domain for anything the engine does, with two independent layers.
- No denied host's cookies in any kept sign-in.
- Warnings, not refusals, where refusing would cost the person a feature (keeping `google.com`, sharing a Google login).

**Non-Goals:**
- The passkey step-up for person-only actions (the audit's L2).
- Making pipelock a boundary for the app-server (SEC-003 is deferred to the network split).
- Refusing whole registrable domains: only exact hosts are denied.

## Decisions

### One deny list, computed by lolloctl
A helper `deny_hosts` prints, comma-separated and sorted: for every stack directory with a `stack.env` on this host, the host of its `PUBLIC_ORIGIN` and its `LOLLO_HOSTNAME`, then `<CF_TEAM_DOMAIN>.cloudflareaccess.com`. `compose_env` passes it as `LOLLO_DENY_HOSTS` to every Compose call, as it does `PIPELOCK_DIRECT`. `compose/pipelock.yml` hands it to `lollo-pipelock`, and `compose/browser.yml` to the engine and to `lollo-browser`. The vault does not get it, so the vault is never recreated by this change or by a later stack being added.
- *Alternative:* read `stacks.conf`. Rejected: an entry there may have no hosts yet, and a stack removed from `stacks.conf` but still on the host still has live hosts.
- *Alternative:* deny `*.<DOMAIN>`. Rejected: it would block the person's other sites on the same domain.

### Pipelock renders its config at start
`pipelock/pipelock.yaml` keeps every setting and gains no blocklist. The entrypoint checks `LOLLO_DENY_HOSTS` (each entry a host name, at most a few hundred), then writes `/tmp/pipelock.yaml`: the baked config with `fetch_proxy.monitoring.blocklist` set to the shipped six entries plus the deny list, and runs pipelock with it. With no variable it writes the shipped list only, so pipelock still works in a stack without the browser.
The six shipped entries live in `pipelock/shipped-blocklist` (one per line). `tests/run-pipelock.sh` compares that file with `pipelock generate config --preset balanced`, so a pipelock upgrade that changes the list fails the test instead of silently dropping entries.
- *Alternative:* list everything in `pipelock.yaml` and add a second blocklist key. Rejected: pipelock has one list, and unknown keys are refused at load.
- The entrypoint renders with `sed`/`printf` on a YAML block it owns (marked by a comment line), not a YAML library, since the image is Alpine with no extra tools; `pipelock check --config /tmp/pipelock.yaml` runs before `exec` and the entrypoint exits on failure.

### Chromium policy rendered by the launcher
The Dockerfile copies `policies.json` to `/opt/engine/policies.json` and makes the managed file `/etc/opt/chrome_for_testing/policies/managed/lollo.json` a symlink to `/tmp/policies/lollo.json`. Before `launchServer`, `launcher.mjs` validates `LOLLO_DENY_HOSTS` (same host-name pattern), reads the base policies, appends `.<host>` for each entry to `URLBlocklist`, and writes the file (0644). It dies with exit 2 when the variable is missing or malformed, or when writing fails, so Chromium never starts without its policies (the password manager and autofill stay off too).
- *Alternative:* two files in the managed directory. Rejected: Chromium does not merge one list policy across files.
- *Alternative:* a Compose `configs:` entry. Rejected: `check_browser` refuses any non-tmpfs mount on the engine, and that rule is worth keeping as it is.
- The file is writable by the engine's own user. Only the browser process could rewrite it, and a page that controls the browser process has already escaped the sandbox; pipelock still refuses.

### Kept sign-ins filter by host
`Sessions` gets the deny list at start. `save` and `initial_state` drop a cookie whose domain, without a leading dot, is a denied host or a subdomain of one, and an origin whose host is one, before the existing per-site filter. A cookie scoped to the registrable domain itself (`.example.com`) is kept: Access sets a host-only cookie on the application's host.

### Warnings on the person's pages
- Sites page: turning on "keep me signed in" for a site whose registrable domain is in a fixed identity-provider list (`google.com`, the only provider `lolloctl` configures, `CF_GOOGLE_IDP_ID`) returns the form with a warning and a confirm box; it saves only once that box is ticked.
- Vault: the approval view checks the login's `uris` field (one URL per line) and sets `identity_provider` on the request view when a host is, or is under, `google.com`; `_decide.html` shows a warning banner. Nothing changes in `service.py`'s tool results.

### The check
`check_browser` gains a rule: the rendered `LOLLO_DENY_HOSTS` of both `lollo-pipelock` and `lollo-browser-engine` contains the stack's `PUBLIC_ORIGIN` host, its `LOLLO_HOSTNAME` and the team domain. It is a list check, not an equality, so another stack's hosts can be missing until that stack's next `up` without failing `check`.

## Risks / Trade-offs

- [A pipelock upgrade changes the shipped blocklist] → `tests/run-pipelock.sh` compares `pipelock/shipped-blocklist` with the preset and fails; the pipelock README's upgrade notes name the file.
- [Adding a stack recreates the other stacks' pipelock and browser at their next `up`, closing open browser tasks] → only on `up`, never on its own; release notes and `docs/browser.md` say so. The vault is not affected.
- [The nested-URL scan refuses a search results URL that names a stack host] → rare and harmless; noted in `docs/egress-firewall.md`.
- [The person's company runs other Access applications on the same team] → agents can no longer reach them in the browser; that is wanted, and `docs/browser.md` says so.
- [A Google session in a task is still powerful outside the stack] → out of scope; the new warnings make it visible, and the audit's S5 (leases for kept sign-ins) addresses it later.
- [The deny list grows with stacks] → one host name pair per stack; the entrypoint and launcher cap it at 500 entries and refuse more.

## Migration Plan

Ships in one release. `up` recreates `lollo-pipelock`, `lollo-browser-engine` and `lollo-browser` because their environment and images change; the vault, Lettuce and the other sidecars are untouched. Rollback: the previous release's images and overlays, with no data change (kept sign-ins only lose cookies that should never have been kept).

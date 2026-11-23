# Design

## Context

See `proposal.md` for why. The design starts from issue #33 (its body and the attached `browser-design.md`, which uses the old names "garden" for lollo and `lettucectl` for `lolloctl`) and the attached `Security_Concerns.md`, and from a review of both against the repository and against what was published up to November 2026 (summarised under "Research" below).

Constraints from the repository that shape it:
- Agents are root in the app-server and share its network namespace with the BFF. Anything they can reach over the network, they can call: an endpoint for agents authenticates nothing about them (CONTRIBUTING, "Adding a lollo sidecar", step 6). What the person sets on a lollo page behind `CF_LOLLO_AUD` is out of their reach, which is what makes the vault's approval a gate.
- Pipelock is advisory for the app-server: the stack network keeps its direct route, and agents can unset `HTTPS_PROXY` (`pipelock/README.md`, Rules). It is a boundary only for a container whose sole way out it is.
- Overlays restrict and never redefine upstream services; the two written exceptions are `pipelock-proxy.yml` and `doc-tools.yml` (CONTRIBUTING rule 3).
- `lolloctl mcp` owns three kinds of entries in the MCP list (`lib/README.md`), and the BFF has four bypasses, each with a written reason (CONTRIBUTING rule 4).
- Lettuce's MCP bridge gives a tool call 120 s; `letta mcp call` reads arguments from stdin with `--args-file -`, which is how `vault-run` and `vault-fill` pipe values into a call (`docs/vault.md`, "Placeholders").

## Goals / Non-Goals

**Goals:**
- Every protection the docs claim is enforced outside the agent: in the engine's network, in `lollo-browser`'s code, or on the person's page.
- What hostile pages can compromise (the engine) holds nothing at rest and controls nothing.
- No change to upstream services, the vault or pipelock's configuration.

**Non-Goals:**
- Hiding automation from anti-bot systems (stealth patches, fingerprint spoofing, residential proxies). Challenges are handed to the person.
- Downloads and uploads. Pages can't save files and agents can't send any; a later change can add a quarantined download folder.
- Agent payment schemes (Visa Intelligent Commerce, Mastercard Agent Pay, AP2, ACP): none is mature enough to build on.
- Separating agents of one stack from each other: they share one app-server as root, so a per-agent gate would claim more than it can enforce.
- Stopping prompt injection. The design limits what a misled agent can do in the browser; it does not make the agent unmisleadable.

## Decisions

### 1. Our own MCP server on the Playwright library, not Playwright MCP
`lollo-browser` is Python (like the vault and News Radar, with `lollo-common` for the page) and uses Playwright for Python, pinned. Its snapshots use `page.aria_snapshot(mode="ai")`, which labels elements `[ref=e2]`, and `page.get_by_ref()` to act on them; both are public API in 1.65 (checked in the 1.65.2 Python wheel and 1.65.1 `playwright-core` types).

Alternatives:
- **Playwright MCP (`@playwright/mcp`, 0.0.86)** behind a filtering proxy. Its README says it is "not a security boundary"; its core tools include `browser_evaluate` and `browser_run_code_unsafe` ("RCE-equivalent"); since 0.0.82 it exposes tools a page registers (WebMCP) by default; it releases every one to four weeks, so a proxy would have to keep blocking whatever is new; and its `--secrets` is "a convenience and not a security feature". Masking would mean parsing its text output after the fact.
- **Chrome DevTools MCP**: built for debugging, centred on script evaluation, telemetry on by default.
- **browser-use, Stagehand, Skyvern**: each runs a model inside the tool (an agent inside the agent), against the repo's "no model in the service" stance; Skyvern is AGPL.
- **Steel Browser**: closest self-hosted fit (sessions, viewer), but in public beta, and its tools would still need our masking and gates.
- **Browserless, Notte**: SSPL.

### 2. Two containers: the engine and the service
`lollo-browser-engine` runs Xvnc (TigerVNC: X server and VNC in one process) and Chromium, launched once by a small fixed launcher with Playwright's `launchServer`, exposing a WebSocket endpoint at a path made from the stack's engine secret. `lollo-browser` connects with `chromium.connect()`, creates and destroys contexts, serves `/mcp` and `/browser/`, relays the VNC stream to the page, and owns `browser-data`.

```
 app-server --(default net, NO_PROXY)--> lollo-browser :8000  /mcp, /browser/  [browser-data]
 cloudflared --(default net)-----------> lollo-browser         ^ Access JWT on /browser/
                                             |
                                (net "browser", internal: engine, lollo-browser, lollo-pipelock)
                                             | Playwright WS (secret path), VNC (secret)
                                             v
                                    lollo-browser-engine: Xvnc + Chromium (sandbox on)
                                             | every request, loopback included
                                             v
                                    lollo-pipelock --(refuses private)--> internet
```

Why: a renderer exploit plus a sandbox escape (Chrome has several exploited zero-days a year) then owns a container with no data at rest and no reach into the stack: not the push keys, the settings ("may finish checkout"), the audit log or the saved sign-ins of sites not loaded. It also keeps the app-server off the engine's network, so no third overlay exception is needed and the engine cannot reach the BFF's ports even outside Chromium's proxy.

Alternatives: one container, as in the issue (simpler; a full compromise gets everything, and the app-server must join the internal network, which is a new overlay exception and gives the engine a route to it); a relay container in front of a single browser container (same exception avoided, but still mixes control and hostile content).

`lollo-browser` refuses any request whose peer address is on the `browser` network, so a compromised engine cannot call `/mcp` or the page.

Verified across two containers (Playwright 1.65: `playwright-core` 1.65.1 in the engine, Python `playwright` 1.65.2, which bundles driver 1.65.1; the server checks major.minor and refuses a mismatch with 428): connection, `aria_snapshot(mode="ai")` refs and `get_by_ref()`, isolated contexts, `storage_state(indexed_db=True)` filtered and passed back, `screenshot(mask=...)`, and a `disconnected` event when Chromium or the engine dies. The client cannot change launch options (an `x-playwright-launch-options` header is ignored), but **a context's own `proxy` option overrides the browser's, and `bypass: "*"` makes the engine connect directly**, so only the network keeps the engine on pipelock; `lollo-browser` never sets a context proxy, and its tests assert it. The client can also open a browser-level CDP session; that stays inside `lollo-browser` and is never exposed. A default-mode snapshot invalidates earlier refs and an unknown ref waits until its timeout, so `lollo-browser` takes only ai-mode snapshots and acts with short timeouts. A plain HTTP GET to the engine's port answers `200 Running` without the secret; only the WebSocket path needs it, which is why the port is on the internal network only. The headed window shows its tab strip and address bar on the X display without a window manager.

### Interfaces between the parts
- **Engine** (`browser/engine/`, service `lollo-browser-engine`, image `${LOLLO_IMAGE_BROWSER_ENGINE:-lollo-browser-engine:local}`, uid 10003): `node:22-bookworm-slim` with `playwright-core` 1.65.1 and its Chromium, TigerVNC's `Xvnc` on display `:99` (1280x900x24, `-nolisten tcp`), the launcher `launchServer({headless: false, chromiumSandbox: true, proxy: {server: $BROWSER_PROXY, bypass: "<-loopback>"}, args: ["--disable-quic", "--window-position=0,0", "--window-size=1280,900"], host: "0.0.0.0", port: 3000, wsPath: "/" + $BROWSER_ENGINE_SECRET})`, VNC on port 5900 with VNC authentication (password: the first 8 characters of `BROWSER_ENGINE_SECRET`). Environment: `BROWSER_ENGINE_SECRET`, `BROWSER_PROXY` (`http://lollo-pipelock:8888`). The launcher exits 3 after logging `lollo-browser-engine: Chromium's sandbox failed` when the sandbox cannot start, and exits 1 when Chromium exits. A healthcheck passes once the WebSocket endpoint accepts. `/tmp` is a tmpfs, so no stale X lock survives a restart.
- **Service** (`browser/service/`, service `lollo-browser`, image `${LOLLO_IMAGE_BROWSER:-lollo-browser:local}`, uid 10004): `python:3.14-slim-bookworm` (`lollo-common` needs 3.14; Bookworm for `playwright install --with-deps` in the test stage) plus `libatomic1` (the bundled Node driver needs it), `playwright==1.65.2` without browsers, `lollo-common`. Port 8000: `/mcp` and `/browser/`. Environment: `BROWSER_ENGINE_URL` (`ws://lollo-browser-engine:3000`), `BROWSER_ENGINE_SECRET`, `BROWSER_VNC` (`lollo-browser-engine:5900`), `BROWSER_VAPID_PUBLIC_KEY`, `BROWSER_VAPID_PRIVATE_KEY`, `PUSH_VAPID_CONTACT_EMAIL`, the lollo page variables (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD: ${CF_LOLLO_AUD}`, `CF_ACCESS_ISSUER`, `ALLOWED_USERS`, `PUBLIC_ORIGIN: ${LOLLO_ORIGIN}`), `TZ`. Data in `/data` on `browser-data`. Its tests run against a Chromium launched locally in the image's `test` stage, through the same interface the engine connection gives.
- **Compose** (`compose/browser.yml`): network `browser` with `internal: true`; the engine on `browser` only, with `security_opt: ["no-new-privileges:true", "seccomp=${LOLLO_HOME}/browser/engine/seccomp.json"]`; `lollo-browser` on `default` and `browser`; `lollo-pipelock` gains `browser`. `BROWSER_ENGINE_SECRET` and `BROWSER_VAPID_*` live in `stack.env`, generated once per stack.
- **Release** (`release/images`): `browser  browser/service  lollo-common  lollo-browser` and `browser-engine  browser/engine  -  lollo-browser-engine`.

### 3. Chromium's sandbox on, with our own seccomp profile
The engine runs as a non-root user with `chromiumSandbox: true`. Docker's default seccomp profile denies `clone`/`unshare` with namespace flags without `CAP_SYS_ADMIN`, and allows `chroot` only with `CAP_SYS_CHROOT`; Playwright's published profile allows the namespace calls but still gates `chroot`, so it fails with every capability dropped ("Zygote ... exited prematurely"). Ours, `browser/engine/seccomp.json`, is Docker's current default plus `clone`, `clone3`, `unshare` and `setns` with namespace flags and `chroot`. Inside its own user namespace Chromium then has the capabilities its sandbox needs, so `cap_drop: [ALL]` and `no-new-privileges` stay, `check_hardened` is unchanged, and the setuid helper (which `no-new-privileges` breaks) is not needed.

Verified on Ubuntu 24.04 (kernel 6.8, `apparmor_restrict_unprivileged_userns=1`, docker-default AppArmor) with Chromium 159 from Playwright 1.65.1, uid 1001, read-only root, `cap_drop: ALL`, `no-new-privileges`: `chrome://sandbox` reports namespace, PID and network namespaces and seccomp-BPF all on ("adequately sandboxed"); renderers run in their own user namespace with `NoNewPrivs=1`, `Seccomp=2`, `CapEff=0`, headless and headed under Xvfb. With Docker's built-in profile `unshare(CLONE_NEWUSER)` gets `EPERM`; with Playwright's profile `chroot` fails and the zygote exits. The profile is `moby/profiles` `seccomp/default.json` at commit `9ee3204f` plus an unconditional allow of `clone`, `clone3`, `setns`, `unshare` and `chroot`, minus the one rule returning `ENOSYS` for `clone3` without `CAP_SYS_ADMIN` (an errno rule wins over an allow).

Host prerequisites: `user.max_user_namespaces` above 0 and, on Debian kernels, `kernel.unprivileged_userns_clone` not 0. On Ubuntu 23.10 and later the engine must keep docker-default AppArmor: with `apparmor=unconfined`, the userns restriction strips capabilities inside the new namespace and the sandbox fails, so `check_browser` refuses `apparmor=unconfined` too. `up` checks by starting the engine and refuses, pointing at the guide, if the sandbox fails; it never retries without it.

Alternatives: `--no-sandbox` because "the container is the sandbox" (the issue's position; Puppeteer calls it "strongly discouraged", Playwright's Docker docs say its image is not for untrusted sites without a non-root user and a seccomp profile, and a renderer exploit would own the engine directly); `cap_add: [SYS_CHROOT]` (works, but adds a `check_hardened` exception); gVisor (`runsc`) (stronger, but an install requirement, and Chromium's sandbox under it is unverified).

### 4. The network is the boundary; Chromium's flags are belt and braces
The engine's only network is `browser` (`internal: true`), holding the engine, `lollo-browser` and `lollo-pipelock`; `compose/browser.yml` adds that network to `lollo-pipelock`. Chromium is launched with `--proxy-server=http://lollo-pipelock:8888`, `--proxy-bypass-list=<-loopback>` (since M72 Chromium otherwise sends loopback and link-local addresses direct, which would let a page reach the engine's own ports), `--disable-quic`, and managed policies in `/etc/opt/chrome_for_testing/policies/managed/` (the only path Playwright's Chromium build reads; `/etc/chromium/...` and `/etc/opt/chrome/...` are ignored, verified on arm64): `WebRtcIPHandling=disable_non_proxied_udp` (not `WebRtcIPHandlingPolicy`, which is unknown), `DnsOverHttpsMode=off`, `PasswordManagerEnabled=false`, `AutofillAddressEnabled=false`, `AutofillCreditCardEnabled=false`, `ExtensionInstallBlocklist=*`, `DownloadRestrictions=3`, `URLBlocklist` for `file://*`, `devtools://*`, `view-source:*` and `javascript:*` (verified: `file://` gives `ERR_BLOCKED_BY_ADMINISTRATOR`). `DeveloperToolsAvailability=2` breaks Playwright's own connection, so it is left unset; agents have no tool that reaches developer tools. Playwright's download handling overrides `DownloadRestrictions`, so every context is created with `accept_downloads=False`, which makes a download fail. The engine's tests read `chrome://policy` and assert each policy shows as applied, on both architectures.

Pipelock resolves names for the engine (the browser sends host names to the proxy), so rebinding protection depends on pipelock connecting to the address it checked. It does (verified on v3.8.0, commit 556048c1): `ssrfSafeDialContext` (`internal/proxy/proxy.go`) resolves at dial time, refuses if any address is internal, and dials the checked IPs without resolving again, on plain HTTP and CONNECT alike; a public-then-private answer is refused as `ssrf_dns_rebind`. Hex, octal, decimal and short IPv4 forms, IPv4-mapped and NAT64/6to4 IPv6, `localhost` and cloud metadata addresses are all refused, and the core private ranges cannot be configured away. Chromium tunnels `ws://` and `wss://` through CONNECT behind an HTTP proxy, which uses the same dialer (pipelock refuses `ws://` as a plain proxy request). The browser's stack must never get pipelock `trusted_domains`, which skip the internal-address check; `check_browser` cannot see pipelock's config, so `pipelock/README.md`'s Rules say it.

`lollo-browser` goes into `NO_PROXY` and into the names `check_pipelock` requires there, since vault values pass from the app-server to it.

### 5. Tasks with explicit ids, contexts per task, kept sign-ins as per-site storage state
- `start_task(purpose)` returns an id; every other tool takes it. This makes the issue's open question (does `letta mcp call` keep an MCP session?) irrelevant. Tasks end on `end_task`, an idle timeout, a maximum lifetime, "end task" on the page, or a restart (contexts are in memory).
- Each task is one `browser.new_context()` (Playwright's isolated, non-persistent context). No `--user-data-dir`, so no Chromium profile, password store or card store exists. Playwright's own launch adds `--password-store=basic`, which on Linux means profile secrets are "encrypted" with the fixed key "peanuts"; with no profile there is nothing to protect that way.
- Kept sign-ins: at `end_task`, `lollo-browser` calls `context.storage_state(indexed_db=True)`, keeps only cookies whose domain and origins whose host fall under a kept site's registrable domain (Public Suffix List), and writes one file per site on `browser-data`. `start_task` merges the kept sites' files into the new context's `storage_state`. Session storage is not kept (Playwright doesn't capture it; sites rarely rely on it across visits).
- Settings and kept sign-ins are files on `browser-data` owned by `lollo-browser`, changed only by the page's POST handlers behind the Access JWT and form token. No MCP tool writes them.

Alternative: one persistent profile (the issue's design). Rejected with the person: every task by any agent would inherit every sign-in with no vault step, stored effectively in plaintext.

### 6. Masking secrets in our code
Playwright's snapshot writes `element.value` for every input but checkboxes, radios and files, passwords included (confirmed in its `ariaSnapshot.ts`; a Cursor user hit it in October 2026). `lollo-browser` therefore:
- records, per tab, the refs and frames of fields typed with `type_secret` or `type_totp`;
- after each snapshot, finds every password-type input, every field whose `autocomplete` is a card number, expiry, security code, one-time code or password, every field drawn with `-webkit-text-security`, and those recorded, in each frame, and rewrites their lines to `[masked]`, parsing Playwright's quoted line form too and masking any line with a ref it can't parse;
- scrubs, as a second line, every value of 6 characters or more (like `vault-run`) it typed through a secret tool, in any form (plain, with spaces or dashes) from every result, error and log line; shorter values (an expiry month, a security code) are left to field masking, since scrubbing them breaks ids and refs, and dict keys and the service's ids are never scrubbed;
- takes screenshots with Playwright's `mask` over the same fields;
- never returns console messages, network logs or page errors raw.

The acceptance suite types a known value through each secret path on hostile local pages (a page copying the value into visible text, into the title, into an error) and asserts it appears in no result or log, and that every screenshot was taken with a mask covering each such field's box. A page that copies a typed secret elsewhere already has it, since it is the page that received it; the suite documents that limit.

### 7. Site check for secrets
`type_secret(task, tab, ref, text, expect_site)` and `type_totp(task, tab, ref, key, expect_site)` type only when the field's frame origin and the tab's top-level origin are both HTTPS and both under `expect_site`'s registrable domain. The skill tells agents to pass a vault login's public `uris`. It stops a confused agent typing into a look-alike page; it is not a boundary (the agent chooses `expect_site`, and can redeem a vault handle itself), and the docs say so. `type_totp` computes the RFC 6238 code from the key in `lollo-browser`.

Card fields often sit in a payment provider's frame, a frame from another site inside the shop's checkout, which would fail this check. `type_secret` (never `type_totp`) makes one exception: the field's frame, and frames just above it, may be on one site of `PAYMENT_FRAME_SITES` in `sites.py` (`stripe.com`, `adyen.com`, `braintreegateway.com`, `checkout.com`, `mollie.com`, `squarecdn.com`, `paypal.com`, the domains each provider documents for its card frames' `frame-src`), when everything above them, the top-level page included, is on `expect_site` over HTTPS. The list is code, not a setting or an argument: an agent-named frame site would let a confused agent type a card into an ad's or a widget's frame, which a fixed list of payment processors rules out. A provider's frame inside another site's frame, another site's frame inside the provider's, and a provider page at the top level are refused. The result and the audit log name the provider. A shop whose provider is not on the list still gets a hand-off; adding one is a code change with a test. The check stays an aid, not a boundary: the agent still chooses `expect_site`.

### 8. Live view: noVNC of the whole window, read-only until take-over
The engine's Xvnc listens on the `browser` network only, with VNC authentication from the engine secret (VNC passwords are capped at 8 characters, so the network membership is the real control). `lollo-browser` relays it as a WebSocket at `/browser/live`, after the Access JWT, the page's own `Origin` and a short-lived single-use ticket the page fetched with its form token. Fetch Metadata (same-origin, `websocket` mode) is checked when the browser sends it; Chrome sends none on a WebSocket handshake (found on the first real run), so it cannot be required there. The noVNC client is vendored and served by `lollo-browser`, with no third-party origin. The relay drops input events until the person presses "take over"; during take-over the tab is marked controlled and agents' actions on it are refused.

Showing the whole X display shows Chromium's address bar, so the person sees the real site before typing or paying. Alternatives: Chromium's screencast (CDP `Page.startScreencast`) shows only the page content; neko streams over WebRTC, which needs UDP that Cloudflare's tunnel does not carry.

### 9. Checkout recognition, hand-offs and notifications
- Before performing an agent's click, or a key that can submit (Enter, Space) on a ref or on the focused element in any frame, `lollo-browser` scores the target: the button's accessible name against a multilingual list ("place order", "buy now", "pay", "bestellen", "zahlungspflichtig bestellen", "commander", "comprar", "acquista"...), the form's fields (card fields, totals), and the page URL (checkout, payment, order). On a site not allowed to finish checkouts, a high score turns the action into a hand-off and returns that to the agent.
- After any form submission or navigation that matches the same signals, whoever caused it (an agent or the person during take-over), it records an audit entry and sends a push.
- `request_handoff(task, tab, summary)` returns a hand-off id; `wait_handoff(task, id)` waits up to `MAX_WAIT_SECONDS` (below the bridge's 120 s), and the skill runs it as a background command the way `using-vault-secrets` waits for an approval, so the agent gets a new turn when it finishes.
- Push uses Web Push with `BROWSER_VAPID_*` keys generated once per stack (like `VAULT_VAPID_*`) and never rotated; subscriptions live on `browser-data`.

Recognition is a guess and stays advisory; the docs name the limits that hold (vault approval, 3-D Secure, card limits).

### 10. Fitting the repository's rules
- **Profile and route:** `lollo-browser`, `PROFILE_ROUTES` row `^/browser(/|$)`, `LABELS`, `compose/landing.yml`'s list, `profile_unmet` (`lollo-browser` needs `lollo-pipelock`).
- **Checks:** `check_browser` (both services hardened, no host path, the engine's only network `browser` and its members, the engine's seccomp file and no `--no-sandbox` in its command or environment, `lollo-browser` mounting only `browser-data`), `browser-data` and `volumes_from` rules whatever the profiles, `check_pipelock` requiring `lollo-browser` in `NO_PROXY`; each with a `test_check_*` test.
- **Seccomp file path:** Compose reads `seccomp=<file>` client-side and sends the profile's JSON to the daemon (verified with Compose 5.7: `parseSecurityOpts` in `pkg/compose/create.go` does `os.ReadFile(p.RelativePath(...))`; a profile that existed only inside a `docker:cli` container worked against the host daemon, and `docker inspect` shows the JSON, not a path). A relative path resolves against the project directory, which lolloctl sets to `upstream/docker`, so the overlay names it `${LOLLO_HOME}/browser/engine/seccomp.json`, like the build contexts. In release mode the file ships in the `lolloctl` image ("a file lolloctl reads at run time goes into the `lolloctl` image"); it is not a host path, so `require_mounted` does not apply.
- **MCP list:** `mcp` owns a fourth kind of entry, `browser` (`http://lollo-browser:8000/mcp`), handled like News Radar's in `mcp-servers.mjs`; `lib/README.md`'s rule and CONTRIBUTING rule 5 change.
- **BFF bypass:** a fifth entry under CONTRIBUTING rule 4, `/browser/`: a person must see and control what agents do with their accounts and money, without going through an agent. It opens no connection to the app-server or the BFF and starts no turn.
- **Pipelock wording:** `pipelock/README.md` and `docs/egress-firewall.md` say it is a boundary for the browser's engine only, because that container has no other route, and stays advisory everywhere else.
- **Releases:** two images, `browser` and `browser-engine`, named by content.
- **Backup:** `browser-data` deliberately absent from `BACKUP_VOLUMES`, with a comment saying why.

### Research (November 2026)
- Prompt injection: Anthropic reports 1.4% attack success for its best browsing setup against an adaptive attacker and calls it "still meaningful risk" (anthropic.com/research/prompt-injection-defenses); OpenAI says it is "unlikely to ever be fully solved" (openai.com/index/hardening-atlas-against-prompt-injection). Documented attacks: hidden page text (Brave on Comet, Aug 2025), text in screenshots (Brave, Oct 2025), URL fragments (HashJack, Cato, Nov 2025), calendar invites driving a logged-in password manager session (Zenity, Mar 2026).
- Tooling: CVE-2025-9611 (Playwright MCP, DNS rebinding, fixed 0.0.40); MCP SDK rebinding CVEs (CVE-2025-66414, CVE-2025-66416). Our endpoint's `Origin` and host check covers that class.
- Purchases: Operator bought eggs without its confirmation step (Feb 2025, incidentdatabase.ai report 5066).
- Anti-bot: a vendor benchmark from a datacenter address passed 2% of checks headless and 50% headed (browser-use.com/posts/stealth-benchmark). Headed is needed, and challenges will still happen.
- Credentials: 1Password with Browserbase, Bitwarden Agent Access and browser-use all let the model pass a placeholder that a trusted layer fills, as `vault-run` does here.

## Risks / Trade-offs

- [Prompt injection drives an agent with root, a shell, the vault and Gmail; page text reaches it through snapshots that skip pipelock's `/fetch` scanning] → content labelled untrusted, signed-out tasks by default, the vault's approval for every credential, the person's per-site settings, audit and push. The guide says plainly that this is not prevented.
- [Checkout recognition misses a page, so a misled agent places an order on a site that is not allowed] → advisory wording, push and audit on every recognised submit, guide recommends virtual or limited cards and not keeping sign-ins where a card is saved.
- [A kept sign-in on a shop with a saved card lets any agent of the stack spend with no vault step] → keeping is the person's choice per site, the guide says it, "forget" is one click.
- [Masking misses a value: a page renders a typed secret back, or a new Playwright version changes the snapshot format] → two layers (field-based masking, value scrub), the acceptance suite on every Playwright bump, Renovate PRs for Playwright not auto-merged.
- [Chromium's sandbox can't start on some hosts (AppArmor, user namespaces off)] → `up` refuses the browser with the host setting to change; never a silent fallback.
- [A sandbox escape in the engine] → no data at rest there, no route but pipelock, `lollo-browser` refuses its requests; it does get the sessions loaded into open tasks.
- [The host's own public address counts as public to pipelock, so a page can reach any port the host listens on publicly, from inside the host, possibly past a cloud firewall that would stop the same request from outside; this exists for agents through pipelock today] → the guide tells admins; adding the host's public addresses to pipelock's `internal:` list per install is a follow-up, not part of this change.
- [VNC authentication is weak (8-character passwords)] → the display listens only on the internal network shared with `lollo-browser` and pipelock.
- [Pipelock load: about a hundred requests per page against 1200 per minute per base domain, shared with the agents' model calls; receipts it never deletes] → measured in the acceptance run; if needed, a per-page cap in `lollo-browser` and a note in operations on `pipelock-data` growth.
- [Engine image size (Chromium, fonts, Xvnc, about 1 GB)] → built and published once per release like the other images.
- [Playwright for Python and the engine's Playwright must be the same version] → one pinned version for both images, bumped together.

## Migration Plan

New, opt-in profile; nothing changes for stacks without it.
1. An operator runs `lolloctl profiles <current>,lollo-pipelock,lollo-browser`; `up` builds or pulls both images, generates `BROWSER_VAPID_*` and `BROWSER_ENGINE_SECRET`, checks the sandbox, routes `/browser/`.
2. `lolloctl mcp all` registers the browser; `lolloctl skills` installs the skill.
3. Rollback: drop `lollo-browser` from the profiles; `up` removes the route, the containers and the MCP entry. `browser-data` stays until `lolloctl remove`, like the other lollo volumes.

## Open Questions

- Exact idle timeout, maximum task lifetime, task and tab limits: start at 15 min, 4 h, 4 tasks and 8 tabs per task, and adjust after use. A hand-off lasts at most as long as its task: an agent waiting on one gets "task ended" when the task reaches its lifetime, which the skill explains.
- Measured pipelock load and receipt growth per hour of browsing.

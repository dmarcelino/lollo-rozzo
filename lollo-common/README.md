# lollo-common

The Python package `lollo_common`, shared by the seven lollo images: `landing` (the landing page), `vault`, `gmail-triggers`, `news-radar`, `code-server` (the code editor's gate), `browser` (the browser's service) and `notifications`. It holds the checks every lollo request goes through (the Access JWT, Fetch Metadata, Origin and CSRF, COOP), the guard of the executor API at `/executor/v1/` and of any other internal API, the request queue's routes, the senders' client of the notifications service and the payload checks it shares with that service, the agent-list rules, text clean-up, the icon encoder and the lollo stylesheet. It is a library, not a service: it has no compose profile, overlay or route of its own. The security model it implements is described in [docs/vault.md](../docs/vault.md), [docs/gmail-triggers.md](../docs/gmail-triggers.md) and [docs/code-server.md](../docs/code-server.md).

![How a lollo request is checked](../docs/diagrams/lollo-request-checks.svg)

An interactive version, [lollo-request-checks.html](../docs/diagrams/lollo-request-checks.html), opens in a browser from a clone of this repo.

## Files
| Path | What it does |
|---|---|
| `pyproject.toml` | The package and its pinned dependencies (`anyio`, `pyjwt[crypto]`, `starlette`), kept in step with the sidecars that install it |
| `.dockerignore` | Keeps caches, build output, this README and the agents' pointer to it (`AGENTS.md`, `CLAUDE.md`) out of the build context |
| `src/lollo_common/auth.py` | `Authenticator` (the `Cf-Access-Jwt-Assertion` JWT: its header must say RS256 and name a key id (`check_header`) before any key lookup, then RS256 against the team's keys, the lollo's audience and issuer, an email in `ALLOWED_USERS`; `csrf_token`, `check_csrf`, `check_origin`), `AccessKeys` (the team's keys by key id, fetched again at most once a minute for an unknown one), `check_fetch` (Fetch Metadata for `PAGE`, `FORM`, `FETCH`), `admit` (both), `COOP` |
| `src/lollo_common/executor.py` | `guard_sources` (an internal API: under its prefix only, no `Origin` or tunnel headers, a bearer token per caller, whose name the handler finds in `request.state.source`), `guard` (the executor API: `guard_sources` at `/executor/v1/` with the executor's token alone), `SOURCE` (the shape of a caller's name), `read_json` and `read_agents` (capped, validated bodies), `AgentAsks` (when a sidecar wants a new agent list), `request_routes` and `RequestQueue` (the requests, results, agents and heartbeat routes of a sidecar whose requests the executor runs), `EXECUTOR_TOKEN`, and `AGENT_ID` and `CONVERSATION_ID`, the shapes of a request's `agent_id` and `conversation`, which must match `AGENT_ID` and `CONVERSATION` in [landing/executor/run.mjs](../landing/executor/run.mjs) |
| `src/lollo_common/notify.py` | `check_payload` and `PayloadError` (a notification is exactly `title`, `body`, `url` and `tag`; the url a path under `/<source>/`, the tag `<source>-...`; every field one line, the title shortened to 100 characters and the body to 400, a url over 512 or a tag over 64 refused), `Notifier` (a sender's client: `send` and `accept` post to `/notify/v1/send` with the sender's `NOTIFY_TOKEN` and return whether the service took it, never raising; `from_env`), `NoNotifier`, and `SEND_PATH`, `NOTIFY_PREFIX`, `DEFAULT_URL` |
| `src/lollo_common/limits.py` | `BodyLimit` (ASGI middleware: a request body over its route's cap gets 413 before a handler reads it; 1 MiB by default, a larger cap or none per path prefix) and `DEFAULT_MAX_BODY` |
| `src/lollo_common/text.py` | `one_line`, `encodable` and `CONTROL`: outside text made safe to store and show |
| `src/lollo_common/icons.py` | `png`: a small PNG encoder; each sidecar draws its own icon with it |
| `src/lollo_common/lollo.py`, `static/lollo.css` | `stylesheet()`: the lollo's look, which every lollo page links to |

Who uses what: the vault uses `admit` with `PAGE` and `FORM`; gmail-triggers and news-radar with `PAGE` and `FORM` plus `request_routes`; the landing page with `PAGE` plus `guard`; the notifications service with `PAGE` and `FETCH` plus `guard_sources` and `check_payload`, and the vault and the browser, its senders, with `Notifier`; the code editor's gate uses `Authenticator` and `COOP` with Sec-Fetch rules of its own. All seven give their Starlette app `BodyLimit` as middleware: the vault with a larger cap for its Bitwarden import, the code editor's gate with none for the editor's own paths.

## How it plugs in
- **Installed from a build context, not PyPI.** Each overlay (`compose/landing.yml`, `vault.yml`, `gmail-triggers.yml`, `news-radar.yml`, `code-server.yml`, `browser.yml`, `notifications.yml`) passes this directory as the build context named `lollo-common` (`additional_contexts`), and each Dockerfile copies it and runs `pip install` on it before its own package. `tests/run-sidecar.sh` and `scripts/code-server-e2e.sh` pass it with `--build-context`. It is never a pip dependency by name.
- **The whole directory is copied into seven images.** Any file here changes that layer and rebuilds all seven, so `README.md`, `AGENTS.md` and `CLAUDE.md` are in `.dockerignore`: editing these docs rebuilds nothing.
- **Settings it expects:** an object with `team_domain`, `audience`, `issuer`, `allowed_users` and `public_origin` (`AccessSettings`). The overlays fill them from `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` (set from `CF_LOLLO_AUD`), `CF_ACCESS_ISSUER`, `ALLOWED_USERS` and `PUBLIC_ORIGIN` (set from `LOLLO_ORIGIN`); `check_lollo_audience` refuses a lollo service with Lettuce's audience or another origin. The executor guard takes `LOLLO_EXECUTOR_TOKEN`, which `check_stack` requires to be 64 lowercase hex characters. `Notifier.from_env` takes the sender's `NOTIFY_TOKEN` (64 lowercase hex characters, or it refuses to start) and, optionally, `NOTIFY_URL` (the service's send URL, `DEFAULT_URL` otherwise). Every `check_stack` rule: [CONTRIBUTING.md](../CONTRIBUTING.md#what-check_stack-enforces).

## Rules

### The lollo pages refuse same-origin script
The code editor shares the lollo host's origin with the vault and Gmail triggers, and code-server serves webviews from that origin. So a page on the lollo host must not trust a request just because it comes from its own origin. Every lollo page checks Fetch Metadata (`check_fetch`):
- pages load only as top-level documents, from any site (for the Access re-login redirect);
- form posts come only from the same origin;
- the notifications page's JSON calls come only from its own script.

A refusal renders without the person's email, since pages carrying it carry the form token. Stylesheets, scripts, icons and the manifest are assets (`asset=True`) and stay unchecked. The landing page has no script at all (its CSP allows none) and touches no other sidecar. Every lollo response sets COOP same-origin; the code editor's gate applies `Sec-Fetch-*` rules of its own ([code-server/README.md](../code-server/README.md#rules)).

The lollo host keeps the lollo pages off Lettuce's origin; these checks guard the pages from the code editor on the lollo's own origin.

### Bodies are capped before they are read
Any container on the stack network reaches the lollo services without passing Access, and a handler that reads a body holds all of it in memory: one large post to `/redeem` could restart the vault, which locks it. Every lollo app gives its Starlette app `BodyLimit` as middleware, so it runs before routing and before a mounted MCP app: a declared length over the cap gets 413 without a read, a streamed body gets 413 once it passes the cap, and a handler that catches the broken stream still answers 413. The default is 1 MiB, far above any form, MCP call or internal request. A route that needs more names its own cap, as the vault's import does; the code editor's gate leaves the editor's paths uncapped, because it signs the person in before it touches a body and then streams it. Tests: `landing/tests/test_limits.py`, and a test per service that a 2 MiB post gets 413.

### A token's header is checked before any key lookup
Access signs with RS256 and names its key. `Authenticator.email` refuses any other algorithm, and a missing or unprintable key id, as an invalid sign-in before it looks a key up, so a made-up token costs nothing. `AccessKeys` caches the team's keys by key id and fetches the key set again for an unknown one at most once every 60 seconds: a burst of tokens with invented key ids costs one fetch, not one each, and an unreachable key set still fails closed (503): until a fetch succeeds every sign-in gets that 503, never an invalid sign-in, and the next fetch may run 5 seconds after a failed one. Tests: `landing/tests/test_access_keys.py`.

### The executor API is not for browsers or the internet
`guard` admits a request to `/executor/v1/` only with the bearer token, no `Origin`, and none of Cloudflare's `cf-access-jwt-assertion`, `cf-connecting-ip` or `cf-ray` headers. `/executor/v1/` is also outside every tunnelled prefix. The token keeps out the other containers, not agents, who can read it ([landing/executor/README.md](../landing/executor/README.md#trust)).

### The send API is for senders only, and each says only what is its own
The notifications service puts `/notify/v1/send` behind `guard_sources`, with the same refusals as the executor API and a token per sender: the vault's and the browser's, each held only by that sender (as `NOTIFY_TOKEN`) and the service, never by the app-server, so agents cannot read them as they read the executor's. The handler learns the sender from its token, never from the payload. `check_payload` then holds the sender to its own: a link under `/<source>/` (no scheme, host, backslash, dot segment, encoded or not, or control character) and a tag starting with `<source>-`, and nothing but a title, a body, a link and a tag, so a notification has no action to carry. The service runs the same check on what it receives, and `Notifier` before it sends, so a sender's own tests fail first. `Notifier` logs the source, the tag and the outcome, never a title or a body, which may name a secret's labels; it never follows a redirect or goes through a proxy, which would carry the token elsewhere.

## Build and test
The package has no tests or image of its own: the sidecars' tests cover it. Before changing it, run all seven:
```bash
tests/run-sidecar.sh landing
tests/run-sidecar.sh vault
tests/run-sidecar.sh gmail-triggers
tests/run-sidecar.sh news-radar
tests/run-sidecar.sh code-server
tests/run-sidecar.sh browser
tests/run-sidecar.sh notifications
```

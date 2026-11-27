# Design

## Context

What the repository and GitHub hold today (read with `gh api` on 2026-11-26):

**GitHub settings**
- The `Main branch` ruleset (id 27127070) forbids deletion and force pushes. It requires 10 named checks: `shellcheck`, `cli`, six `sidecar (...)` and `pipelock`, `docs`. It does not require `ci-passed`, `sidecar (browser)`, `sidecar (notifications)`, `browser-acceptance` or `doc-tools-arm64`.
  - The admin role (`RepositoryRole` 5) bypasses it always. The `release` skill relies on that bypass to push straight to `main`.
- There is no tag ruleset, nothing protects `hotfix/*`, and immutable releases are off.
- The maintainer is the only collaborator. The default workflow token is read-only.

**Repository code**
- `scripts/check-ci-gate.sh` (run by `tests/run.sh test_ci_gate_`) already checks that `ci-passed` needs every job. CONTRIBUTING.md assumes the ruleset requires `ci-passed` alone, which the live ruleset contradicts.
- `publish.yml` creates the release with `gh release create "$TAG" ... dist/lolloctl dist/RELEASE dist/SHA256SUMS`. gh's own help (2.103) says that with assets it "creates a draft release, uploads each asset, and then publishes the release", and that immutability applies on publish.
- `compose/hardening.yml` drops `NET_RAW` on the app-server, the BFF and the channel gateway only.
  - `check_stack`'s rule (`lib/common.sh`, the `sniffing` query) covers the app-server and `network_mode: service:app-server` only.
  - Lollo sidecars drop `ALL` and are held to it by `check_isolated` and their own checks.
- `.github/renovate.json5` lets Lettuce patch tags that keep letta-code's version automerge after `minimumReleaseAge: '1 day'`.
- **`lollo-common`:**
  - `executor.read_json` caps the bodies of the internal APIs (16 KiB, and 1 MiB for agent lists). Notifications has its own capped reader.
  - The vault's `page(..., max_body=)` checks only a declared `Content-Length`, on its import route.
  - Every other form, `/redeem` and every FastMCP `streamable_http_app` mount reads an unbounded body.
  - `jwks_resolver` uses `PyJWKClient(cache_keys=True, lifespan=3600)`, which refetches the key set on every unknown `kid`.
  - The seven lollo apps build their Starlette app in their own `create_app`. The browser wraps its app in `Guard`.
  - The code editor's gate authenticates (`checked`) before it streams a body to the editor (`request.stream()`).

## Goals / Non-Goals

**Goals:**
- Close the drift between the documented and the live release settings, and make future drift visible.
- One body-cap mechanism in `lollo-common` that every lollo app applies the same way, with no change to what people or agents can send today.

**Non-Goals:**
- Review on `main`: one maintainer, and the admin bypass stays.
- Verifying attestations when an image is reused (SEC-009 part 2), and provenance checks in `self-upgrade` (SEC-018 proper). Both are later roadmap items (S2, L5).
- Turning `check_stack` into an allow-list (S3), or pinning Lettuce by commit and digest.
- Splitting the stack network (L1). Body caps limit memory, not who may call.

## Decisions

### 1. The ruleset requires `ci-passed` alone
Requiring the one gate job, rather than listing every job, means a new CI job is gated as soon as it is in `ci-passed`'s `needs`, which `test_ci_gate_` already enforces. Rejected: adding the four missing names to the list. It drifts again with the next job, and is exactly how today's gap appeared.

### 2. Settings recorded as JSON, checked by a script
- `release/github-settings.json` holds the expected shape of each ruleset: its name, target, include patterns, rule types, required check contexts and bypass actors. It also holds the immutable-releases flag.
- `scripts/check-github-settings.sh [owner/repo]` reads the live settings with `gh api` and compares the fields it records, ignoring ids and timestamps. It prints each difference and exits 1 on any.
- It needs the network and a token, so CI does not run it, which keeps CI's token read-only. The `release` skill runs it before tagging, and it is run by hand after any settings change.
- `tests/run.sh` tests the script against the `gh` stub: a match, a missing required check, a missing tag ruleset and immutability off.
- Rejected: Terraform or a settings app. That is too heavy for one repository, and it needs an admin token stored somewhere.

### 3. Applying the settings is an operator step, by `gh api`
The tasks give the exact `gh api` calls: a `PUT` to the existing ruleset, a `POST` for each of the two new rulesets, and a `PUT` to `immutable-releases`. They run only after the maintainer confirms, and the check script then confirms the result.
- **Tag ruleset:** target `tag`, include `refs/tags/v*`, rules `creation`, `update` and `deletion`, bypass the admin role, so the `release` skill's `git push origin vX.Y.Z` still works.
- **Hotfix ruleset:** target `branch`, include `refs/heads/hotfix/*`, the same rules as `main`, bypass the admin role.

### 4. `publish.yml` keeps `gh release create` with its assets
gh already drafts, uploads, then publishes. A `test_publish_` case pins the form: one `gh release create` that names all three assets, with no `--draft` and no later `gh release upload` or `edit`. Rejected: an explicit draft, then upload, then publish in three commands. It is the same API sequence with more to keep in step.
- Consequence: a broken RC can no longer be re-published under the same tag. `release/README.md` says to cut the next `-rc.N`, which is already the practice.

### 5. NET_RAW: extend the existing rule, keep it profile-aware
The `sniffing` query in `check_stack` gains `cloudflared`, `searxng`, `ddg-mcp` and `google-mcp` by name, and keeps the namespace members. Services that are not rendered (profile off) are absent from the rendered JSON, so the rule needs no profile logic. The message keeps its current form and names the services. `compose/hardening.yml`'s header explains the four.

### 6. Renovate: one rule for every Lettuce update
A single `packageRules` entry with `matchDepNames: ['dmarchevsky/lettuce']` and `automerge: false`. Its `prBodyNotes` asks for upstream's `docker/compose.yml` diff to be read and the checklist walked.
- The two existing Lettuce rules collapse into it: the minor/major one, and the letta-code one, whose note becomes a sentence of the new note.
- The `separateMinorPatch` and one-day `minimumReleaseAge` rules stay: still useful for PR shape and soak.

### 7. Body cap: an ASGI middleware in `lollo-common`
- `lollo_common.limits.BodyLimit(app, default=1 MiB, routes={path_prefix: cap | None})` wraps each app's outermost ASGI callable, so it runs before routing, FastMCP and the browser's `Guard`.
- **For `http` scopes:**
  - A declared `Content-Length` above the cap gets an immediate 413, without reading.
  - Otherwise it wraps `receive`, counts body bytes, and answers 413 when they pass the cap, if no response has started. If one has, it raises so the server drops the connection.
  - `websocket` and `lifespan` scopes pass through untouched.
- **Route overrides:** the longest matching path prefix wins.
  - The vault's import route: `MAX_IMPORT_BYTES + 64 KiB`. The existing friendly 413 page in `page(max_body=)` stays as the first check, for its message.
  - The gate's editor prefix: `None`, meaning uncapped. Safe because the gate authenticates before it touches the body, and it streams.
- Rejected:
  - A cap in each handler. Some 30 call sites, and FastMCP's handler is not ours.
  - A server option. uvicorn has no request-body limit.

### 8. Key lookup: check the header, throttle the refetch
`jwks_resolver` becomes a small class:
- `jwt.get_unverified_header` must give `alg == "RS256"` and a `kid` of at most 128 printable ASCII characters. Otherwise it is an invalid sign-in (403), with no fetch.
- Keys are cached by `kid`. A miss refetches only if the last fetch was at least 60 seconds ago, using `PyJWKClient.get_jwk_set(refresh=True)` under a lock. Otherwise the miss is an invalid sign-in. A failed fetch raises 503, and so does every miss until a fetch succeeds; the next fetch may run 5 seconds after a failed one, not 60, so a sidecar that starts during a network blip is not locked out for a minute.
- A fetch failure stays a 503, failing closed.
- The clock is injectable for tests.
- Rejected: shortening PyJWKClient's lifespan. It does not stop the per-miss refetch.

## Risks / Trade-offs

- **[A missing `ci-passed` blocks every merge, Renovate's included]** → Required checks match by job name, and `ci-passed` runs on every pull request with `if: always()`. The check script is run right after the change.
- **[Admin bypass makes the rulesets moot against a stolen owner token]** → Accepted. They stop workflow tokens, the Renovate app and mistakes. The owner-token path is cut by the toolbox change (C). Immutable releases hold even against admins once a release is published.
- **[Immutable releases are irreversible per release]** → An RC mistake costs an RC number. Documented in `release/README.md`.
- **[A cap refuses a legitimate large MCP call]** → 1 MiB is far above any tool's input today: specs, fills and agent lists are a few KiB. The 413 names the cap, and a route can raise it.
- **[A cap that trips mid-stream after a response started]** → The connection drops instead of a 413. Only possible for handlers that answer before reading their whole body, which no lollo handler does.
- **[cloudflared without NET_RAW]** → It loses only its ICMP proxy (WARP private-network ping), which tunnels with public hostnames do not use, and may log that it is off. Checked on a live stack.
- **[Rebuilding seven images locks the vault once]** → The release notes already say so when the vault's image changes. Ship it with the next release that changes the vault anyway, if one is close.
- **[Lettuce patch PRs now wait for a person]** → Intended. The PR note says what to read, and the one-day soak stays.

## Migration Plan

1. **Merge the code:** compose, check, Renovate, `lollo-common`, script, docs. `lolloctl up` recreates the four helpers, without NET_RAW, and the seven lollo services, with new images. The vault locks once.
2. **Apply the GitHub settings** with the confirmed `gh api` calls, then run `scripts/check-github-settings.sh` until it passes.
3. **Rollback:** remove the four `cap_drop` lines and the rule (one commit), restore the old rule list with `gh api`, and revert the Renovate rule. Immutable releases can be turned off for future releases, but releases published while it was on stay immutable.

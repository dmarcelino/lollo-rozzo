# Proposal

## Why

The security audit found four cheap gaps around how code reaches a host and how the stack's helpers behave on its network:

- **Release settings (SEC-009, SEC-018).** The `Main branch` ruleset requires 10 named CI checks, not `ci-passed`, so `sidecar (browser)`, `sidecar (notifications)`, `browser-acceptance` and `doc-tools-arm64` do not gate a merge. Renovate's auto-merge (GitHub's own) can merge without them, although CONTRIBUTING.md says it cannot.
  - Immutable releases are off, so a published release's assets can be replaced.
  - No tag ruleset protects `v*`, and nothing protects `hotfix/*`, which `publish.yml` accepts as a release source.
- **Upstream helpers keep `NET_RAW` (SEC-007).** `cloudflared`, `searxng`, `ddg-mcp` and `google-mcp` keep Docker's default `NET_RAW` on the network where Access JWTs, the vault passphrase and redeemed values travel in plain HTTP.
- **Lettuce updates merge on their own (SEC-008).** A Lettuce patch tag that keeps letta-code's version merges after one day with nobody reading it. Lettuce defines the isolation the whole product relies on.
- **Lollo services can be knocked over without a credential (SEC-025).**
  - Any container on the stack network can post an unbounded body to a lollo service, for example the vault's `/redeem`. An out-of-memory restart locks the vault.
  - A token with an unknown key id makes the service fetch the Access key set again, on every request.

Each fix is a few lines or a settings change, none of them changes what people or agents can do, and together they close the cheap half of chain 3 ("from one stack to every install") and chain 6 ("upstream tag to every host").

## What Changes

- **GitHub settings (operator-applied, recorded in the repository):**
  - Turn on immutable releases.
  - The `Main branch` ruleset requires `ci-passed` only, in place of the 10 named checks.
  - A tag ruleset on `v*` forbids deleting or moving a tag, and restricts creating one to the admin role.
  - A `hotfix/*` ruleset matches `main`'s.
  - Review on `main` stays off: the repository has one maintainer, and the admin bypass stays.
  - The expected settings are written down, and a script compares the live ones with them so drift shows.
- **`publish.yml`:** keeps `gh release create` with its assets, which creates a draft, uploads and then publishes, so immutability applies to a complete release. A test pins that form.
- **`NET_RAW` dropped** on `cloudflared`, `searxng`, `ddg-mcp` and `google-mcp` (`compose/hardening.yml`). `lolloctl check` refuses a stack where any of them, or any container in the app-server's namespace, keeps it.
- **Renovate never merges a Lettuce update on its own,** whatever the update type. Every Lettuce PR names the upgrade checklist. CONTRIBUTING.md and the `CLAUDE.md` directive change with it.
- **`lollo-common` body cap.** A size cap on request bodies that every lollo service applies to its HTTP routes, MCP endpoints included. It refuses an oversized body with 413 before the handler reads it, and a route can declare a larger cap (the vault's Bitwarden import).
- **`lollo-common` key lookup.** It refuses a token that is not `RS256`, or that has no usable key id, before any key lookup, and fetches the Access key set again at most once per interval.

## Capabilities

### New Capabilities
- `supply-chain/release-integrity`: what protects the path from a commit to a published release. Covers the required CI gate, immutable releases, protected release tags and hotfix branches, and the check that the live settings match the recorded ones.
- `supply-chain/dependency-updates`: which dependency updates may merge without a person, starting with Lettuce never doing so.
- `stack/container-hardening`: the capability rules every stack container is held to by `lolloctl check`, starting with `NET_RAW` on the stack network.
- `lollo-services/request-limits`: the bounds a lollo service puts on what an unauthenticated caller can make it spend: request body size and Access key-set fetches.

### Modified Capabilities
None. No existing spec covers these behaviours.

## Impact

- **GitHub repository settings:** the rulesets and immutable releases. These are applied by the operator with `gh api` at apply time, after confirmation.
- `.github/workflows/publish.yml` (unchanged behaviour, pinned by a test), `.github/renovate.json5`, a new `scripts/check-github-settings.sh` and its expected-settings file.
- `compose/hardening.yml`, `lib/common.sh` (`check_stack`), `tests/run.sh`.
- **`lollo-common`:** `auth.py` and a new middleware module. That rebuilds all seven lollo images: landing, the vault, gmail-triggers, news-radar, the code editor's gate, the browser and notifications. A vault image rebuild means every person unlocks once after the upgrade.
- **Docs:** CONTRIBUTING.md ("Dependency updates", "What `check_stack` enforces", "Upgrading `LETTUCE_REF`"), `CLAUDE.md`, `compose/README.md`, `lollo-common/README.md`, `release/README.md`, and ARCHITECTURE.md's hardening sentence.

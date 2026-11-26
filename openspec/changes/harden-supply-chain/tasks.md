# Tasks

## 1. NET_RAW on the upstream helpers

- [x] 1.1 Add `cap_drop: [NET_RAW]` to `cloudflared`, `searxng`, `ddg-mcp` and `google-mcp` in `compose/hardening.yml`, and extend its header comment to explain why. Verify that `lolloctl compose <name> config` on a test stack shows the four with `cap_drop: [NET_RAW]`.
- [x] 1.2 Extend `check_stack`'s NET_RAW rule (`lib/common.sh`, the `sniffing` query) to the four services by name, keeping the namespace members, with a message that names each offender. Verify with a new `test_check_refuses_net_raw_on_the_stack_helpers` in `tests/run.sh` (one case per service, each `bad_rc` with `NET_RAW` and the service named), a case where the search services are absent and `check` passes, and the stub's base `compose-config.json` updated so the existing `test_check_*` tests still pass (`tests/run.sh test_check_`).
- [x] 1.3 Update `compose/README.md` (hardening overlay), CONTRIBUTING.md ("What `check_stack` enforces") and ARCHITECTURE.md's hardening sentence to say every upstream helper on the stack network drops NET_RAW. Verify that `node scripts/check-doc-links.mjs` passes.
- [x] 1.4 Run `shellcheck -x lolloctl lib/*.sh tests/run.sh` and the full `tests/run.sh`, and verify both pass.

## 2. Lettuce updates wait for a person

- [x] 2.1 In `.github/renovate.json5`, replace the two conditional Lettuce `automerge: false` rules with one rule for `dmarchevsky/lettuce` (every update type, `automerge: false`), whose `prBodyNotes` asks to read upstream's `docker/compose.yml` diff and walk "Upgrading `LETTUCE_REF`". Keep `separateMinorPatch` and the one-day `minimumReleaseAge`. Verify that `npx --yes --package "renovate@$(sed -n 's/^ *RENOVATE_VERSION: //p' .github/workflows/renovate.yml)" renovate-config-validator --strict` passes.
- [x] 2.2 Rewrite CONTRIBUTING.md's "Dependency updates" (what merges on its own: never a Lettuce update) and the upgrade checklist's intro, and change `CLAUDE.md`'s Upstream directive to "Renovate never merges a Lettuce update; a person walks the checklist". Verify that `node scripts/check-doc-links.mjs` passes and that `grep -n 'patch tags that keep' CLAUDE.md CONTRIBUTING.md` finds nothing.

## 3. Request limits in lollo-common

- [x] 3.1 Add `lollo_common/limits.py` with `BodyLimit` (1 MiB default, longest-prefix route overrides, `None` meaning uncapped, 413 on a declared or counted overrun, websocket and lifespan untouched). Verify with unit tests run through `tests/run-sidecar.sh landing`: declared overrun with no `receive` call, chunked overrun, under-cap pass-through, override larger and `None`, websocket untouched.
- [x] 3.2 Replace `jwks_resolver` in `auth.py` with a resolver that checks the unverified header (`RS256`, a printable `kid` of at most 128 characters) before any lookup, caches keys by `kid`, and refetches at most once per 60 seconds under a lock (injectable clock), keeping 503 for an unreachable key set. Verify with tests: an `HS256` token makes no fetch and gets 403; 100 unknown `kid`s within a minute cause one fetch; a new `kid` after 60 s is fetched and accepted.
- [x] 3.3 Wrap each app's outermost ASGI callable in `BodyLimit`:
  - landing, Gmail triggers, News Radar and notifications: the default cap.
  - the vault: its import route at `MAX_IMPORT_BYTES + 64 KiB`, with `/redeem` and the MCP mount at the default.
  - the browser: outside `Guard`.
  - the code editor's gate: the editor proxy prefix uncapped, its own routes at the default.

  Verify with one test per service that a 2 MiB post to its MCP or internal endpoint gets 413 and an ordinary request still works, plus a vault test that a 4 MiB import works and a 6 MiB one gets 413. Run all seven: `tests/run-sidecar.sh landing|vault|gmail-triggers|news-radar|code-server|browser|notifications`.
- [x] 3.4 Document `limits.py` and the resolver in `lollo-common/README.md` (Files, Who uses what, and a Rules entry "Bodies are capped before they are read"), and note in `vault/README.md` and `code-server/README.md` their route overrides. Verify that `node scripts/check-doc-links.mjs` passes and that no file other than README/AGENTS/CLAUDE is added to `lollo-common/` for docs.

## 4. Release settings, recorded and checked

- [x] 4.1 Add `release/github-settings.json` recording the expected settings:
  - `Main branch` with `ci-passed` as its only required check, no deletion, no force push, admin bypass.
  - `Release tags` on `refs/tags/v*`, with `creation`, `update` and `deletion` rules and admin bypass.
  - `Hotfix branches` on `refs/heads/hotfix/*`, the same as main.
  - Immutable releases on.

  Verify that it parses with `jq` and names `ci-passed` alone.
- [x] 4.2 Add `scripts/check-github-settings.sh [owner/repo]`, which compares live rulesets and `immutable-releases` (through `gh api`) with the recorded file, prints each difference and exits 1 on any. Verify with `test_github_settings_*` cases in `tests/run.sh` using the `gh` stub (match; required checks are the old ten; no tag ruleset; immutability off), and that it passes `shellcheck`. Add the script to CLAUDE.md's and CONTRIBUTING.md's shellcheck command lines and to `ci.yml`'s shellcheck job.
- [x] 4.3 Add a `test_publish_creates_the_release_with_its_assets_in_one_call` case that `publish.yml` has a single `gh release create` naming `dist/lolloctl dist/RELEASE dist/SHA256SUMS`, without `--draft`, and no `gh release upload`, `edit` or `delete`. Verify with `tests/run.sh test_publish_`.
- [x] 4.4 Have the `release` skill (`.agents/skills/release/SKILL.md`) run `scripts/check-github-settings.sh` before tagging and stop on a difference. Document the rulesets, immutable releases and "a broken RC takes the next number" in `release/README.md`, and correct CONTRIBUTING.md's sentence about the ruleset. Verify with `tests/run.sh test_ci_gate_` and `node scripts/check-doc-links.mjs`.
- [ ] 4.5 With the maintainer's confirmation, apply the settings:
  - `gh api -X PUT repos/<repo>/rulesets/27127070` with `ci-passed` only.
  - `gh api -X POST repos/<repo>/rulesets` for the tag ruleset and for the hotfix ruleset.
  - `gh api -X PUT repos/<repo>/immutable-releases`.

  Verify that `scripts/check-github-settings.sh` exits 0, and that the next pull request shows `ci-passed` as required.

## 5. Integration

- [ ] 5.1 On a test stack, run `lolloctl up` and check that cloudflared still connects, a search through `ddg-mcp` works, the vault unlocks and serves an approval, a Bitwarden import works, and an editor upload works. Then `docker inspect` the four helpers to verify NET_RAW is not in their effective capabilities.
- [ ] 5.2 Cut the next RC with the `release` skill, and verify that its release is published with all three assets and shows as immutable on GitHub.

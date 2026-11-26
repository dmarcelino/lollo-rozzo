# Proposal

## Why

The browser gives agents a real Chromium on the public internet, and the stack's own two hosts are ordinary public hosts to it. A task that holds a Google session can open the person's lollo host, complete the Cloudflare Access sign-in, and then use every person-only control (approve its own vault request, show hidden fields, allow checkouts, edit trigger rules) with a genuine origin (SEC-001). A kept sign-in for the operator's registrable domain then carries that Access cookie into every later task (SEC-031). Together they are the last step of the audit's chain 1, "agent becomes the person", and a small, deterministic fix breaks it.

## What Changes

- `lolloctl` computes one deny list for every stack: the Lettuce host and the lollo host of every stack on this host, plus the Cloudflare Access team domain (`<team>.cloudflareaccess.com`). It passes the list to the stack's pipelock, the browser's engine and `lollo-browser`, the way it already passes `PIPELOCK_DIRECT`: computed by `lolloctl`, never read from `stack.env`.
- Pipelock refuses those hosts, in addition to its shipped blocklist. Pipelock is the engine's only way out, so this is a boundary for the browser. Redirects, frames, service workers, percent-encoded or trailing-dot hosts all end at the same check. The app-server's proxied traffic gets the same refusal, which it never needs.
- Chromium's managed policy blocks the same hosts, as a second layer with a clear error page.
- Kept sign-ins never save or load cookies or storage of a denied host, even when the person keeps its registrable domain.
- The browser's Sites page warns when the person keeps the identity provider's site (`google.com`): such a sign-in reaches every site that offers "Sign in with Google".
- The vault's approval page warns when a login being shared is for the identity provider. It does not refuse: with the Access hosts denied, a Google session can no longer complete the stack's Access sign-in, and agents keep doing ordinary Google work.
- `lolloctl check` refuses a stack with the browser whose pipelock or engine lacks the stack's own hosts and team domain in its deny list.
- Out of scope: the person-presence step-up (SEC-001 part 3, the audit's L2), and the network split.

## Capabilities

### New Capabilities

- `vault-identity-logins`: how the vault presents a request to share a login for the identity provider that signs the person in to their stack.

### Modified Capabilities

- `browser/isolation`: adds that the engine can never reach any stack's Lettuce or lollo host, nor the Access team domain, and that `lolloctl check` holds both layers.
- `browser/sessions`: adds that a kept site never carries a denied host's cookies or storage, and that keeping the identity provider's site shows a warning.

## Impact

- **Code:** `lib/common.sh` (`compose_env`, `check_browser`, a deny-list helper), `compose/pipelock.yml`, `compose/browser.yml`, `pipelock/entrypoint.sh` and `pipelock/pipelock.yaml`, `browser/engine/` (launcher, Dockerfile, policies), `browser/service/` (`sessions.py`, Sites page), `vault/` (approval page).
- **Tests:** `tests/run.sh` (`test_check_*`, `test_compose_env_*`), `tests/run-pipelock.sh`, `tests/run-browser-acceptance.sh`, the browser's and the vault's pytest suites.
- **Docs:** `docs/browser.md`, `docs/egress-firewall.md`, `docs/vault.md`, `browser/README.md`, `pipelock/README.md`, `compose/README.md`, `CONTRIBUTING.md` (the `check_stack` rule), and the `CLAUDE.md` browser directive.
- **Behaviour:** agents can no longer open any stack's own pages, nor any Access application of the same Cloudflare team, in the browser or through the proxy. Adding or removing a stack changes every other stack's deny list at that stack's next `up`, which recreates its pipelock and browser containers (the vault is not touched).

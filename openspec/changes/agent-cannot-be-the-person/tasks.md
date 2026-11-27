# Tasks

## 1. The deny list in lolloctl

- [x] 1.1 Add `deny_hosts` to `lib/common.sh`: every stack directory with a `stack.env` (its `PUBLIC_ORIGIN` host and `LOLLO_HOSTNAME`), then `<CF_TEAM_DOMAIN>.cloudflareaccess.com`, sorted, comma-separated, read with `env_get`/`cfg` only; verify with `test_deny_hosts_*` cases for one stack, two stacks, a stack with no `stack.env` yet, and an unset team domain (dies naming `CF_TEAM_DOMAIN`)
- [x] 1.2 Pass it from `compose_env` as `LOLLO_DENY_HOSTS`, always, beside `PIPELOCK_DIRECT`; verify with a `test_compose_env_` case that the docker stub sees the variable and that a value in `stack.env` is ignored
- [x] 1.3 Document the helper and the variable in `lib/README.md` and the variables list in `compose/README.md`; verify with `node scripts/check-doc-links.mjs`

## 2. Pipelock refuses the hosts

- [x] 2.1 Add `pipelock/shipped-blocklist` with pipelock v3.8.0's six entries, and a marked blocklist block for the entrypoint to fill; verify `pipelock check --config` passes on the rendered file
- [x] 2.2 Make `pipelock/entrypoint.sh` validate `LOLLO_DENY_HOSTS` (host names only, at most 500), write `/tmp/pipelock.yaml` with the shipped list plus the deny list, run `pipelock check` on it, then `exec pipelock run --config /tmp/pipelock.yaml`; with no variable, the shipped list only
- [x] 2.3 Pass `LOLLO_DENY_HOSTS` to `lollo-pipelock` in `compose/pipelock.yml`
- [x] 2.4 Extend `tests/run-pipelock.sh`: the rendered config refuses an exact denied host, upper case and a trailing dot, still refuses a `pastebin.com` subdomain, allows `www.example.com`, refuses a malformed variable at start; and `pipelock/shipped-blocklist` equals the blocklist of `pipelock generate config --preset balanced`; verify by running `tests/run-pipelock.sh`
- [x] 2.5 Update `pipelock/README.md` (files, rules: the deny list and the shipped-list file to check on upgrade) and `docs/egress-firewall.md` (what is refused, the nested-URL side effect); verify with `node scripts/check-doc-links.mjs`

## 3. Chromium policy

- [x] 3.1 In `browser/engine/Dockerfile`, copy `policies.json` to `/opt/engine/policies.json` and make the managed `lollo.json` a symlink to `/tmp/policies/lollo.json`
- [x] 3.2 In `browser/engine/launcher.mjs`, validate `LOLLO_DENY_HOSTS`, write the policy file with `.<host>` entries appended to `URLBlocklist` before `launchServer`, and exit 2 when the variable is missing, malformed or the file can't be written; verify with cases in `browser/engine/tests/run.sh` for a missing and a malformed list, and that the written policy keeps the base settings
- [x] 3.3 Pass `LOLLO_DENY_HOSTS` to `lollo-browser-engine` and `lollo-browser` in `compose/browser.yml`

## 4. The check

- [x] 4.1 Add the rule to `check_browser`: both `lollo-pipelock`'s and `lollo-browser-engine`'s rendered `LOLLO_DENY_HOSTS` contain the stack's two hosts and its team domain; verify with `test_check_browser_needs_deny_hosts` cases (empty value on each service, a missing own host) and `tests/run.sh test_check_`
- [x] 4.2 Add the rule to `CONTRIBUTING.md` ("What `check_stack` enforces") and to `browser/README.md`'s Rules; verify with `node scripts/check-doc-links.mjs`

## 5. Kept sign-ins

- [x] 5.1 Give `Sessions` the deny list from `lollo-browser`'s settings and drop denied hosts' cookies and origins in `save` and `initial_state`; verify with pytest cases: a kept `example.com` saves `shop.example.com` but nothing of `alfred-lollo.example.com`, and a tampered saved file's `alfred.example.com` cookie is not loaded
- [x] 5.2 Add the identity-provider warning and confirm box to the Sites form; verify with pytest page cases: keeping `google.com` without the box shows the warning and saves nothing, with the box it saves, and `shop.test` shows no warning
- [x] 5.3 Update `docs/browser.md` (security model: stack hosts unreachable, the warning, other Access applications of the same team, recreation on `up` after a stack is added); verify with `node scripts/check-doc-links.mjs`

## 6. Vault warning

- [x] 6.1 Mark requests for a login whose `uris` name `google.com` or a subdomain, and show the warning banner in `_decide.html`; verify with pytest cases: a Google login's approval page has the warning, a `shop.test` login's does not, the tool result is identical for both, and "Share once" works on both
- [x] 6.2 Update `docs/vault.md` (approving an identity-provider login); verify with `node scripts/check-doc-links.mjs`

## 7. Integration

- [ ] 7.1 Add cases to `tests/run-browser-acceptance.sh` with a stand-in denied host on the internal network: a direct navigation, a 302 chain, an iframe, a service worker's fetch, a percent-encoded host and a trailing dot, each refused with a block receipt, and an allowed host still loading; verify by running `tests/run-browser-acceptance.sh`
- [x] 7.2 Update the browser line of `CLAUDE.md`'s directives (the engine never reaches any stack's hosts or the Access team domain) and `ARCHITECTURE.md`'s browser row if its wording changes; run `tests/run.sh`, the shellcheck command, `tests/run-sidecar.sh browser`, `tests/run-sidecar.sh vault` and `node scripts/check-doc-links.mjs`, all passing

## Workflow follow-up

- Live check on a throwaway stack with a throwaway Google account: a browser task kept signed in to Google cannot reach the stack's lollo host.
- Release notes: the browser and pipelock are recreated once; agents can no longer open the stack's own pages.
- Archive the change after review.

# Tasks

Read `vault/README.md#rules` and `agent-skills/README.md#rules` before starting (CLAUDE.md). Every check below runs with `tests/run-sidecar.sh vault` (Docker), which mounts `agent-skills/` for the helper tests.

## 1. Vault: spent handles get their own errors

- [x] 1.1 In `vault/src/lollo_vault/service.py`, add the in-memory `_spent` record (id → reason, expiry): `redeem` moves each handle it hands over into it, `expire()` moves handles cleared for a lock into it as `"locked"` and drops entries past their expiry, and every other place that clears `_handles` today clears it too. Verify by reading that no `Handle.env` or value is stored in it.
- [x] 1.2 Make `redeem` answer 410 "already used: an earlier vault-run took this handle, even if its command failed; request the secret again" and 410 "dropped when the vault locked; request the secret again once it is unlocked", and keep 404 for unknown or expired, with "(almost always: already used, or older than N minutes)". Still all or nothing, and the message says the others are still valid. Verify with new `test_service.py` tests: used then used again; locked then used; spent past expiry gives 404; made-up gives 404; `[spent, fresh]` uses up neither; `_spent` is empty after expiry; and a test secret isn't in `repr(service._spent)` or `caplog`.
- [x] 1.3 Update `vault/tests/test_vault_run.py`'s stub `/redeem` so it can answer 410 with the new texts, and assert that `vault-run` prints them and exits 2. Verify with `tests/run-sidecar.sh vault -k "vault_run"`.
- [x] 1.4 Update `vault/README.md` ("What agents get": a spent or dropped handle is remembered by id only, until it would have expired) and its file table if needed. Verify with `node scripts/check-doc-links.mjs`.

## 2. Vault: `get_result(collect=...)` and the `pending` reply

- [x] 2.1 Add `collect: bool = True` to `Service.get_result` and to the MCP tool in `mcp_tools.py`, with a description line. With `collect=False`, a `done` request returns `{"id", "status": "done"}` and leaves `req.values` in place. Verify with `test_service.py` tests: peeking, then collecting, returns the handle once; the default is unchanged; and peeking at a pending request gives the normal pending reply.
- [x] 2.2 Turn `KEEP_WAITING` into a function of the id. Add `wait_command` (`python3 /root/.letta/skills/using-vault-secrets/vault-wait <id>`, built from `SKILL_DIR`) to both pending replies, and a message that gives the command, says "one background command; list several ids in one command", and keeps the `get_result` / `wait_seconds` fallback for agents without a shell. Verify by extending `test_a_pending_answer_tells_the_agent_to_keep_waiting`: `wait_command` is exact and in the message, the fallback is still in it, and there is no `wait_command` on done, denied, expired or unknown.
- [x] 2.3 Extend `_deliver`'s message: MCP arguments go through a `vault-fill` JSON template piped into the wrapper with `--args-file -`, never `$VAULT_...` in the arguments, and a failed command still uses up the handle. Verify with a `test_service.py` assertion on the done reply.
- [x] 2.4 Run the full suite, `tests/run-sidecar.sh vault`, and verify it passes.

## 3. Skill: `vault-wait` helper

- [x] 3.1 Write `agent-skills/using-vault-secrets/vault-wait` (Python, standard library only, executable, with a docstring in `vault-run`'s style), following design decisions 1–3:
  - usage checks (1–5 different ids);
  - one thread per pending id calling the wrapper (`LOLLO_MCP_SH`, default `/root/.letta/skills/mcp-servers/scripts/mcp.sh`) with `wait_seconds` 50 and `collect` false;
  - collect all at the end; a JSON array printed in the order given;
  - exit codes 0, 1 and 2; 3 retries on wrapper failure; the overall time guard;
  - SIGTERM and SIGINT handling;
  - exit 2 when a `collect: false` reply carries a handle (an old vault), printing that answer.
- [x] 3.2 Add `vault/tests/test_vault_wait.py` with a stub `mcp.sh` (a small script that answers from a JSON state file the test edits, and logs its calls). Cover: one id done; three ids approved at different times, with no collect call before the last is answered; a mix of denied and expired gives exit 1 with all answers printed; the wrapper failing gives exit 2 with nothing collected; bad usage gives exit 2 with no calls; SIGTERM stops it; and a placeholder answer prints no value. Verify with `tests/run-sidecar.sh vault -k vault_wait`.
- [x] 3.3 Make sure `tests/run-sidecar.sh vault` and CI find the new helper: it lives in the already-mounted `agent-skills/using-vault-secrets/`. Add it to the `-k` line in `agent-skills/README.md`'s "Build and test" (`vault_run or vault_fill or vault_wait`), and to vault/README's test note. Verify with `python3 -m py_compile agent-skills/using-vault-secrets/vault-wait`, and confirm the suite collects `test_vault_wait.py`.

## 4. Skill text and docs

- [x] 4.1 Rewrite `agent-skills/using-vault-secrets/SKILL.md`'s "Waiting for the user's approval" around `vault-wait`: post every link, run `vault-wait <id1> <id2> …` once in the background, act on its JSON, run it again with the ids still pending if it was stopped, and use the `get_result` fallback without a shell. Drop the "loop in one command runs into the time limit" claim. Keep the text generic. Verify by reading it against the spec scenario "reading the skill".
- [x] 4.2 In the same file, add the general handle rules to "Rules": the first `vault-run` uses up the handle even if the command fails; MCP arguments only from a `vault-fill` template; and the three errors ("already used", "dropped when the vault locked", "unknown or expired") with what to do for each. Rename the email recipe to "An MCP tool's arguments (an email, a form field, any tool)". Verify by reading it, and with `node scripts/check-doc-links.mjs`.
- [x] 4.3 Update `agent-skills/README.md`: a file-table row for `vault-wait`, "What each skill talks to", and Upstream seams (letta-code gives a background command no time limit unless one is set, checked in 0.34.67). Mirror the seam in `vault/README.md#upstream-seams` and in CONTRIBUTING.md's seams table row for "the vault and its skill", and add "background commands still have no time limit" to the "Upgrading `LETTUCE_REF`" checklist. Verify with `node scripts/check-doc-links.mjs`.
- [x] 4.4 Update `docs/vault.md` (what the agent does while you approve: it waits in one background command, and a spent handle means a new approval) and `docs/operations.md:109` (helpers: `vault-run`, `vault-fill`, `vault-wait`). Check whether `docs/diagrams/vault-flow.svg` shows the polling step, and update it only if it does. Verify with `node scripts/check-doc-links.mjs`.

## 5. Integration check

- [x] 5.1 Run every check the touched folders list: `tests/run-sidecar.sh vault`, `tests/run.sh test_skills_`, `node scripts/check-doc-links.mjs`, and the CLAUDE.md `shellcheck` line (unchanged files, to confirm nothing else broke). Verify they all pass, and record the output.
- [ ] 5.2 On a test stack (not a person's), after the user agrees: `lolloctl skills all`, then rebuild and restart the vault (it locks), unlock it, and ask the agent for two secrets. Approve one at once and the other after two minutes. Verify that the agent gets one background notification with both handles, and that redeeming one handle twice prints "already used". This needs the user's go-ahead, since it restarts a stack's vault.

## Workflow follow-up

- Done: `docs/diagrams/agent-skills-architecture` and `vault-architecture` show `vault-wait` and its `get_result` path, re-finalised against `bb0414b` with their SVGs exported again from the viewer.
- On `33-browser`, point `using-the-browser`'s "wait in the background" step at `vault-wait` once this is merged.
- Archive the change once it is reviewed and merged.

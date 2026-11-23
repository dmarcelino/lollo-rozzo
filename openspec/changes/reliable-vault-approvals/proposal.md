# Proposal

## Why

On 23 Nov 2026 an agent asked a stack's vault for the user's address, card and a shop password. The user approved four times, but the agent collected only after being told. It never started a wait for three requests, and it ended its turn after one 50-second `get_result` came back `pending`. Two handles that failed commands had used up were then reported as "expired after 5 minutes". The agent blamed the timeouts and asked for approval again and again.

The design depends on the model restarting a 50-second wait by hand after every `pending`, and on it telling apart error causes that the vault reports as one message. Both failed in practice.

## What Changes

- **New `vault-wait` helper** in `agent-skills/using-vault-secrets/`: `vault-wait <id> [<id>...]` keeps waiting on the listed requests until none is `pending`, then collects them all and prints each answer. It runs as one background command, so the agent is told once, when everything is answered. No restarting is needed, and it covers several requests at once.
- **`get_result` gains `collect` (default `true`)**: with `collect: false` it reports the status without handing over the handle or values. `vault-wait` needs this so that it doesn't start a handle's 5-minute clock while other requests are still waiting.
- **`request_secret`'s and `get_result`'s `pending` replies include the exact command**: a `wait_command` field with `python3 /root/.letta/skills/using-vault-secrets/vault-wait <id>`, repeated in the message. A `get_result` fallback stays for agents without a shell.
- **`/redeem` tells "already used" and "dropped by a lock" apart from "unknown or expired"**: the vault remembers spent and dropped handle ids, never values, until each would have expired. `vault-run` prints the specific reason, so the agent knows a failed command took the handle.
- **The handle reply and the skill say how to pass values into an MCP call**: fill a JSON template with `vault-fill` and pipe it into `mcp.sh call <tool> --args-file -`. Never put `$VAULT_...` in the arguments: the calling shell expands it to nothing first. The skill makes "a failed command still uses up the handle" a general rule, not just a line in the email recipe.
- **Docs**: the skill's wait section, `agent-skills/README.md`, `vault/README.md`, `docs/vault.md`, `docs/operations.md` and the upstream seams in CONTRIBUTING (a background command has no time limit in letta-code).

No timeout changes: none of the failures came from a timeout running out (see design).

Not in this change: on the `33-browser` branch, `using-the-browser` already types a vault password through a `vault-fill` template. That branch only needs its "wait in the background" step pointed at `vault-wait` once this lands, as a follow-up there.

## Capabilities

### New Capabilities
- `vault-approval-wait`: how an agent waits for and collects the user's decisions on its vault requests: the `pending` reply, `get_result`'s `collect`, and the `vault-wait` helper.
- `vault-handles`: how a placeholder handle is redeemed and what the agent is told when it can't be: already used, dropped by a lock, or unknown/expired. Also how values reach a command or an MCP call's arguments.

### Modified Capabilities
(none: `openspec/specs/` has no specs yet)

## Impact

- **Vault service**: `vault/src/lollo_vault/service.py` (`get_result`, `KEEP_WAITING`, `_deliver`'s message, `redeem`, `expire`), `mcp_tools.py` (the `get_result` signature and description), tests in `vault/tests/test_service.py` and `test_app.py`.
- **Agent skill**: new `agent-skills/using-vault-secrets/vault-wait` (Python, standard library only, like `vault-run`); `SKILL.md`; new `vault/tests/test_vault_wait.py`; `test_vault_run.py` gets the new error texts.
- **MCP tool schema**: `get_result` gets an optional parameter. Existing callers are unaffected.
- **Upstream seams**: `vault-wait` calls `get_result` through the `mcp-servers` wrapper (already a seam) and relies on letta-code giving background commands no time limit (`timeout 0`; checked in letta-code 0.34.67, `letta.js` Bash tool). That goes into the seams lists.
- **Operators**: `lolloctl skills all` pushes the new helper, and `lolloctl up` rebuilds the vault image. No config, profile or data migration.

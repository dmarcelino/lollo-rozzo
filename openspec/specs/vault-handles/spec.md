# vault-handles Specification

## Purpose

How a placeholder handle is redeemed by `vault-run`, what the agent is told when it can't be, and how its values reach a file, a command or an MCP call's arguments. The agent should act on the real reason instead of guessing.

## Requirements

### Requirement: A spent handle is reported as already used
When `/redeem` is given a handle that an earlier redeem already used, and that handle would not yet have expired, the vault SHALL refuse it with an error saying it was already used: the earlier command took the values, even if that command failed. It SHALL tell the agent to request the secret again.

#### Scenario: second redeem after a failed command
- **GIVEN** a handle redeemed once by a `vault-run` whose command then failed
- **WHEN** the agent runs `vault-run` again with the same handle within its lifetime
- **THEN** `vault-run` exits 2 and prints an error that says "already used" and that the earlier command took it even though it failed, and nothing runs

#### Scenario: after its lifetime
- **WHEN** the same spent handle is used again after it would have expired
- **THEN** the error is the generic "unknown or expired handle"

### Requirement: A handle dropped by a lock is reported as such
When the vault locks, it SHALL remember the dropped handles until each would have expired. Redeeming one SHALL be refused with an error saying the vault locked and dropped it, and that the agent must request the secret again once the vault is unlocked.

#### Scenario: lock between approval and use
- **GIVEN** an unused handle
- **WHEN** the vault locks and the agent then runs `vault-run` with that handle
- **THEN** the error says the vault was locked and the handle dropped, and does not say "expired"

### Requirement: Unknown and expired handles keep the generic error
A handle the vault never issued, or one past its lifetime, SHALL get the existing "unknown or expired handle" error. The error's wording SHALL say that a handle is almost always unknown because it was already used, or because its few minutes are up.

#### Scenario: never issued
- **WHEN** `vault-run` is given a made-up handle
- **THEN** it exits 2 with "unknown or expired handle"

#### Scenario: expired unused
- **WHEN** an unused handle is redeemed after `HANDLE_TTL_SECONDS`
- **THEN** it exits 2 with "unknown or expired handle"

### Requirement: Refusals stay all or nothing
A redeem with several handles in which any one is already used, dropped or unknown SHALL use up none of them. The error SHALL name the reason of the first refused handle and say that the others are still valid.

#### Scenario: one spent among two
- **WHEN** `vault-run h1 h2` is run with `h1` already used and `h2` fresh
- **THEN** the error says `h1`'s handle was already used and that the others are still valid, and `h2` can still be redeemed

### Requirement: The vault remembers spent handles without their values
The record of used and dropped handles SHALL hold only the handle id, the reason and the time it would have expired. It SHALL be kept in memory only, never on disk or in logs, and SHALL be forgotten once that time passes.

#### Scenario: the record holds no value
- **WHEN** a handle carrying a test secret is redeemed
- **THEN** the test secret appears nowhere in the vault's record of spent handles, its logs or its files on disk

#### Scenario: forgotten after expiry
- **WHEN** the spent handle's expiry time passes
- **THEN** the vault no longer holds any record of it

### Requirement: The handle reply says how values reach an MCP call
The `done` reply with a handle SHALL tell the agent how to pass the values into an MCP tool's arguments: fill a JSON template with `vault-fill` and pipe it into the `mcp-servers` wrapper with `--args-file -`, inside `vault-run`. It SHALL say never to write `$VAULT_...` into the arguments on the command line.

#### Scenario: collecting a handle
- **WHEN** `get_result` returns `done` with a handle
- **THEN** its message mentions `vault-fill`, `--args-file -`, and that `$VAULT_...` never goes in the arguments, and that a failed command still uses up the handle

### Requirement: The skill states the handle rules in general
The `using-vault-secrets` skill SHALL state as general rules, not only in one recipe:
- a handle is used up by its first `vault-run`, even when the command fails;
- an MCP call's arguments are filled from a `vault-fill` template, never from `$VAULT_...` in the arguments;
- "already used" means requesting again, not waiting or blaming a timeout.

Its wait section SHALL use `vault-wait`.

#### Scenario: reading the skill
- **WHEN** an agent reads `using-vault-secrets/SKILL.md`
- **THEN** it finds those three rules in its Rules section, each of the three handle errors with what to do, and `vault-wait` as the way to wait for approvals

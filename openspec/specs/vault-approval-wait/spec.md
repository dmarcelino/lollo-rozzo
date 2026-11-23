# vault-approval-wait Specification

## Purpose

How an agent waits for, and collects, the user's decisions on its vault requests. Nothing starts a turn when the user approves, so this has to keep working without the agent remembering to poll again.

## Requirements

### Requirement: A pending reply names the exact wait command
Whenever `request_secret` or `get_result` answers `pending`, the reply SHALL include a `wait_command` field holding the full shell command that waits on that request with the `vault-wait` helper. The reply's message SHALL repeat that command and tell the agent to run it as one background command. For agents without a shell, it SHALL also give the `get_result` fallback.

#### Scenario: request_secret answers pending
- **WHEN** an agent calls `request_secret` for a field that needs approval
- **THEN** the reply has `status` `pending`, the `approve_url`, and `wait_command` equal to `python3 /root/.letta/skills/using-vault-secrets/vault-wait <id>` with the request's id
- **AND** the message contains that same command, says to run it in the background, and says to call `get_result` with `wait_seconds` 50 until it is no longer pending when no shell is available
- **AND** the message says to run it even for a single request or one made again later in the task, and to end the turn leaving it running, without stopping it

#### Scenario: get_result still pending
- **WHEN** an agent calls `get_result` and the request is still `pending` when the wait ends
- **THEN** the reply carries the same `wait_command` for that id

#### Scenario: no wait command once answered
- **WHEN** a request is `done`, `denied`, `expired` or unknown
- **THEN** the `get_result` reply has no `wait_command`

### Requirement: A pending reply opens with three steps, the wait first
A `pending` reply SHALL open with "Next, in this order:" and three numbered steps: call Bash with `run_in_background: true` and the wait command, give the user the `approve_url` in the same response or after it, and end the turn leaving the command running. The reasons SHALL follow the steps. Its `wait_command` field SHALL come before `approve_url`. The locked-vault reply SHALL ask the agent to give the user the unlock link.

#### Scenario: a request that needs approval
- **WHEN** an agent calls `request_secret` for a field that needs approval
- **THEN** the message starts with "Waiting for the user's approval. Next, in this order: 1. Call Bash with run_in_background: true and the command" followed by the wait command
- **AND** step 2 gives the `approve_url`, and step 3 says to end the turn and leave the command running
- **AND** the reply's fields are `id`, `status`, `wait_command`, `approve_url`, in that order, then `message`

#### Scenario: still pending
- **WHEN** `get_result` answers `pending`
- **THEN** the message opens with the same three steps, step 2 asking for the link only if the user hasn't been given it yet

### Requirement: get_result can report status without collecting
`get_result` SHALL accept an optional `collect` flag, default true. With `collect` false it SHALL wait and report the status as usual, but SHALL NOT hand over a handle or values, SHALL NOT start a handle's lifetime, and SHALL leave an approved result collectable by a later call.

#### Scenario: peeking at an approved request
- **WHEN** the user has approved a request and the agent calls `get_result` with `collect` false
- **THEN** the reply has `status` `done` and no `handle`, `placeholders` or `values`
- **AND** a later `get_result` with `collect` true returns the handle (or the values for `text` delivery) once

#### Scenario: default behaviour unchanged
- **WHEN** an agent calls `get_result` without `collect`
- **THEN** it behaves as before: an approved result is handed over and marked as returned

#### Scenario: peeking at a pending request
- **WHEN** the request is still pending and `collect` is false
- **THEN** the reply is the same `pending` reply, with `wait_command`, as with `collect` true

### Requirement: vault-wait waits on several requests in one command
The skill SHALL ship a `vault-wait <id> [<id>...]` helper. It keeps waiting until none of the listed requests is `pending`, then collects every one and prints one JSON answer per id. It SHALL need no restart from the agent while the requests are within their lifetime.

#### Scenario: all approved, at different times
- **GIVEN** three pending requests
- **WHEN** the agent runs `vault-wait a b c` in the background and the user approves `a` at once and `b` and `c` four minutes later
- **THEN** `vault-wait` keeps running until `c` is answered, then prints the collected answer of `a`, `b` and `c` (each with its handle and placeholders) and exits 0

#### Scenario: handles are collected only at the end
- **WHEN** one request is approved while another is still pending
- **THEN** `vault-wait` does not collect the approved one until the other is answered, so every handle it prints has its full lifetime left

#### Scenario: some denied or expired
- **WHEN** at least one listed request ends `denied`, `expired` or unknown, and the rest are `done`
- **THEN** `vault-wait` prints every answer, collecting the `done` ones, and exits 1

#### Scenario: no answer before the requests expire
- **WHEN** the user never answers
- **THEN** `vault-wait` stops once the vault reports the requests `expired` (at most the request lifetime, 10 minutes, plus one wait), and never runs forever

#### Scenario: the vault cannot be reached
- **WHEN** the call to `get_result` fails repeatedly (the bridge or the vault is down)
- **THEN** `vault-wait` retries a few times, then prints the error and the ids still unanswered and exits 2, collecting nothing

#### Scenario: bad usage
- **WHEN** `vault-wait` gets no id, a repeated id, or more ids than the vault lets wait at once (5)
- **THEN** it prints its usage and exits 2 without calling the vault

### Requirement: vault-wait adds no new path to the vault
`vault-wait` SHALL call the vault only through the agent's own `get_result` tool, via the `mcp-servers` wrapper. It SHALL use the Python standard library only, and its output SHALL hold nothing that `get_result` itself would not return.

#### Scenario: a text delivery is collected
- **WHEN** a listed request used `delivery` `text` and is approved
- **THEN** `vault-wait` prints the same `values` and `rules` that `get_result` returns, and nothing more

#### Scenario: placeholder delivery keeps values out
- **WHEN** a listed request used the default `placeholders` delivery
- **THEN** `vault-wait`'s output contains the handle and placeholders and no private value

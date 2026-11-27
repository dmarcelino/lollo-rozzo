# Proposal

## Why

An agent with a shell can rewrite lollo's own helpers (`vault-run`), drop a cron file, repoint an MCP entry or change its container's files. Nothing compares any of this with what was shipped, and a backup carries it to the next host (SEC-002, attack chain 2). This change cannot stop the edit itself: agents are root in the app-server and write `letta-home` by design. What it can do cheaply, without taking a feature away, is let the operator see the tampering and recover from it, and keep backups from bringing it back.

## What Changes

- New `lolloctl audit <name|all>`: a report-only check of the stack's agent runtime. It reads the state from a throwaway container of lollo's backup image, with the state mounted read-only and no network, and never through `compose exec` into the app-server, whose binaries agents control. It reports:
  - lollo's installed skills (`agent-skills/`) and executor files (`landing/executor/`) that differ from the shipped ones;
  - skills, cron files (`letta-home/system-cron`) and Settings -> MCP entries that lollo did not install and the operator has not accepted;
  - a summary of `docker diff` for the app-server's container, with sensitive paths named.
- `lolloctl audit <name> --accept <entry>` records an entry the operator recognises in `stacks/<name>/audit-accepted`, outside `state/`, where agents cannot write. An acceptance is bound to the entry's content hash, so a later change to it is reported again.
- `status`, `up` (before it recreates the app-server or reinstalls the executor), `skills` (before it replaces lollo's skills) and `backup` (before it copies) run the audit and print a one-line summary. The audit never fails or blocks those commands.
- Every value read from the state or the container (skill, cron and MCP names, paths) is stripped of control characters before it is printed. This reuses the `printable` helper from `safer-operator-tooling`, or adds it if that change has not landed yet.
- `backup` leaves out the files `up` and `skills` reinstall, chosen from the shipped `agent-skills/` names on the host, no longer from the agent-writable `.lolloctl-skills` manifest. It also leaves out the executor's directory and its cron file. A restore then cannot bring back a tampered copy of lollo's own files.
- A recovery runbook in `docs/`: how to inspect a stack you suspect, how to bring it back to a clean state (a fresh container, reinstalled lollo files, removing flagged entries, or an earlier backup), and which secrets to rotate. It also states that the VAPID keys are never rotated.

Not in this change (deferred): a scheduled recreate of the app-server, `lolloctl reset`, `restore --clean`, and a read-only path for lollo's helpers.

## Capabilities

### New Capabilities
- `cli/runtime-audit`: how `lolloctl` reports differences between a stack's agent runtime and what lollo shipped or the operator accepted, where it reads from, and when it runs.
- `cli/backup-contents`: which agent-runtime files a backup leaves out because lolloctl reinstalls them, and how that list is chosen.

### Modified Capabilities
None. No existing spec covers the CLI or backups.

## Impact

- `lolloctl`: the new `cmd_audit` and its helpers; calls from `cmd_status`, `up_stack`, `cmd_skills` and `backup_stack`; `BACKUP_COPY` (which no longer reads the manifest) and `backup_stack`'s excludes; usage text.
- `lib/common.sh`: `printable`, unless `safer-operator-tooling` has already added it.
- `backup/Dockerfile`: unchanged if the base image's `sha256sum`, `find` and `sh` are enough (they come with `debian:bookworm-slim`).
- Tests: new `test_audit_*` cases and updated `test_backup_*` cases in `tests/run.sh`, plus docker stub routes for `diff` and the audit container.
- Docs: `lib/README.md` (a new rule), `backup/README.md` (what is left out), `docs/operations.md` (audit, backups, security notes), a new `docs/incident-response.md`, `CONTRIBUTING.md` if the command list there names commands, and `CLAUDE.md` (a one-line directive).
- No container, network, volume or route changes. No Lettuce change. The vault is not recreated.

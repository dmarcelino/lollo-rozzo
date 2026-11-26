# Design

## Context

See proposal.md for the motivation. These facts about the code shape the approach:

- **Where the files are.** `stacks/<name>/state/` is root-owned and the app-server mounts it. `letta-home` (the container's `/root/.letta`) holds the global skills (`skills/`, with lollo's manifest `.lolloctl-skills`), the executor (`lollo/executor/bin/run.mjs`), cron files (`system-cron/`, which upstream's entrypoint copies to `/etc/cron.d` at every start) and the MCP list (`mcp-home/.letta/settings.json`, `MCP_SETTINGS`).
- **Reading the state.** `backup` already reads the state as root, without sudo, from `BACKUP_RUN` containers of `lollo-backup` (`--network none`, read-only root, read-only mounts). `executor_present` tests only whether paths exist from the host, because the operator may not be allowed to read the files themselves.
- **Who reinstalls what.**
  - `up` reinstalls the executor (`sync_executor`) after `up_stack` has recreated whatever changed.
  - `skills`, `apply` and `_after-upgrade` reinstall lollo's skills; `up` does not.
  - `SKILLS_INSTALL` always replaces the skills named on the command line, so a tampered manifest cannot keep a lollo skill from being replaced.
- **Backups read the agents' manifest.** `BACKUP_COPY` reads the agent-writable `.lolloctl-skills` inside the container to choose its excludes.
- **Agents may add cron files.** Upstream's `installing-tools` skill documents adding scheduled jobs to `system-cron`, and people install skills from Settings. Unknown entries are therefore expected, and the audit has to let the operator accept them.

## Goals / Non-Goals

**Goals:**
- Show tampering with lollo's own files, and with the places a persistence step lands, without trusting anything the agent controls.
- No change to what agents or people can do. The audit reports and never blocks.
- Backups that cannot bring back a tampered copy of lollo's files.

**Non-Goals:**
- Preventing the edits. Agents are root in the app-server and write `letta-home` by design.
- A scheduled app-server recreate, `lolloctl reset`, `restore --clean`, a read-only helper path, and detecting changes in memory, conversations or workspaces.
- Watching the stack continuously. The audit runs only when the operator runs a command.

## Decisions

### 1. Read through a `lollo-backup` container, never through the app-server
The audit runs one `BACKUP_RUN` container per stack. It mounts `state/letta-home` read-only at `/src` and runs a small `sh` script (`AUDIT_SCAN`, like `BACKUP_COPY`). The script prints, one record per line:
- the `sha256sum` of every file under `skills/` and `lollo/executor/bin/`;
- the names of the entries in `skills/` and `system-cron/`;
- each cron file's hash and its first few lines;
- the MCP settings file as it is.

lolloctl then hashes the shipped trees in `LOLLO_HOME` on the host and does the comparison there, with `jq` for the MCP JSON. Nothing runs in the app-server, whose `sh`, `sha256sum` and `node` the agent controls.

- *Alternative: read from the host directly.* Rejected. The operator may lack read access to root's files, and in release mode lolloctl is not root.
- *Alternative: `compose exec app-server`.* Rejected. That is exactly what an agent can subvert.

The image already has `sh`, `find` and `sha256sum` (coreutils comes with `debian:bookworm-slim`), so `backup/Dockerfile` does not change. `build_backup_image` is called first, as `backup` does.

### 2. Container changes come from the Docker daemon
`compose NAME ps -q app-server` finds the container, and `host_docker diff <id>` lists its changes. lolloctl reports:
- the number of changes;
- each change under a fixed `AUDIT_SENSITIVE` list (`/etc/cron.d`, `/etc/ld.so.preload`, `/etc/profile.d`, `/etc/bash.bashrc`, `/etc/environment`, `/usr/bin`, `/usr/sbin`, `/usr/local/bin`, `/usr/local/lib`, and letta-code's install directory);
- minus lollo's expected entries: `/etc/cron.d/lollo-rozzo-executor` and `/etc/lollo-rozzo` with its token.

`--verbose` prints the whole diff. With no container, this section is skipped with a note. The audit runs before `up` recreates anything, because a recreate throws this evidence away.

### 3. What counts as expected
- **Lollo's skills:** each directory in `agent-skills/`. Every file is compared by content (the install runs `tar --no-same-owner`, so owners and modes are not compared). Added, changed and missing files are all reported.
- **Upstream's skills:** `upstream_dir/docker/agent-skills/*`, by name only (the BFF rewrites them on every connect).
- **The executor:** `landing/executor/run.mjs` against `lollo/executor/bin/run.mjs`, and `executor_cron NAME` against `system-cron/lollo-rozzo-executor`, when `executor_wanted`. When the executor is not wanted, its files being present are reported.
- **MCP:** the entries `mcp-servers.mjs`'s groups own (the exact HTTP URLs and the `/opt/lollo-doc-tools/` commands). Two kinds are reported:
  - an entry named `vault`, `news-radar`, `browser`, `office` or `pdf` that points elsewhere;
  - any other entry that has not been accepted.

  The expectations are restated in shell rather than by running `mcp-servers.mjs`, which needs `node` where it runs. `tests/mcp-servers.test.mjs` and `test_audit_*` hold the two lists in step: a test fails if a `GROUPS` URL is missing from the audit's list.
- **The `.lolloctl-skills` manifest:** reported when it differs from the shipped names. It is a hint, never a source of truth.

### 4. Acceptances live in `stacks/<name>/audit-accepted`, bound to a hash
- **Format:** one line per entry, `<kind>:<key> <sha256>`, where kind is `skill`, `cron`, `mcp` or `diff`. Skills are hashed as a sorted list of path and hash pairs, cron files by content, MCP entries by their canonical JSON (`jq -cS`), and `diff` entries by the change kind, the path and, for a file or link, what the daemon's copy of it holds (`docker cp <id>:<path> -`, never exec: bytes, mode, owner, size, link target, but not its time, since upstream's entrypoint copies the cron files again on every start), so an accepted file written again is reported again. A directory or a deleted path is held to kind and path alone, since a directory's entries are changes of their own. Only accepted `diff` entries are read this way during an audit; `--accept` reads the one it records, so `status` does not copy every changed file.
- **Writing it:** `--accept` takes the hash from the current scan and writes with the same atomic temp-file-and-rename as `env_set`, mode 600. An entry that lollo reinstalls (a lollo skill or the executor) is refused. `--unaccept` removes a line.
- **Why there:** `stacks/<name>/` is not mounted into any container, unlike `state/` under it.
- *Alternative: accept by name only.* Rejected. An agent could then replace an accepted skill's contents unseen.

### 5. Exit codes and callers
- **`cmd_audit`:**
  - exit 0 when clean, 1 when there are findings, 2 when it could not audit (no state directory, no image, or a scan that failed);
  - `all` audits every stack and returns the highest code.
- **The other commands** call `audit_summary NAME`, which runs the audit in a subshell, captures its code and prints one line: `alfred: runtime audit: 3 finding(s); run: lolloctl audit alfred`, or a warning that it could not run. They ignore the code:
  - `cmd_status`, per stack, after its `ps` table;
  - `up_stack`, before `check_stack` and before `compose up`;
  - `cmd_skills`, before `sync_skills`;
  - `backup_stack`, before pausing.
- **Cost:** about one short container per stack per command, which is acceptable for operator commands. `apply` reaches it through `up` and `skills`, so it audits twice; the second run is cheap and shows what `up` changed.

### 6. `printable` is shared with `safer-operator-tooling`
That change adds `printable` to `lib/common.sh`: it replaces C0 and C1 control characters and DEL with `?`. If it has not landed when this change is applied, this change adds the same helper with the same name and tests, and the other change reuses it. Every audit line built from scanned data goes through it, cron excerpts and MCP names included.

### 7. Backups choose their excludes on the host
`backup_stack` adds `state/letta-home/skills/<s>` for each `agent-skills/*/` name, `state/letta-home/skills/.lolloctl-skills`, `state/letta-home/lollo/executor` and `state/letta-home/system-cron/lollo-rozzo-executor` to its `excludes`, next to upstream's skills. The manifest-reading block leaves `BACKUP_COPY`.

`restore` needs no change. The guide already says to run `lolloctl up` and `lolloctl skills` after a restore, and those reinstall the shipped copies.

### 8. The recovery runbook
A new `docs/incident-response.md`, linked from `docs/operations.md` ("Security notes" and "Backups") and from the README's index, covers:
- **Inspect:** `audit`, `audit --verbose`, the Runs pane, pipelock receipts.
- **Contain:** `lolloctl down`, which also discards the container layer.
- **Clean:** `up` and `skills` restore lollo's files and give a fresh container. Remove the flagged unknown entries with `lolloctl shell <name>` straight after that `up`, before any turn starts. Or restore a backup from before the suspected injection.
- **Rotate:** the list is derived from what the app-server can read, checked against `compose config` for the app-server's environment and against `letta-home`. It covers model-provider keys, Claude, Codex and gh logins, tokens entered in Settings -> MCP, the executor token (delete `LOLLO_EXECUTOR_TOKEN` and run `up`), Telegram's bot token, and any vault value released since the suspected start, which the vault's audit page lists. It also says what agents could not read and why (the notification senders' tokens, the vault's data, the browser's sign-ins), and that Google is reconnected only if its tokens were in reach.
- **Never:** `PUSH_VAPID_*` and `LOLLO_VAPID_*`, with the reason.

## Risks / Trade-offs

- [An agent changes a file between the audit and the operator reading it, or right after] → The audit is evidence at a point in time, not a guard. The runbook says so, and `up` and `skills` replace lollo's files right after auditing them.
- [Unknown skills, cron files and MCP entries the person added on purpose make the report noisy] → A hash-bound accept list. The summary line gives a count, not the whole list.
- [An agent fills `system-cron` or `skills` with thousands of entries to bury a real one] → The scan caps the lines it prints per kind, says how many it left out, and always prints lollo's own files first.
- [The sensitive-path list misses a persistence point] → `--verbose` prints the whole diff, and the list is one array in `lolloctl`, extended with a test per path.
- [`status` becomes slower] → One short container per stack. `status` stays usable while a stack is down.
- [A hostile file name breaks the scan's line format] → The scan prints names NUL-free and escaped (`find -print0`, then `printf %q`-style quoting inside the container). lolloctl parses the output, never sources it, and refuses lines that do not parse.
- [`MCP_SETTINGS`' layout changes upstream] → The scan reports a missing or unparsable file as "could not read the MCP list", not as clean, and the upgrade checklist names the audit.

## Migration Plan

- No data migration. The first audit of an existing stack lists everything the person added. The operator accepts what they recognise once.
- The first backup after the change is smaller by lollo's skills and the executor. Restores of older backups behave as before, and `up` and `skills` overwrite those copies.
- To roll back, revert the change. `stacks/<name>/audit-accepted` is then ignored and can be deleted.

## Open Questions

- The exact path of letta-code's global install inside the app-server image. It is read from the image (`npm root -g`, run through `host_docker run` of the image) when the sensitive list is written, and it does not change the approach.

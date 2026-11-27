# Tasks

## 1. Shared helper

- [x] 1.1 If `safer-operator-tooling` has not added `printable` to `lib/common.sh`, add it (C0, C1 control characters and DEL become `?`), with a `test_printable_*` case for an ANSI escape and a C1 character; verify with `tests/run.sh test_printable_` and shellcheck. Otherwise reuse it and note that in the PR.

## 2. Scanning the state

- [x] 2.1 Add `AUDIT_SCAN` to `lolloctl`, an `sh` script run in a `BACKUP_RUN` container of `lollo-backup` with `state/letta-home` mounted read-only at `/src`. It prints parseable records: file hashes under `skills/` and `lollo/executor/bin/`, the entries in `skills/` and `system-cron/`, each cron file's hash and first lines, and the MCP settings file. Hostile names are quoted and each kind's lines are capped. Verify with a `test_audit_scan_*` case that runs the script on the host's `sh` against a temp tree holding a name with a newline and an escape.
- [x] 2.2 Add the host-side comparison:
  - shipped trees hashed from `LOLLO_HOME/agent-skills` and `landing/executor/run.mjs`;
  - `executor_cron NAME` for the cron text;
  - upstream skill names from `upstream_dir`;
  - the MCP expectations (`AUDIT_MCP`: the `vault`, `news-radar` and `browser` URLs and the `/opt/lollo-doc-tools/` prefix).

  Verify with `test_audit_reports_a_modified_vault_run`, `test_audit_reports_an_added_file_in_a_lollo_skill`, `test_audit_reports_a_changed_executor`, `test_audit_reports_an_extra_skill`, `test_audit_reports_a_cron_file`, `test_audit_reports_a_repointed_mcp_entry`, `test_audit_ignores_upstream_skills` and `test_audit_is_clean_on_a_fresh_install`.
- [x] 2.3 Make the audit's MCP expectations and `mcp-servers.mjs`'s `GROUPS` fail a test when they drift; verify with `node --test tests/mcp-servers.test.mjs` or a `test_audit_mcp_list_matches_mcp_servers` case.

## 3. Container changes

- [x] 3.1 Add the container section: find the container with `compose NAME ps -q app-server`, read `host_docker diff`, report the count and the changes under `AUDIT_SENSITIVE` minus lollo's own entries, print everything with `--verbose`, and skip with a note when there is no container. Add a `diff` route to `tests/stubs/docker`. Verify with `test_audit_names_ld_so_preload`, `test_audit_ignores_the_executor_cron_and_token` and `test_audit_skips_the_container_when_the_stack_is_down`.
- [x] 3.2 Read letta-code's global install path from the app-server image, settling the design's open question, and add it to `AUDIT_SENSITIVE` with a test case for a file added there.

## 4. The command, acceptances and exit codes

- [x] 4.1 Add `cmd_audit <name|all> [--verbose]` to the usage text and dispatch. It exits 0, 1 or 2, `all` returns the highest, and it never makes a `compose exec`. Verify with `test_audit_exit_codes` and `test_audit_never_execs_into_the_stack` (the docker stub logs every call; assert no `exec`).
- [x] 4.2 Add `--accept <kind:key>` and `--unaccept <kind:key>`, which write `stack_dir NAME/audit-accepted` atomically with mode 600, bind the entry to the current hash, and refuse lollo's own skills and the executor. Verify with `test_audit_accept_hides_an_unchanged_entry`, `test_audit_reports_a_changed_accepted_skill`, `test_audit_accept_refuses_lollo_files` and `test_audit_accepted_list_is_outside_state`.
- [x] 4.3 Make every printed value from the scan or the diff go through `printable`. Verify with `test_audit_strips_escapes_from_names` (an unknown skill and a cron line with `\e[2J`).

## 5. Callers

- [x] 5.1 Add `audit_summary NAME` (one line, the exit code ignored, a warning when it could not run) and call it from:
  - `cmd_status` after each stack's table;
  - `up_stack` before `check_stack` and `compose up`;
  - `cmd_skills` before `sync_skills`;
  - `backup_stack` before pausing.

  Verify with `test_up_audits_before_recreating` (the docker stub log shows the scan and `diff` before `up -d`), `test_skills_audits_before_installing`, `test_status_prints_the_audit_summary` and `test_backup_goes_on_when_the_audit_fails`. The existing `test_up_*`, `test_skills_*` and `test_backup_*` cases must still pass.

## 6. Backups choose their excludes on the host

- [x] 6.1 In `backup_stack`, exclude `state/letta-home/skills/<s>` for each `agent-skills/*/` name, `state/letta-home/skills/.lolloctl-skills`, `state/letta-home/lollo/executor` and `state/letta-home/system-cron/lollo-rozzo-executor`. Remove the manifest-reading block from `BACKUP_COPY`. Verify with `test_backup_leaves_out_lollo_skills_by_shipped_names`, `test_backup_ignores_an_edited_manifest`, `test_backup_keeps_the_persons_skills` and `test_backup_leaves_out_the_executor`, in both rsync and tar mode, plus the existing `test_restore_` round trip.
- [x] 6.2 Update `backup/README.md` (Rules: what is left out and why it comes from the code), and the "Backups" section of `docs/operations.md` (the executor too, and that the list comes from the code). Verify with `node scripts/check-doc-links.mjs`.

## 7. Docs and directives

- [x] 7.1 Add a rule to `lib/README.md`: "The runtime audit reads the state from outside the agents' reach and only reports", with what it compares, where acceptances live, the exit codes and its callers. Add `audit` to `docs/operations.md` ("Everyday" and "Security notes") and to the command list in `README.md` if it lists commands. Verify with `node scripts/check-doc-links.mjs`.
- [x] 7.2 Write `docs/incident-response.md` (inspect, contain, clean, rotate, never rotate the VAPID keys). Derive the rotate list from the rendered app-server config (`lolloctl compose alfred config` on a test stack) and from `letta-home`'s contents, not from memory. Link it from `docs/operations.md` and the README's docs index. Use only placeholder names (`alfred`, `example.com`). Verify with `node scripts/check-doc-links.mjs`.
- [x] 7.3 Add the audit to the Lettuce upgrade checklist in `CONTRIBUTING.md`: check `MCP_SETTINGS`, `system-cron` and letta-code's install path still hold. Add a one-line directive to `CLAUDE.md` under "The CLI": the audit never inspects a stack through `compose exec`, never blocks, and keeps acceptances outside `state/`. Verify with `node scripts/check-doc-links.mjs`.

## 8. Integration checks

- [x] 8.1 Run `tests/run.sh`, the full shellcheck command from `CLAUDE.md`, `node --test tests/mcp-servers.test.mjs` and `node scripts/check-doc-links.mjs`; all pass.
- [x] 8.2 On a throwaway stack (never beside live stacks on this Mac):
  - append a line to `vault-run` from an agent's shell, add a file to `system-cron` and create `/etc/ld.so.preload` in the container;
  - check that `lolloctl status` summarises three findings and `lolloctl audit` names each;
  - check that `lolloctl up` and `lolloctl skills` report them before replacing lollo's files;
  - check that a backup taken afterwards holds none of lollo's skills or executor files.

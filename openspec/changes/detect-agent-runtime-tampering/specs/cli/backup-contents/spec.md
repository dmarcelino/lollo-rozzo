# Spec Delta

## Purpose

Keeps backups from carrying a tampered copy of lollo's own agent-runtime files to a restored stack: what lolloctl reinstalls is left out, chosen from what the code ships rather than from anything agents can write.

## ADDED Requirements

### Requirement: Backups leave out lollo's reinstalled skills, chosen from the shipped names
`backup` SHALL leave out each global skill whose name is a skill directory in the code's `agent-skills/`, plus the install manifest. It SHALL choose them from the code on the host and SHALL NOT read the list from the stack's state.

#### Scenario: A tampered lollo skill is not copied
- **GIVEN** alfred's installed `using-vault-secrets/vault-run` was changed
- **WHEN** the operator runs `lolloctl backup alfred /mnt/backup`
- **THEN** the snapshot holds no `state/letta-home/skills/using-vault-secrets`

#### Scenario: An edited manifest changes nothing
- **GIVEN** an agent removed `using-vault-secrets` from `.lolloctl-skills` and added another skill's name to it
- **WHEN** the operator runs a backup
- **THEN** `using-vault-secrets` is still left out and the other skill is still copied

#### Scenario: Skills the person installed are kept
- **WHEN** alfred's global skills hold a skill that neither lollo nor upstream ships
- **THEN** the backup copies it

### Requirement: Backups leave out the lollo executor
`backup` SHALL leave out the executor's directory under `letta-home` and its cron file in `system-cron`, which `up` reinstalls.

#### Scenario: Executor files are not copied
- **WHEN** the operator backs up a stack that has the lollo executor installed
- **THEN** neither the executor's directory nor its `system-cron` job is in the backup, in snapshot or `--compress` mode

#### Scenario: A restored stack gets the shipped copies back
- **GIVEN** a stack restored from such a backup
- **WHEN** the operator runs `lolloctl up alfred` and `lolloctl skills alfred`
- **THEN** the executor and lollo's skills are the shipped ones

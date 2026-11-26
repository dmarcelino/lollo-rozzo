# Spec Delta

## Purpose

Lets the operator see when a stack's agent runtime departs from what lollo shipped or what they accepted, so a tampered helper, cron file, MCP entry or container change is noticed instead of persisting silently.

## ADDED Requirements

### Requirement: The audit reads the state from outside the agents' reach
`lolloctl audit` SHALL read the stack's agent runtime only from a container of lollo's own image, with the state mounted read-only and no network, and from the Docker daemon. It SHALL never run a command in the app-server or another stack container to inspect it.

#### Scenario: No exec into the app-server
- **WHEN** the operator runs `lolloctl audit alfred` on a running stack
- **THEN** no `compose exec` or `docker exec` into any of alfred's containers is made, and the state is mounted read-only into a container with no network

#### Scenario: A stopped stack is still audited
- **WHEN** alfred is down and the operator runs `lolloctl audit alfred`
- **THEN** every file check runs, and the report says the container check was skipped because no app-server container exists

### Requirement: Lollo's installed files are compared with the shipped ones
The audit SHALL compare each skill lollo installs (by the names in `agent-skills/`) and the executor's installed files with the shipped copies, by content, and report each file that differs, is missing or was added.

#### Scenario: A modified helper is reported
- **GIVEN** an agent appended a line to `vault-run` in alfred's installed `using-vault-secrets` skill
- **WHEN** the operator runs `lolloctl audit alfred`
- **THEN** the report names `using-vault-secrets/vault-run` as changed

#### Scenario: A file added inside one of lollo's skills is reported
- **WHEN** an installed lollo skill holds a file the shipped skill does not have
- **THEN** the report names that file as added

#### Scenario: A changed executor is reported
- **WHEN** the installed executor script differs from `landing/executor/run.mjs`
- **THEN** the report names the executor as changed

### Requirement: Unknown skills, cron files and MCP entries are reported
The audit SHALL report each global skill that is neither lollo's, upstream's nor accepted. It SHALL report each cron file other than lollo's executor job, and lollo's job when its text differs from what lolloctl would write. It SHALL report each Settings -> MCP entry that is not lollo's own and not accepted, and each entry under a lollo name that points elsewhere.

#### Scenario: An extra skill is reported
- **WHEN** alfred's global skills hold a skill named `helper-x` that neither lollo nor upstream ships and the operator has not accepted
- **THEN** the report lists `helper-x` as an unknown skill

#### Scenario: A dropped cron file is reported
- **WHEN** alfred's `system-cron` holds a file other than lollo's executor job
- **THEN** the report lists that file, with its first lines stripped of control characters

#### Scenario: A repointed MCP entry is reported
- **WHEN** Settings -> MCP has a server named `vault` whose URL is not `http://lollo-vault:8000/mcp`
- **THEN** the report lists it as a lollo-named entry pointing elsewhere

#### Scenario: Upstream's skills are not reported
- **WHEN** a global skill has the name of a skill upstream Lettuce ships
- **THEN** the report does not list it as unknown

### Requirement: The container's own changes are summarised
When the app-server's container exists, the audit SHALL summarise the container's changes against its image as reported by Docker. It SHALL name each change under a fixed list of sensitive paths (cron, the dynamic loader's preload, shell start-up files, system binary directories and letta-code's install) other than lollo's own.

#### Scenario: A preload file is named
- **WHEN** the app-server's container has an added `/etc/ld.so.preload`
- **THEN** the report names `/etc/ld.so.preload` under the sensitive changes

#### Scenario: Lollo's own container files are not reported
- **WHEN** the only changes under sensitive paths are lollo's executor cron file and its token directory
- **THEN** the report shows no sensitive changes

### Requirement: The operator can accept an entry, bound to its content
`lolloctl audit <name> --accept <entry>` SHALL record the entry with its current content hash in a file under the stack's directory and outside its state directory. An accepted entry SHALL be reported again once its content changes.

#### Scenario: An accepted skill is no longer reported
- **GIVEN** the operator ran `lolloctl audit alfred --accept skill:helper-x`
- **WHEN** the operator runs `lolloctl audit alfred` again and `helper-x` is unchanged
- **THEN** the report does not list `helper-x`

#### Scenario: A changed accepted skill is reported again
- **GIVEN** `helper-x` was accepted
- **WHEN** a file in `helper-x` changes afterwards
- **THEN** the report lists `helper-x` as changed since it was accepted

#### Scenario: An accepted container file written again is reported again
- **GIVEN** the operator accepted `diff:/usr/bin/git-helper`
- **WHEN** the agent later overwrites `/usr/bin/git-helper` in the app-server, and `docker diff` still shows it as added
- **THEN** the report lists `/usr/bin/git-helper` as changed since it was accepted

#### Scenario: The accepted list is out of the agents' reach
- **WHEN** the operator accepts an entry
- **THEN** the record is written under `stacks/alfred/` and not under `stacks/alfred/state/`, which the app-server mounts

#### Scenario: Lollo's own files cannot be accepted
- **WHEN** the operator tries to accept a changed file of a lollo skill or of the executor
- **THEN** the command refuses and says that `lolloctl skills` or `lolloctl up` reinstalls it

### Requirement: The audit reports and never blocks
`lolloctl audit` SHALL exit 0 when it finds nothing, 1 when it reports findings and 2 when it could not audit. `status`, `up`, `skills` and `backup` SHALL run the audit first and print a one-line summary naming `lolloctl audit <name>`. Whatever the audit finds or fails on, they SHALL go on as they would without it.

#### Scenario: Exit codes
- **WHEN** the audit of alfred finds a changed `vault-run`
- **THEN** `lolloctl audit alfred` exits 1, and exits 0 once nothing is reported

#### Scenario: up audits before it replaces anything
- **GIVEN** alfred's executor script was changed and its container has an added cron file
- **WHEN** the operator runs `lolloctl up alfred`
- **THEN** the summary reports both before the app-server is recreated or the executor reinstalled, and `up` then completes as before

#### Scenario: skills audits before it replaces lollo's skills
- **WHEN** the operator runs `lolloctl skills alfred` and `vault-run` was changed
- **THEN** the summary reports it before the skills are reinstalled

#### Scenario: A failed audit does not stop a backup
- **WHEN** the audit cannot run during `lolloctl backup alfred` (for example, the image is missing)
- **THEN** `backup` warns that the audit did not run and goes on with the copy

### Requirement: Values from the state are printed without control characters
Every name, path or line the audit reads from the state or the container SHALL have its control characters replaced before it is printed, so no agent-written text can move the cursor, clear lines or change the terminal.

#### Scenario: An escape sequence in a skill name
- **WHEN** an unknown skill's directory name holds an ANSI escape sequence
- **THEN** the report prints the name with the escape character replaced, and the terminal's state is unchanged

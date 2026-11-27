# Spec Delta

## Purpose

`lolloctl toolbox` lets the operator work on one agent's workspace with their own tools and credentials, without those credentials ever reaching the agent's container, and without the agent being able to choose what gets mounted or what runs with them.

## ADDED Requirements

### Requirement: Agent ids from the stack are validated before use
`toolbox` and `shell <name> <agent>` SHALL use an agent id read from the app-server only if it matches `^agent-[A-Za-z0-9-]{1,80}$`. Otherwise they SHALL stop before running any Docker command.

#### Scenario: An id with a path in it
- **WHEN** the app-server's agent list returns, for the agent the operator named, the id `agent-1/../../stacks`
- **THEN** `toolbox` stops with an error naming an unexpected agent id, and no `docker run` is made

#### Scenario: An id with a colon
- **WHEN** the agent list returns the id `agent-1:/etc`
- **THEN** `toolbox` stops before any `docker run`

#### Scenario: A well-formed id
- **WHEN** the agent list returns `agent-0b1c2d3e-aaaa-bbbb-cccc-000000000001` for `lucius`
- **THEN** `toolbox` continues

### Requirement: The workspace is reached through the fixed parent mount
`toolbox` SHALL bind-mount the stack's `state/workspaces` directory at `/work` and start in `/work/<id>`. It SHALL NOT bind-mount a path built from the agent id, and SHALL refuse a workspace, or a workspaces directory, that is a symbolic link.

#### Scenario: Ordinary workspace
- **WHEN** the operator runs `lolloctl toolbox alfred lucius`
- **THEN** the container gets `state/workspaces` at `/work`, its working directory is `/work/<id>`, and `USER_CWD` is `/work/<id>`

#### Scenario: Workspace replaced by a link
- **WHEN** the agent's workspace on the host is a symbolic link
- **THEN** `toolbox` stops with an error saying so, and no `docker run` is made

### Requirement: Operator credentials are opt-in
`toolbox` SHALL bring in an operator credential only when that kind was chosen. The kinds are `gh` (GitHub token and git credential helper), `ssh` (ssh-agent socket) and `claude` (Claude token and Claude Code config). A kind is chosen with its flag, or by a `use` line in the toolbox file.

#### Scenario: Nothing chosen
- **WHEN** the host has a logged-in `gh`, an ssh-agent and a Claude token, and the operator runs `lolloctl toolbox alfred lucius` with no flags and no `use` line
- **THEN** the `docker run` has no `GH_TOKEN`, no `SSH_AUTH_SOCK`, no `CLAUDE_CODE_OAUTH_TOKEN` and no Claude Code config mount
- **AND** `toolbox` prints how to opt in with `--gh`, `--ssh`, `--claude` or a `use` line

#### Scenario: One kind by flag
- **WHEN** the operator runs `lolloctl toolbox --gh alfred lucius`
- **THEN** the container gets `GH_TOKEN` by name and git's credential helper for `https://github.com`, and neither the ssh-agent nor the Claude token

#### Scenario: Chosen once in the toolbox file
- **WHEN** `~/.config/lolloctl/toolbox` holds `use gh claude` and the operator runs `lolloctl toolbox alfred lucius`
- **THEN** the container gets the GitHub token and the Claude token and config, and not the ssh-agent

#### Scenario: Dropping the file's choice for one run
- **WHEN** the file holds `use gh ssh claude` and the operator runs `lolloctl toolbox --no-credentials alfred lucius`
- **THEN** no operator credential reaches the container

#### Scenario: Unknown kind
- **WHEN** the toolbox file holds `use gh aws`
- **THEN** `toolbox` stops with an error naming `aws` and the line

### Requirement: The release wrapper fetches only chosen credentials
In release mode, the wrapper SHALL run `gh auth token`, read the Claude keyring entry, and forward the ssh-agent only for the kinds chosen by the run's flags or the toolbox file's `use` line.

#### Scenario: Release mode with nothing chosen
- **WHEN** the released `lolloctl toolbox alfred lucius` runs with no flags and no `use` line on a host with `gh` logged in
- **THEN** the wrapper never runs `gh auth token`, and passes no `LOLLOCTL_GH_TOKEN`, `LOLLOCTL_CLAUDE_TOKEN` or ssh-agent mount to lolloctl's container

### Requirement: Risky workspace configuration stops the toolbox
Before starting, `toolbox` SHALL look for configuration that runs code on ordinary use in the workspace, in each git repository under it at any depth (folders such as `node_modules`, `.venv` and `vendor` included), and in the workspaces directory above it. If it finds any, it SHALL list each finding and refuse to start unless `--trust-workspace` is given. If the scan cannot complete, `toolbox` SHALL refuse the same way.

#### Scenario: A planted fsmonitor
- **WHEN** the workspace's `.git/config` sets `core.fsmonitor`
- **THEN** `toolbox` lists `.git/config: core.fsmonitor`, refuses to start, and names `--trust-workspace`

#### Scenario: Ordinary repositories pass
- **WHEN** the workspace holds a repository with a submodule, a linked worktree, Git LFS attributes and hooks, husky's `core.hooksPath`, a `gh` fork's remote and a pre-commit hook
- **THEN** `toolbox` starts, and git there runs none of those hooks

#### Scenario: A filter attribute
- **WHEN** a `.gitattributes` in the workspace assigns a `filter`, `diff` or `merge` driver other than Git LFS's
- **THEN** `toolbox` lists the file and refuses to start

#### Scenario: Project configuration for tools
- **WHEN** the workspace or a repository root holds `.claude/`, `.mcp.json`, `.envrc` or `.npmrc`
- **THEN** `toolbox` lists each one and refuses to start

#### Scenario: Only allow-listed git keys
- **WHEN** `.git/config` holds only keys such as `core.bare`, `remote.origin.url`, `remote.origin.fetch`, `branch.main.remote` and `user.email`
- **THEN** `toolbox` starts

#### Scenario: The operator trusts the workspace
- **WHEN** the workspace holds a `.mcp.json` and the operator passes `--trust-workspace`
- **THEN** `toolbox` lists the finding as a warning and starts

#### Scenario: A repository the walk prunes
- **WHEN** a repository under `vendor/`, or deeper than the walk's depth, sets `credential.helper` in its `.git/config`
- **THEN** `toolbox` lists it and refuses to start

#### Scenario: Configuration above the workspace
- **WHEN** an agent writes `.git/` or `.envrc` in the workspaces directory, above its own workspace
- **THEN** `toolbox` lists each one and refuses to start

#### Scenario: A git file pointing elsewhere
- **WHEN** a repository's `.git` is a file (`gitdir: ...`) rather than a directory
- **THEN** `toolbox` lists it and refuses to start, since the configuration it names was not read

### Requirement: The workspace scan runs nothing the agent wrote and reaches no credential
The scan SHALL run without the operator's credentials, without a network, with the workspaces mounted read-only, and without following links outside the mounted tree on the host. It SHALL only read and parse files, never run them.

#### Scenario: A symbolic link inside the workspace
- **WHEN** the workspace holds `notes -> /` and `.git/config -> /etc/passwd`
- **THEN** the scan does not read anything on the host outside the workspaces directory, and lists the linked `.git/config` as a finding

### Requirement: Git inside the toolbox ignores repository-local executables
`toolbox` SHALL pass, as Git command-scope configuration, settings that disable `core.fsmonitor`, repository hooks (`core.hooksPath`), `core.sshCommand`, the repository's credential helpers (an empty `credential.helper` before the operator's own) and the `ext` transport, whatever the workspace or `--trust-workspace` says. It SHALL also stop git's search for a repository at the workspaces directory (`GIT_CEILING_DIRECTORIES`).

#### Scenario: A hook appears after the scan
- **WHEN** an agent adds `core.fsmonitor` and a `pre-commit` hook after the scan passed, and the operator then runs `git status` and `git commit` in the toolbox
- **THEN** git runs neither the fsmonitor command nor the hook

#### Scenario: A credential helper of the agent's
- **WHEN** a repository's config names a `credential.helper` and the operator runs `git push` in the toolbox with `--gh`
- **THEN** git asks only gh's helper for the GitHub credential

#### Scenario: A repository above the workspace
- **WHEN** the workspaces directory holds a `.git` and the operator runs git in a workspace that is not a repository
- **THEN** git does not use the repository above

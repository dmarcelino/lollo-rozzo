# Proposal

## Why

`lolloctl toolbox` brings the operator's GitHub token, ssh-agent and Claude token into a container whose working directory an agent wrote. A compromised agent can then steer the bind mount (an unvalidated agent id and a symlinked workspace, SEC-004), or plant repository and project configuration that runs on the first `git status` and sends the tokens out (SEC-005). That is the step where one agent's stack reaches the operator, and through the operator's GitHub token the release pipeline (the audit's chain 3). Separately, `lolloctl` prints agent names, ids and MCP server names read from the app-server without filtering them, so an agent can put terminal escape sequences into the operator's terminal and mislead them during an investigation (SEC-028).

## What Changes

- `toolbox` (and `shell <name> <agent>`) accepts an agent id from the app-server only if it matches `^agent-[A-Za-z0-9-]{1,80}$`. It refuses a workspace that is a symbolic link, mounts the fixed parent `state/workspaces` at `/work`, and starts in `/work/<id>`, so a link resolves inside the container, never on the host.
- **BREAKING (for operators):** each operator credential becomes opt-in. That covers the GitHub token and the git credential helper (`gh`), the ssh-agent (`ssh`), and the Claude token and Claude Code config (`claude`). They are chosen per run with `--gh`, `--ssh` and `--claude`, or once with a `use gh ssh claude` line in `~/.config/lolloctl/toolbox`. `--no-credentials` drops the file's choice for one run. With nothing chosen, `toolbox` says how to opt in. In release mode the wrapper fetches only the credentials that were chosen.
- Before `docker run`, `toolbox` scans the workspace for configuration that runs code on ordinary use: `.git/config` keys outside an allow-list, `filter`, `diff` and `merge` attributes, and `.claude/`, `.mcp.json`, `.envrc`, `.npmrc`. It refuses to start and lists what it found, unless `--trust-workspace` is given. The scan runs in a throwaway container with no network and no credentials, so nothing it reads can reach the host.
- As a second layer, the toolbox forces git settings that neutralise the common repository-local hooks (`core.fsmonitor`, `core.hooksPath`, `core.sshCommand` and others) through Git's command-scope configuration.
- A shared output rule: every value `lolloctl` reads from a container and prints has its control and bidirectional-override characters replaced with visible escapes. That covers agent names and ids, MCP server names, and the error output of its non-interactive calls into the app-server.

## Capabilities

### New Capabilities
- `cli/toolbox`: what `lolloctl toolbox` mounts, which operator credentials it brings in and when, and when it refuses to start because of the workspace it was asked to open.
- `cli/terminal-output`: how `lolloctl` treats text that comes from a stack's containers before it reaches the operator's terminal.

### Modified Capabilities
<!-- None: the CLI has no specs yet. -->

## Impact

- **Code:** `lolloctl` (`find_agent`, `open_agent`, `cmd_shell`, `cmd_toolbox`, `toolbox_builtins`, `toolbox_personal`, the MCP registration messages, and the non-interactive `compose exec` calls into the app-server), `lib/common.sh` (a `printable` helper), `release/lolloctl-wrapper.sh` (`toolbox_host`).
- **Tests:** new `test_toolbox_*` and `test_printable_*` cases in `tests/run.sh`, and existing `test_toolbox_*` cases updated for opt-in credentials and the parent mount. Shellcheck covers the wrapper.
- **Docs:** `docs/operations.md` ("Working on an agent's workspace with your own tools"), `lib/README.md` (the `toolbox` rule, plus a new output rule), `CLAUDE.md` (the `toolbox` directive), and `lolloctl`'s usage text.
- **Operators:** whoever used `toolbox` with credentials adds one `use` line, or passes flags. Whoever opens a workspace with project configuration either reviews it or passes `--trust-workspace`. The toolbox also sees the stack's other agents' workspaces, which those agents already share.
- **No change** to stacks, containers, the app-server or any sidecar. Nothing is recreated.

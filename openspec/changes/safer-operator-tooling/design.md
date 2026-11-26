# Design

## Context

Today's `toolbox` code is in `lolloctl`, from `find_agent` to `cmd_toolbox`:
- `find_agent` takes the id from `letta agents list` run inside the app-server, where agents are root.
- `open_agent` builds `ws=$LETTA_STATE_DIR/workspaces/$id`, tests it with `[[ -d ]]`, and `cmd_toolbox` mounts it with `-v "$ws:/work/$id"`.
- `toolbox_builtins` brings in, whenever the host has them: the ssh-agent socket, `~/.ssh/known_hosts`, `~/.gitconfig`, a `gh` token with a credential helper (set through `GIT_CONFIG_COUNT`), Claude Code's config files, and a Claude token.
- In release mode, `toolbox_host` in `release/lolloctl-wrapper.sh` runs `gh auth token` and the keyring lookups on the host, and forwards the ssh-agent, before lolloctl's container starts. It also runs the toolbox file's `secret` lines.

The lolloctl image has no `git`, and lolloctl's required commands are `docker`, `curl`, `jq`, `openssl`, `dig` and `tar`. The app-server image has `git` and `find`. That image is built or pulled by lolloctl, and agents cannot change it: they write only the running container's layer and the bind mounts.

The motivation is in proposal.md. The behaviour is in `specs/cli/toolbox` and `specs/cli/terminal-output`.

## Goals / Non-Goals

**Goals:**
- An agent cannot choose what the toolbox mounts.
- An operator credential is in the toolbox only when the operator chose it.
- Configuration that runs code on ordinary use is shown to the operator before any credential meets it.

**Non-Goals:**
- Making it safe to run agent-written code with credentials. Package scripts, Makefiles, test suites and `--tools` stay the operator's judgment, as `docs/operations.md` says today.
- Filtering interactive sessions or `logs`.
- Any change to the stacks, their containers or `stack.env`.

## Decisions

### 1. Validate the id where it enters
`find_agent` checks the matched id against `^agent-[A-Za-z0-9-]{1,80}$`, the same pattern the landing service uses, before returning it. Both `toolbox` and `shell <name> <agent>` get the check from that one place.
- *Alternative:* `realpath` checks before the mount. Rejected: an agent that is root over the directory can swap it between the check and the mount.

### 2. Mount the parent, not the workspace
`-v "$state/workspaces:/work" -w "/work/$id"`. A symbolic link at `workspaces/<id>`, or anywhere under it, then resolves inside the container's own filesystem, never on the host. `toolbox` refuses a workspace or `workspaces` directory that is a link already when it looks, which gives a clear message. A link created after that check gains nothing, because the bind source is the fixed parent.
- *Cost:* the toolbox sees the stack's other agents' workspaces, which those agents already read and write.

### 3. Credentials are three kinds, opted in by flag or by a `use` line
- **`gh`:** the GitHub token and the credential helper.
- **`ssh`:** the ssh-agent socket.
- **`claude`:** the Claude token plus the Claude Code config mounts. `settings.json` can carry `env` secrets and hooks, so one switch covers both.

`~/.ssh/known_hosts` and `~/.gitconfig` stay unconditional and read-only. They are configuration, not credentials, and taking them away would change what git does for every operator.

How a kind is chosen:
- The toolbox file gains `use KIND...`, parsed like its other directives, never sourced. An unknown kind is an error.
- `--gh`, `--ssh` and `--claude` add a kind for one run. `--no-credentials` ignores the file's `use` lines for one run, which suits a look at a suspicious workspace.
- The file's `secret`, `mount` and `env` lines keep working as today: the operator wrote each one, so they are opt-in already.
- With nothing chosen, `toolbox` prints one line naming the flags and the `use` line, and the existing warning about a missing Claude token appears only when `claude` was chosen.

*Alternatives:*
- Flags only. Rejected: daily friction, and operators would put the flags in a shell alias anyway.
- An interactive prompt. Rejected: `toolbox` also runs a command non-interactively.

### 4. The wrapper decides from the same inputs, before it fetches anything
`toolbox_host` reads the toolbox flags from the wrapper's arguments: the leading options before the stack name, using its existing `positional` handling. It also reads the `use` lines from the file it already checks for owner and mode. It runs `gh auth token`, the keychain or `secret-tool` lookup, and the ssh-agent forwarding (including Colima's tunnel) only for chosen kinds.

The grammar is small, so it is written twice, once in each script. Tests drive both with the same cases, so they cannot drift.

### 5. The scan runs in a throwaway, credential-less container of the app-server image
The command is `docker run --rm --network none --read-only --cap-drop ALL --cap-add DAC_READ_SEARCH --security-opt no-new-privileges -v "$state/workspaces:/work:ro"`, with the image's `sh`, `find` and `git` and a fixed script that lolloctl sends on stdin, as `SKILLS_INSTALL` is sent today.
- Git's own parser reads `.git/config`: `git config --file F --name-only --list`, which ignores `include.*` when given a file. A hand-written parser in lolloctl could disagree with git and hide a key.
- Links resolve inside that container, so nothing on the host outside the workspaces directory is read.
- The script prints findings separated by NUL. lolloctl filters them for display (decision 8).
- A non-zero exit, a parse error, or an unreadable file counts as a finding, so the toolbox refuses.

*Alternatives:*
- Parse on the host with `awk`. Rejected: parser differential, and links are harder to handle safely.
- Add `git` to the lolloctl image. Rejected: it grows the image for one check, and the container approach also settles links.

### 6. What the scan looks at
The script walks the workspace with `find -P` to depth 6. It prunes `node_modules`, `.venv` and `vendor`, and stops at 50,000 entries; past the limit the scan is incomplete, which is a finding. Depth matters because agents keep several repositories in one workspace, and git and Claude Code run in subdirectories.

Repositories are searched for apart from that walk, at any depth and in the pruned folders too, since git reads the configuration of whichever repository it is used in. That search only lists names, so a time limit (60 seconds) bounds it instead of the entry cap; a search that does not finish in time, or more than 50,000 repositories, leaves the scan incomplete.

The toolbox mounts the whole workspaces directory at `/work`, which every agent can write. The scan reports `.git`, `.gitattributes`, `.gitmodules`, `.claude/`, `.mcp.json`, `.envrc` and `.npmrc` there, and the toolbox sets `GIT_CEILING_DIRECTORIES=/work`, so git never takes `/work` for a repository from a workspace that is not one.

For each `.git` it finds:
- **A file** (`gitdir:`): a finding.
- **A directory:**
  - Keys in `config`, and in `config.worktree` if present, outside the allow-list. The allow-list covers repository format and core flags (`repositoryformatversion`, `filemode`, `bare`, `logallrefupdates`, `ignorecase`, `precomposeunicode`, `symlinks`, `autocrlf`, `eol`); `remote.*.{url,pushurl,fetch,tagopt,prune}`; `branch.*.{remote,merge,rebase,pushremote}`; `user.{name,email}`; `pull.*`; `push.default`; `push.autosetupremote`; `init.defaultbranch`; `fetch.prune`; `extensions.{objectformat,worktreeconfig}`; and `lfs.repositoryformatversion`.
  - Any remote URL whose transport is `ext::` or `fd::`.
  - Files in `hooks/` not ending in `.sample`.
  - Any `filter=`, `diff=` or `merge=` driver in `info/attributes`.
- **At the repository root:** `.gitattributes` lines that assign a `filter`, `diff` or `merge` driver. Built-in values (`binary`, `text`, `union`, a bare `diff` or `-diff`) are allowed. Also `.gitmodules` entries with `update`.

At the workspace root and at each repository root, the presence of `.claude/`, `.mcp.json`, `.envrc` or `.npmrc` is a finding.

The allow-list and depth live in one place in `lolloctl`, with tests. Widening the allow-list is a reviewed change.

### 7. Forced git settings as a second layer
The toolbox's `GIT_CONFIG_COUNT` block grows from the credential-helper entries to also set:
- `core.fsmonitor=false`
- `core.hooksPath=/dev/null`
- `core.sshCommand=ssh`
- `protocol.ext.allow=never`
- `core.pager=less`
- `credential.helper=` (empty), before gh's own helper when `gh` is chosen

Command scope beats repository config for these single-valued keys, and an empty `credential.helper` empties the list of helpers the repository's config built, so a repository the scan missed still never receives the GitHub token through a helper of its own. They are set whatever `--trust-workspace` says, so that flag means "start anyway", not "run my hooks". An operator who wants hooks can run `git -c core.hooksPath=.git/hooks ...` on purpose.

### 8. `printable`: one jq filter at the point of reading
`printable` lives in `lib/common.sh` and uses jq, which is already required. jq works on Unicode code points, so it can replace C0 controls, DEL, C1 controls (U+0080 to U+009F) and the bidirectional controls (U+202A to U+202E, U+2066 to U+2069) with `\xNN` or `\u{NNNN}` escapes, and leave other non-ASCII text alone.
- A byte-wise `tr` is rejected: C1's raw bytes are UTF-8 continuation bytes, so it would garble names.

Values are filtered where lolloctl reads them for display:
- the name column in `find_agent`, and the agent list in its error message
- the `added`, `present`, `conflicts` and `removed` lists from `mcp-servers.mjs`

Ids are compared before filtering, and they are pattern-checked anyway. For the non-interactive `compose exec` calls into the app-server (`find_agent`, `sync_skills`, the executor install and uninstall, `mcp_servers`), stderr goes to a temporary file and is printed through `printable` after the call returns. The exit status is unchanged.

### 9. Option parsing
`cmd_toolbox` reads leading options in any order until the first word that does not start with `--`: `--tools[=rw]`, `--gh`, `--ssh`, `--claude`, `--no-credentials` and `--trust-workspace`. An unknown option prints the usage. The usage line in `lolloctl`'s help lists them.

## Risks / Trade-offs

- **[Scan-then-start race]** An agent can watch its workspace for reads (inotify sees the scan's opens through the bind mount, on the same inodes) and add configuration in the seconds between the scan and the toolbox starting.
  - Covered by decision 7: fsmonitor, hooks, `sshCommand`, the `ext` transport and the pager.
  - Not covered: filter, diff and merge drivers with names the agent chooses, `.claude/` settings, `.mcp.json`, `.envrc` and `.npmrc`. That residual risk goes in `docs/operations.md`'s limits. Credentials being opt-in is what keeps most runs safe: a run without credentials has nothing to lose.
  - Snapshotting the workspace was rejected, because the operator's edits must land in the live workspace.
- **[Friction]** Workspaces with a legitimate `.claude/` or `.envrc` refuse every time. Mitigation: the refusal lists the files and names `--trust-workspace`. The operator decides with the evidence on screen.
- **[Operators lose credentials silently]** The first run after upgrading has no token. Mitigation: `toolbox` names the flags and the `use` line whenever nothing is chosen, and the release notes say so.
- **[Scan cost on large workspaces]** The depth and entry limits bound it, and an incomplete scan refuses rather than passes.
- **[Allow-list too narrow]** A harmless key, `core.editor` set by an agent for example, stops the toolbox. Mitigation: `--trust-workspace`, and the allow-list can grow by review. Defaulting to refuse is the point.
- **[Two parsers for the `use` grammar]** The wrapper and lolloctl could drift. Mitigation: shared test cases in `tests/run.sh`, and the grammar is a single word list.

## Migration Plan

- **Ships in one release.** No stack changes and nothing is recreated.
- **Release notes:**
  - Add `use gh ssh claude` (or the subset you need) to `~/.config/lolloctl/toolbox`, or pass the flags.
  - A workspace with project configuration now needs `--trust-workspace`.
- **Rollback:** the previous release's `lolloctl` behaves as before. The `use` line is an unknown directive there, so remove it before rolling back.

## Open Questions

- The exact depth and entry limits (6 and 50,000) can be tuned after trying them on a real workspace, without changing the specs or tasks.

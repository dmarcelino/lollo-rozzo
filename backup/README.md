# backup

The image `lolloctl backup` copies a stack with, and `lolloctl restore` puts one back with: `lollo-backup:local`, Debian slim plus rsync (tar and gzip come with the base). The state under `stacks/<name>/state/` is root-owned, so `backup` reads it, and `restore` writes it, as root inside a throwaway container of this image instead of with sudo on the host. No profile: every stack can be backed up. How to back up and restore is in [docs/operations.md](../docs/operations.md#backups).

![lolloctl backup, step by step](../docs/diagrams/backup-workflow.svg)

An interactive version, [backup-workflow.html](../docs/diagrams/backup-workflow.html), opens in a browser from a clone of this repo.

## Files
| File | What it does |
|---|---|
| `Dockerfile` | `FROM debian:bookworm-slim`, installs `rsync`. Nothing else: the copy scripts come from `lolloctl` (`BACKUP_COPY`, `RESTORE_COPY`) at run time |

## How it plugs in
The image is built with plain `docker build` (`build_backup_image`), never by Compose, so its containers carry no Compose labels and `stale_services` never counts them. `cmd_backup` rebuilds it on every run (cached layers make that cheap); `ensure_backup_image` builds it only when it is missing.

Every container of it runs with `BACKUP_RUN`: `--rm --init` (as PID 1, sh would ignore the signal Docker forwards on Ctrl-C), `--network none`, `--read-only` with a tmpfs `/tmp`, `no-new-privileges`, and `--cap-drop ALL` plus only `CHOWN`, `DAC_OVERRIDE` and `FOWNER`: read everything, write into the operator's directory, and hand the copies to the operator.

`lolloctl backup <name|all> <dir>`:
1. Refuses a destination that is not a directory, contains `:` or `,`, or lies inside this repo (it is public, and a backup holds `stack.env` and the agents' logins), and needs `flock`.
2. Runs `backup_stack` for each stack in its own subshell, so one failing stack does not stop the others; the failed ones are listed at the end and the command exits 1.
3. Takes `flock -n` on `<dir>/<name>/.lock`; a second backup of the same stack stops there, so it cannot delete the first one's `.partial-*`.
4. Mounts, read-only: the state at `/src/state`, `stack.env` at `/src/stack.env`, `config.env` at `/src/config.env`, the host's Docker platform (`linux/amd64`, `linux/arm64`) at `/src/platform` from a `.partial-<TS>.platform` file removed when the stack's subshell exits, and `lollo-<name>_vault-data` at `/src/vault`, `lollo-<name>_pipelock-data` at `/src/pipelock`, `lollo-<name>_news-radar-data` at `/src/news-radar` and `lollo-<name>_notifications-data` (the devices' push subscriptions) at `/src/notifications` when the volume exists (checked with `volume inspect` first, since `run -v` would create a missing one), whatever the profiles. With `--with-google`, also `lollo-<name>_google-policy` and `lollo-<name>_google-creds` at `/src/google-policy` and `/src/google-creds`, each when it exists. It leaves out caches, upstream's skills (the BFF reinstalls them), the skills `lolloctl skills` installed, and with `--no-tools` `state/tools`.
5. Removes any `.partial-*` an interrupted run left, from inside a container (a snapshot can hold read-only directories the operator cannot empty).
6. For a running stack, asks: pause, copy live, or abort; `--pause` or `--live` answers ahead of time. With pause, the `EXIT` trap that unpauses the stack is set before `compose pause`, so a pause that fails partway is undone too.
7. Copies with `rsync -aH --numeric-ids --chown=<operator uid:gid>` into `<dir>/<name>/.partial-<TS>` (`<TS>` is the UTC start time, `YYYYMMDDTHHMMSSZ`), with `--link-dest` to the newest earlier snapshot, so unchanged files are hard links. rsync's code 24 (files vanished, as in a live copy) is a warning.
8. Renames `.partial-<TS>` to `<TS>` only when the copy succeeded, so `--link-dest` and `--keep` never pick a broken one. With `--keep N`, `backup_prune` deletes all but the newest N snapshots, from inside a container.

With `--compress`, step 7 writes a tar.gz of the same tree to stdout instead, with numeric owners and no `--link-dest`, into `.partial-<TS>.tar.gz` (mode 600), renamed when complete.

`lolloctl restore <name> <backup>` is the reverse, for one snapshot directory or one archive:
1. Refuses what is not a backup (a directory without `stack.env` and `state/`, or a file that is not a `.tar.gz` holding them), an archive with an entry that is absolute or goes up with `..` (step 3 unpacks names as written), a backup lolloctl's container cannot see (`require_mounted`), a backup of another stack (the first line of its `stack.env`, `# Lettuce stack "<name>"`, names it: its tunnel and Cloudflare ids are that stack's), and, with `flock -n` on `stacks/<name>/.restore.lock`, a second restore of the same stack. It reads `stack.env`, `config.env` and `platform` out of the backup first, into a private directory in the data directory. When `platform` names another architecture than this host's, it leaves the backup's `state/tools` out and keeps the tools here: agents' tools are native programs, which would fail with "exec format error". A backup older than `platform` is taken as this host's. It needs no `init`, so it runs first on a new host.
2. Refuses a stack that has processes: running, paused or restarting (`stack_live`). When the stack already has data here, lists what it replaces and asks for the stack's name; `--yes` answers ahead of time, and without a terminal and without `--yes` it stops.
3. Runs `RESTORE_COPY` once, in a container of this image with `BACKUP_RUN`'s limits, the backup mounted read-only and the state and each volume the backup holds mounted writable under `/r` (`BACKUP_VOLUMES`, the table `backup` reads too: the vault, pipelock, News Radar, the notifications service (owner 10005) and the two Google volumes, each with its service's owner). It makes each destination match: `rsync --delete` from a snapshot, with the owner given (a snapshot keeps none), or, from an archive, emptying them all and extracting every entry in one pass over it, with their owners. It extracts with `-P`, so tar makes a link to an absolute path or through `..` at once: without it tar first writes an empty mode-0 file in its place, and Colima's virtiofs share on macOS refuses to open that, even to root. A backup without `state/tools` leaves the tools there alone. A missing volume is first created with Compose's `com.docker.compose.project` and `com.docker.compose.volume` labels, so Compose takes it as its own instead of warning that it exists.
4. Writes `stack.env` last (a failed copy leaves the old one) with `LETTA_STATE_DIR` set to this host's state directory, and `config.env` only when the host has none. When `stacks.conf` lists the stack, `ALLOWED_USERS` becomes its users, which `users` and `apply` keep in step with the Access policy; when it does not, the stack's line is written from the backup's `ALLOWED_USERS`. It makes no Cloudflare call and starts nothing.

The same image fills the document tools' volume: `fill_doc_tools_volume` (under `ensure_doc_tools_volume`) streams the tools out of the `doc-tools` image with `docker cp` into a container of this image with `BACKUP_RUN`'s limits, which unpacks them into the volume and writes the completion marker last ([docs/doc-tools.md](../docs/doc-tools.md)).

`backup` and `restore` touch no Compose file and are not part of `check_stack`.

## Rules

### `backup` and `restore` touch the state only through their own container
The state is root-owned and holds every stack's secrets, so the operator's shell never reads or writes it directly and no sudo is needed. Every copy goes through a container of this image with `BACKUP_RUN`'s limits (above): `--init` so Ctrl-C reaches rsync, `--network none`, a read-only root, and only the capabilities needed to read, write and chown. `backup` mounts the state, `stack.env`, `config.env` and the volumes read-only; `restore` mounts the backup read-only.

- **The destination may not be inside this repo:** the repo is public, and backups hold secrets.
- **Snapshots are complete or absent.** They are written as the operator (`--chown`), hard-linked to the previous one (`--link-dest`), and renamed from `.partial-<TS>` only when complete. A `flock` on `<dir>/<name>/.lock` keeps a second run from deleting the first one's `.partial-*`.
- **The vault, pipelock's receipts, News Radar and the notification subscriptions are copied whenever their volume exists**, whatever the profiles. A restored stack's devices keep getting the lollo's notifications without enrolling again, since the subscriptions and the VAPID pair they were made with (`LOLLO_VAPID_*`, in `stack.env`) come back together. Check `volume inspect` before mounting one, since `run -v` creates a missing volume.
- **Lettuce's Google volumes only with `--with-google`.** A refresh token opens the person's mail and Drive, and reconnecting costs them a minute, so routine backups leave them out; a one-off copy for moving hosts takes them. The Cloudflare API token (`.secrets/`) is never copied: it is the most powerful secret on the host, and a new one is cheap.
- **`restore` refuses a running stack**, makes the destination match the backup, writes `stack.env` last, and never overwrites a `config.env`.
- **A running stack is copied only after `--pause`, `--live` or the operator's answer**, and the `EXIT` trap that unpauses it is set before pausing.
- **With `all`, each stack runs in its own subshell**, and a failure does not stop the rest.

## Build and test
```bash
tests/run.sh test_backup_    # backup's offline tests (docker is stubbed)
tests/run.sh test_restore_   # restore's, including a round trip of both scripts with the host's rsync and tar
tests/run.sh                 # all offline tests
```
The image itself has no tests; `docker build -t lollo-backup:local backup` builds it by hand.

## Upstream seams
- `upstream/docker/agent-skills/`: `backup` leaves out a skill of each name there, since the BFF rewrites upstream's skills on every connect.

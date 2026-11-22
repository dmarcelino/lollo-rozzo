# release

What a lollo-rozzo release is made of, and how `publish.yml` makes one. A release is the `lolloctl` image (the CLI and every file it reads at run time), the lollo's published images, and a wrapper script pinned to the `lolloctl` image's digest, which admins put in their data directory. Installing and upgrading, for admins: [docs/operations.md](../docs/operations.md#installing-and-upgrading). Why it is built this way: [lib/DESIGN.md](../lib/DESIGN.md).

![Release and dev builds, from a tag or a pull request to an admin's self-upgrade](../docs/diagrams/release-workflow.svg)

An interactive version, [release-workflow.html](../docs/diagrams/release-workflow.html), opens in a browser from a clone of this repo.

## Files
| File | What it does |
|---|---|
| `images` | The images `publish.yml` publishes, one per line: the name, the build context, the shared context (`lollo-common`) and the Compose service. lolloctl, `content-tag` and `publish.yml` all read it. |
| `content-tag` | `release/content-tag <image>` prints the image's content tag: a hash of its build context and shared context (docs, tests, caches and the top-level names its `.dockerignore` lists left out), its name, and `../REBUILD`. |
| `dev-tag` | `release/dev-tag <branch>` prints `vX.Y.Z-dev.<slug>`, the name of the branch's dev builds without their number ([Dev builds](#dev-builds)). |
| `lolloctl.Dockerfile` | The `lolloctl` image: Debian with `curl`, `jq`, `openssl`, `dig` and `flock`; the Docker CLI, Compose and Buildx from Docker's own `docker:<version>-cli` image, pinned by digest; and this repo's runtime files, with `RELEASE`. Built from the repo root. |
| `lolloctl.Dockerfile.dockerignore` | What the `lolloctl` image takes from the repo (an allowlist). |
| `NOTES.md` | The highlights of the next release, which `publish.yml` puts at the top of its notes, RCs included. No version in its name, so changing the version changes nothing in it. It does not exist between a final release and the next release's first tag. |
| `lolloctl-wrapper.sh` | The wrapper, with `@VERSION@`, `@IMAGE@` and `@REPOSITORY@` for `publish.yml` to fill in. It runs the image with the data directory, the Docker socket (on macOS, the VM's `/var/run/docker.sock` and its group there), the directories `backup` and `receipts` write to, and the backup `restore` reads (read-only), mounted at the same path; for `toolbox`, the operator's home read-only, the ssh agent (on macOS, through Docker's VM) and what only the host's commands give; and it does `self-upgrade`. The `lolloctl` image carries it too, so `self-upgrade` can fill in a dev build's. It runs on Linux and macOS (bash 4+, BSD or GNU tools), and needs no Docker Desktop, Colima or OrbStack setting of its own. |

`VERSION` (the release's version) and `REBUILD` (a date; changing it rebuilds every image on fresh base images) are at the repo root. `RELEASE`, written by `publish.yml` and baked into the `lolloctl` image, holds the version, the Lettuce tag the release was tested with (`config.env.example`'s `LETTUCE_REF`), the repository, and each published image's full name, `ghcr.io/<owner>/lollo-<image>:<content tag>@sha256:<digest>`; it is also attached to the GitHub release.

## How it plugs in
- **Release mode.** lolloctl runs in release mode when `RELEASE` exists ([lib/README.md](../lib/README.md#release-mode-is-a-file-not-a-setting)): it names the published images from it, builds only the two it cannot pull (the document tools and the code editor's shell), and checks that every host path is mounted at the same path in its container.
- **The overlays** name each published image `${LOLLO_IMAGE_<IMAGE>:-lollo-<image>:local}`, so a clone builds and runs its own ([compose/README.md](../compose/README.md)).
- **`publish.yml`** runs on a `vX.Y.Z` or `vX.Y.Z-rc.N` tag on a commit of `main` or of a `hotfix/*` branch, and for a pull request's dev build ([Dev builds](#dev-builds)), after every job of `ci.yml` on the commit it publishes. Each image whose content tag is not in the registry yet is built once per platform, each on a runner of that architecture (`ubuntu-24.04` for `linux/amd64`, `ubuntu-24.04-arm` for `linux/arm64`, never under emulation) and pushed by digest with an SBOM and BuildKit provenance; one job per image then puts the content tag on both digests and adds a GitHub attestation. Then `RELEASE`, the `lolloctl` image (built the same way), the wrapper, `SHA256SUMS`, the notes (`NOTES.md`'s highlights, when it has any, then which images changed since the last release) and the GitHub release.
- **The `release` skill** ([.agents/skills/release/SKILL.md](../.agents/skills/release/SKILL.md), for Claude Code through `.claude/skills/release`, and for Codex) writes `NOTES.md` from the PRs merged since the last full release, commits it, pushes the tag, and after a final release starts the next version.

## Rules

### Images are named by their content, never by the release
Compose recreates a container when its image's name changes, even for the same image, and recreating the vault locks it. A release reuses every image whose content tag is already published, so it keeps its name and digest, and only the containers whose image changed restart. Never tag a published image by the release version, and never push to an existing content tag.

### Two images are never published
`doc-tools` (its PDF server uses PyMuPDF, AGPL-3.0: publishing the image would make it AGPL) and `code-server/exec` (built `FROM` the app-server's image, so one per Lettuce tag) are built on the host; their contexts ship in the `lolloctl` image. Keep them out of `images`.

### Nothing moves after a release
No `latest`, nothing published from `main` without a tag: the `lolloctl` image holds the Docker socket, so a moving tag would let one push reach every host. The wrapper names the image by digest, and `RELEASE` names every other image by digest. A dev build's tag is new too: `publish.yml` takes the next number and refuses one that exists, or one the registry cannot say is free.

### Releases come from `main` or a `hotfix/*` branch
`publish.yml`'s `plan` refuses a `vX.Y.Z` or `vX.Y.Z-rc.N` tag on a commit that is on neither, which catches a tag put on the wrong commit by mistake. It is not a lock: a tag runs the `publish.yml` of the commit it names, which a branch can change, so only a tag ruleset in the repository's settings stops it. A branch's builds to try are dev builds.

### A dev build never writes to the repository
Its last job, `dev`, has no `contents: write`: only `release` does, and only for a tag. A fork's pull requests are not built (their token cannot publish anyway), nor Renovate's, which would publish one build per dependency update; a Renovate branch can be built by hand. `test_publish_` checks all three.

### Base images get fixes through `REBUILD`
Reused images keep their base images. To pick up their security fixes, change `REBUILD` to the day's date in a release of its own: every content tag changes, every sidecar restarts and every vault locks, and the release notes say so.

### Never `[ci skip]` a commit that gets tagged
GitHub skips a push's workflows when its commit message has `[ci skip]`, and that can take the tag's `publish.yml` with it. The notes commit a tag points at goes without it: `ci.yml` ignores a push that changes only `NOTES.md`, and `publish.yml` runs the checks itself. Only the commit that starts the next version, never tagged, carries `[ci skip]`.

### What the image carries is what lolloctl reads
A file lolloctl reads at run time (a compose file, the browser engine's seccomp profile, which Compose reads itself, a skill, the executor, a build context built on the host) goes into `lolloctl.Dockerfile` and its `.dockerignore`, or a release install cannot find it.

## Cutting a release
The `release` skill does steps 2 to 4 (`/release rc`, `/release final`); without an agent, do them by hand.

1. Make sure `VERSION` is the version to release (and set `REBUILD`, for a rebuild release) on `main`, and that `config.env.example`'s `LETTUCE_REF` is the Lettuce tag you tested with ([CONTRIBUTING.md](../CONTRIBUTING.md#upgrading-lettuce_ref)).
2. Write the highlights in `NOTES.md`: what is new for people and admins since the last full release, breaking changes and what to do about them first among the rest. They are a summary: at most five items, one bullet each with no sub-bullets; the PRs hold the details. Commit it to `main`, without `[ci skip]`.
3. Tag that commit: `git tag vX.Y.Z && git push origin vX.Y.Z`. For a candidate to try on a test host first, `vX.Y.Z-rc.N`, published as a pre-release; fix `NOTES.md` between candidates. The tag goes on `main`, or for a hotfix on a `hotfix/*` branch made from the release's tag, with `VERSION` set to the patch there; `publish.yml` refuses a tag elsewhere, as a check against mistakes ([the rule](#releases-come-from-main-or-a-hotfix-branch)). The `release` skill cuts from `main` only; tag a hotfix by hand.
4. After a final release, start the next version: set `VERSION` to the next minor, delete `NOTES.md`, and commit both as `Set VERSION to X.Y.Z [ci skip]`. A smaller next version is chosen at its first tag.
5. Check that hosts can pull without credentials: logged out of `ghcr.io`, `docker manifest inspect` each image `RELEASE` names and the wrapper's `lolloctl` image. The packages `publish.yml` creates from this public repository come out public (as for `v0.1.0-rc.1`); one that does not is made public in its package settings.

## Dev builds
A dev build lets people try a pull request before it is released. `publish.yml` makes one for every pull request from a branch of this repository but Renovate's, when it opens and on every push to it, and for any branch but `main` by hand (Actions, "Publish", "Run workflow"). A branch's builds run one at a time: a new push waits for the running one, and replaces any build still waiting.

- **What is published.** The same images as a release (an unchanged one reused by its content tag), and one more tag: `ghcr.io/<owner>/lolloctl:vX.Y.Z-dev.<slug>.<N>`. `X.Y.Z` is the branch's `VERSION`, `<slug>` comes from the branch's name (`dev-tag`), and `<N>` is the first number not yet published. The image carries `RELEASE` with that version, and is attested like a release's. No git tag, no GitHub release, no wrapper download: the history and the Releases page stay clean.
- **How it is installed.** `lolloctl self-upgrade vX.Y.Z-dev.<slug>.<N>`, the command the run's summary prints. The current `lolloctl` image pulls the dev build's image from this release's own repository, pins its digest, checks that its `RELEASE` is that build's, and fills in the wrapper template the image carries, as `publish.yml` fills a release's (`fetch_dev_wrapper`). The rest is a release's `self-upgrade`: the new wrapper pulls its image, replaces the old one (kept as `bin/lolloctl.previous`), and runs `up all` and `skills all`, moving Lettuce forward to the build's tested tag. `self-upgrade vX.Y.Z` goes back to a release.
- **Which hosts can.** `self-upgrade` runs the host's current `lolloctl`, so a host installs dev builds only once it runs a release that has this code; an older one refuses the tag with its usage message.

## Build and test
```bash
tests/run.sh test_content_tags        # content-tag
tests/run.sh test_every_published     # every image in images has its overlay variable
tests/run.sh test_publish_            # publish.yml fetches each image's own digests
tests/run.sh test_wrapper_            # the wrapper, with docker stubbed
tests/run.sh test_release_            # lolloctl in release mode
shellcheck -x release/content-tag release/dev-tag release/lolloctl-wrapper.sh
```
To try the image itself, write a `RELEASE` at the repo root (it is gitignored), `docker build -f release/lolloctl.Dockerfile -t <name> .`, delete `RELEASE`, and run a wrapper filled in with `<name>` from a data directory's `bin/`. `publish.yml` is checked by an `-rc` tag on a fork, and its dev path by a pull request there.

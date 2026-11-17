---
name: release
description: Use when the user asks to cut, tag or publish a lollo-rozzo release or release candidate ("release 0.4.0", "tag an RC", "cut v0.4.0-rc.2", "publish the release"), or to write the release notes for one. Writes the highlights into release/NOTES.md from the PRs merged since the last full release, commits them, pushes the tag that publish.yml publishes, and after a final release bumps VERSION and deletes release/NOTES.md.
---

# Release

Cuts a release as [release/README.md](../../../release/README.md#cutting-a-release) describes,
with the highlights at the top of its notes. `publish.yml` runs on the tag; its notes are
`release/NOTES.md` (the highlights), then what it generates (the tested Lettuce tag, how to
install, which images changed). `release/NOTES.md` has no version in its name: it holds the
highlights of whatever release comes next, and it does not exist between a final release and
the next release's first tag.

Invoking this skill is the request to commit to `main` and push the tag, but the notes are
published text: show them and every command that writes to the remote, and wait for a yes
before running any of them.

## Arguments
- `rc` (the default): the next release candidate of `VERSION`.
- `final`: the full release of `VERSION`.
- An explicit tag, `vX.Y.Z` or `vX.Y.Z-rc.N`. When X.Y.Z is not `VERSION` (say the release
  turned out smaller, 1.0.0 becomes 0.5.0), the release commit sets `VERSION` too.

## 1. Check the starting point
```bash
git fetch origin --tags
git status --porcelain            # must be empty
git rev-parse --abbrev-ref HEAD   # must be main
git rev-parse HEAD origin/main    # must be equal
```
Stop and say why if any of them fails. Do not stash, reset or rebase to make them pass.

## 2. Work out the tag
- X.Y.Z is `VERSION`, unless the argument names another.
- Refuse a tag that exists (`git tag -l`), and an RC of a version that already has a final
  release.
- An RC is `vX.Y.Z-rc.N`, N one more than the highest `vX.Y.Z-rc.*` tag (1 when there is none).
- `publish.yml` fails when the tag (without `-rc.N`) and `VERSION` disagree; the release commit
  keeps them in step.

## 3. Gather what changed
```bash
prev=$(gh release list --exclude-drafts --exclude-pre-releases --limit 1 --json tagName --jq '.[0].tagName')
git log --first-parent --format='%h %s' "$prev"..HEAD
gh pr view <N> --json number,title,body
gh release view "$prev" --json body --jq .body   # the previous highlights, for their style
```
- Everything since the previous **full** release counts, RCs included: the notes of a final
  release cover the whole version.
- PR numbers come from merge commits (`Merge pull request #N`) and squashed titles (`... (#N)`).
  Read commits without a PR from their message and diff.
- Leave out Renovate's dependency PRs, except a Lettuce update (`LETTUCE_REF` in
  `config.env.example`) or one that changes what people or admins see. Leave out CI-only,
  test-only and developer-docs-only changes.
- An RC's own fixes, made between RCs of the same version, are not news in the final release
  unless they changed what was announced; fold them into the item they belong to.

## 4. Write release/NOTES.md
```markdown
## Highlights
- **New: <feature>.** What a person or an admin can now do, in a sentence or two. Its details
  as sub-bullets when there are several (#N).
- **Breaking:** what stops working and what the admin must do (#N).
- **Behaviour change:** what works differently, and how to keep the old result (#N).
- **<Improvement or fix people notice>.** One line each; group a feature's small ones (#N).
```
- Order: new features, then breaking changes and behaviour changes, then improvements and
  fixes. The biggest first within each.
- Write for the people and admins who install it: what changes for them, not how the code does
  it. No test counts, file lists or review history from the PR bodies.
- Use the names as they are in this release (commands, profiles, services).
- Point to the PR (`#N`) instead of repeating long steps, such as a manual migration.
- Follow AGENTS.md: never claim more than is true (pipelock is advisory, the vault's
  placeholders are not a boundary); no real emails, names, domains, Cloudflare ids, IPs or
  `/home/<user>` paths, only the placeholders it lists.
- The file holds only the highlights. `publish.yml` adds `## Release details` and the rest.

When `release/NOTES.md` already exists:
- Changed since `$prev` (`git log --oneline "$prev"..HEAD -- release/NOTES.md` prints
  something): it is this version's, from an earlier RC or a hand edit. Keep its wording and add
  only what is not in it yet.
- Not changed since `$prev`: it is left over from the previous release (its reset was
  skipped). Write it from scratch.

## 5. Show it and wait
Show:
- the notes (or their diff against the existing file);
- the tag;
- the commands of steps 6 and, for a final release, 7.

Wait for the user's yes. Apply what they change and show it again.

## 6. Commit, push and tag
```bash
git add release/NOTES.md          # and VERSION, when the tag's version changed it
git commit -m "Release notes for vX.Y.Z[-rc.N]"
git push origin HEAD:main
git tag vX.Y.Z[-rc.N]
git push origin vX.Y.Z[-rc.N]
```
- No `[ci skip]` in this commit: the tag points at it, and a skip marker on the tagged commit
  can skip `publish.yml` too. `ci.yml` ignores a push that changes only `release/NOTES.md`, and
  `publish.yml` runs the checks itself.
- No attribution lines in the commit message.
- When a push is rejected (the main ruleset, or `main` moved), stop and report. Never force
  push, and never move or delete a pushed tag.

## 7. Follow the release, then start the next version
```bash
gh run list --workflow publish.yml --commit "$(git rev-parse HEAD)" --limit 1 --json databaseId --jq '.[0].databaseId'   # may take a few seconds to appear
gh run watch <id> --exit-status
gh release view vX.Y.Z[-rc.N] --json url --jq .url
```
Report the release's URL, or the failing job when the run fails.

After a **final** release whose run succeeded, start the next version, unless `main`'s
`VERSION` is no longer X.Y.Z (someone moved it: report and stop):
```bash
git pull --ff-only origin main
echo X.(Y+1).0 >VERSION
git rm release/NOTES.md
git add VERSION
git commit -m "Set VERSION to X.(Y+1).0 [ci skip]"
git push origin HEAD:main
```
- The next version defaults to the next minor. A smaller one is chosen at its first tag (step 2).
- `[ci skip]` is safe here: this commit is never tagged, since the next release's tag goes on
  its own notes commit.

Finish with a short summary: the tag, the release's URL, and the next `VERSION`.

# Spec Delta

## Purpose

Protects the path from a reviewed commit to a published lollo-rozzo release, so that nothing short of the maintainer's own admin role can merge untested code, move a release tag or swap a published release's files.

## ADDED Requirements

### Requirement: Merging into main waits for the whole CI gate
The `main` branch SHALL require the `ci-passed` check, which passes only when every other CI job passed, and SHALL NOT be mergeable by a non-admin actor, GitHub's auto-merge included, before that check passes.

#### Scenario: A job outside the old list fails
- **WHEN** a pull request's `sidecar (browser)` job fails and every other job passes
- **THEN** `ci-passed` fails and GitHub's auto-merge does not merge the pull request

#### Scenario: Every job passes
- **WHEN** every CI job of a pull request passes
- **THEN** `ci-passed` passes and the pull request may merge

### Requirement: Hotfix branches are protected like main
Every branch matching `hotfix/*` SHALL be protected as `main` is: no deletion, no force push, and the `ci-passed` check before a non-admin merge.

#### Scenario: Force push to a hotfix branch
- **WHEN** an actor without the admin role force-pushes to `hotfix/0.4`
- **THEN** GitHub refuses the push

### Requirement: Release tags cannot be moved or deleted
Tags matching `v*` SHALL be creatable only by the repository's admin role, and no actor without that role SHALL delete or move one.

#### Scenario: A workflow token creates a release tag
- **WHEN** a token without the admin role, a workflow's or the Renovate app's, pushes a tag `v9.9.9`
- **THEN** GitHub refuses the tag

#### Scenario: Moving a published tag
- **WHEN** anyone pushes `v0.4.0` to another commit after its release was published
- **THEN** GitHub refuses the update

### Requirement: Published releases are immutable
The repository SHALL have immutable releases on, and a release SHALL be published only once all of its assets are attached, so that no published release's assets or tag can later be changed or deleted.

#### Scenario: Replacing a published asset
- **WHEN** anyone tries to delete or re-upload `lolloctl` or `SHA256SUMS` on a published release
- **THEN** GitHub refuses the change

#### Scenario: The release workflow attaches its assets
- **WHEN** `publish.yml` creates a release with the wrapper, `RELEASE` and `SHA256SUMS`
- **THEN** the release is a draft until every asset is uploaded, and becomes immutable when published

### Requirement: The live settings can be checked against the recorded ones
The repository SHALL record the expected rulesets and the immutable-releases setting. A check SHALL compare the live settings with that record and name each difference, exiting non-zero when there is any.

#### Scenario: The ruleset requires the old named checks
- **WHEN** the live `Main branch` ruleset requires `cli` and `docs` but not `ci-passed`
- **THEN** the check names the ruleset and the missing and extra checks, and exits non-zero

#### Scenario: Settings match
- **WHEN** every recorded setting matches the live repository
- **THEN** the check prints that the settings match and exits zero

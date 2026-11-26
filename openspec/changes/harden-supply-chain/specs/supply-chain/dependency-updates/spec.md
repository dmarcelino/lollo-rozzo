# Spec Delta

## Purpose

Decides which automated dependency updates may merge without a person, so that the components that define the stack's isolation are always read by someone before they ship.

## ADDED Requirements

### Requirement: Lettuce updates never merge on their own
A Renovate pull request that moves `dmarchevsky/lettuce` (`LETTUCE_REF`) SHALL NOT have auto-merge enabled, whatever its update type, patch tags that keep letta-code's version included.

#### Scenario: A Lettuce patch tag that keeps letta-code
- **WHEN** Renovate opens a pull request moving `LETTUCE_REF` from `v0.50.2-letta_0.34.70` to `v0.50.3-letta_0.34.70` and CI passes
- **THEN** the pull request stays open until a person merges it

#### Scenario: A Lettuce minor tag
- **WHEN** Renovate opens a pull request for a Lettuce minor tag
- **THEN** the pull request stays open until a person merges it

### Requirement: A Lettuce pull request names the checklist
Every Renovate pull request that moves `LETTUCE_REF` SHALL say in its body that the person must read upstream's Compose changes and walk the Lettuce upgrade checklist before merging.

#### Scenario: Pull request body
- **WHEN** Renovate opens any Lettuce pull request
- **THEN** its body points at CONTRIBUTING.md's "Upgrading `LETTUCE_REF`" checklist and asks for upstream's Compose diff to be read

### Requirement: The Renovate configuration stays valid
The Renovate configuration SHALL pass `renovate-config-validator --strict` at the version the Renovate workflow runs.

#### Scenario: Validating the configuration
- **WHEN** `renovate-config-validator --strict` runs on `.github/renovate.json5`
- **THEN** it reports no error or warning

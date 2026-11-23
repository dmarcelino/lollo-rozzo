# Spec Delta

## MODIFIED Requirements

### Requirement: Pages run apart from what controls them
Chromium and its display SHALL run in a container of their own, `lollo-browser-engine`, separate from `lollo-browser`, which holds the tools, the page, the settings, the audit log, the kept sign-ins and the token it sends notifications with. The engine SHALL mount no volume and hold none of that data at rest.

#### Scenario: What the engine holds
- **WHEN** the engine container is inspected
- **THEN** it has no named volume, no host path, no notification token, and no settings, audit log or saved sign-in on disk

### Requirement: Browser data stays on its own volume
The browser's stored state (kept sign-ins, settings, audit log) SHALL live only on the named volume `browser-data`, which only `lollo-browser` mounts, whatever the profiles. Nothing of it SHALL be written under `stacks/<name>/state/`. `lolloctl backup` SHALL NOT copy `browser-data`.

#### Scenario: Another service mounts the volume
- **WHEN** any service other than `lollo-browser` mounts `browser-data`, or a `volumes_from` names `lollo-browser`
- **THEN** `lolloctl check` refuses the stack, also when the `lollo-browser` profile is off

#### Scenario: Backup
- **WHEN** an operator runs `lolloctl backup <name>`
- **THEN** the backup contains no browser data, and the guide says sign-ins and settings must be made again after a restore

#### Scenario: Stack removed
- **WHEN** an operator runs `lolloctl remove <name>`
- **THEN** `browser-data` is deleted with the stack, and the confirmation says so when the volume exists

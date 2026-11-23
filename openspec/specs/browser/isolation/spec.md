# browser/isolation Specification

## Purpose

Keeps the agents' browser, which loads hostile pages and holds live sign-ins, contained: what it can run, what it can reach, where its data lives, and which checks refuse a stack that loosens any of it.

## Requirements

### Requirement: Opt-in profile that needs pipelock
The browser SHALL run only in stacks with the `lollo-browser` profile, and that profile SHALL require `lollo-pipelock`. `lolloctl profiles` and `up` SHALL refuse a profile list with `lollo-browser` but without `lollo-pipelock`, saying why.

#### Scenario: Browser without pipelock is refused
- **WHEN** an operator runs `lolloctl profiles cloudflared,lollo-browser`
- **THEN** the command fails before writing any list, naming `lollo-pipelock` as the missing dependency

#### Scenario: Profile off
- **WHEN** a stack's profiles do not include `lollo-browser`
- **THEN** no browser container runs and no `/browser/` route exists

### Requirement: Pages run apart from what controls them
Chromium and its display SHALL run in a container of their own, `lollo-browser-engine`, separate from `lollo-browser`, which holds the tools, the page, the settings, the audit log, the kept sign-ins and the push keys. The engine SHALL mount no volume and hold none of that data at rest.

#### Scenario: What the engine holds
- **WHEN** the engine container is inspected
- **THEN** it has no named volume, no host path, and no settings, audit log, push key or saved sign-in on disk

### Requirement: Chromium's own sandbox stays on
Chromium SHALL run with its sandbox enabled, as a non-root user, and never with `--no-sandbox` or an equivalent switch. `lolloctl check` SHALL refuse a stack whose engine would start Chromium without the sandbox, and `up` SHALL refuse to start the browser on a host where the sandbox cannot work, rather than fall back.

#### Scenario: Sandbox switch added
- **WHEN** the rendered config or the engine's launch settings would pass `--no-sandbox` to Chromium
- **THEN** `lolloctl check` refuses the stack, citing the browser's rules

#### Scenario: Host without unprivileged user namespaces
- **WHEN** `up` runs on a host where Chromium's sandbox cannot start
- **THEN** the browser does not start and the operator is told which host setting to change, with a pointer to the guide

### Requirement: Hardened containers
Both browser services SHALL keep the sidecar hardening: read-only root, every capability dropped, `no-new-privileges`, no host path, no added capability, no shared namespace, no devices, no `volumes_from`, memory and process limits. The engine's seccomp profile SHALL be the browser's own, and `lolloctl check` SHALL refuse any other.

#### Scenario: Capability added back
- **WHEN** an overlay gives either browser service `cap_add: [SYS_ADMIN]`
- **THEN** `lolloctl check` refuses the stack

#### Scenario: Seccomp unconfined
- **WHEN** the engine's `security_opt` sets `seccomp=unconfined` or another profile
- **THEN** `lolloctl check` refuses the stack

### Requirement: The engine's only way out is pipelock
The engine SHALL be on one internal network of its own and no other, and that network SHALL hold only the engine, `lollo-browser` and `lollo-pipelock`. Every request Chromium makes, loopback included, SHALL go through `lollo-pipelock`, and Chromium SHALL not use QUIC, non-proxied WebRTC or DNS-over-HTTPS. The app-server SHALL not join that network.

#### Scenario: Engine on the default network
- **WHEN** an overlay adds the stack's `default` network to the engine, or any other service to the engine's network
- **THEN** `lolloctl check` refuses the stack

#### Scenario: Page reaches a stack service
- **WHEN** a page's script requests `http://lollo-vault:8000/redeem`, the BFF, the app-server, `lollo-browser` or any private, loopback or link-local address
- **THEN** the request fails and no stack service receives it

#### Scenario: Page reaches the engine's own loopback
- **WHEN** a page's script requests any loopback port of the engine container
- **THEN** the request is refused by `lollo-pipelock`

#### Scenario: Rebinding a public name to a private address
- **WHEN** a page's host name resolves to a public address and then to a private one
- **THEN** no request reaches the private address

#### Scenario: Ordinary browsing
- **WHEN** an agent opens a public HTTPS site
- **THEN** the page loads through `lollo-pipelock`, which records a receipt

### Requirement: The engine obeys only lollo-browser
The engine's control and display connections SHALL listen only on its internal network and SHALL require a secret generated once per stack that only `lollo-browser` and the engine receive. `lollo-browser` SHALL refuse tool and page requests that arrive from the engine's network.

#### Scenario: Connection without the secret
- **WHEN** anything connects to the engine's control or display port without the stack's secret
- **THEN** the connection is refused

#### Scenario: Engine calls lollo-browser
- **WHEN** a request reaches `lollo-browser` from the engine's network
- **THEN** it is refused, whatever its path

### Requirement: Browser data stays on its own volume
The browser's stored state (kept sign-ins, settings, audit log, push subscriptions) SHALL live only on the named volume `browser-data`, which only `lollo-browser` mounts, whatever the profiles. Nothing of it SHALL be written under `stacks/<name>/state/`. `lolloctl backup` SHALL NOT copy `browser-data`.

#### Scenario: Another service mounts the volume
- **WHEN** any service other than `lollo-browser` mounts `browser-data`, or a `volumes_from` names `lollo-browser`
- **THEN** `lolloctl check` refuses the stack, also when the `lollo-browser` profile is off

#### Scenario: Backup
- **WHEN** an operator runs `lolloctl backup <name>`
- **THEN** the backup contains no browser data, and the guide says sign-ins and settings must be made again after a restore

#### Scenario: Stack removed
- **WHEN** an operator runs `lolloctl remove <name>`
- **THEN** `browser-data` is deleted with the stack, and the confirmation says so when the volume exists

### Requirement: No Chromium profile, password store or extensions
Chromium SHALL run without a persistent profile directory, with its password manager, form autofill, payment-card storage and extensions turned off by policy, and SHALL open no remote-debugging port: it is driven over a pipe, and its launcher refuses any extra argument.

#### Scenario: Site offers to save a password
- **WHEN** an agent signs in to a site
- **THEN** Chromium offers to save nothing and stores no password or card

#### Scenario: Engine's listeners
- **WHEN** the engine is running
- **THEN** only its Playwright endpoint and its display port listen

# Spec Delta

## Purpose

States the container capability rules `lolloctl check` holds every stack to, so that no container on the stack network can capture the plain HTTP that carries Access tokens, the vault passphrase and redeemed values.

## ADDED Requirements

### Requirement: Upstream helpers on the stack network drop NET_RAW
The stack's `cloudflared`, `searxng`, `ddg-mcp` and `google-mcp` containers SHALL run without the `NET_RAW` capability, as the app-server, the BFF and the channel gateway already do.

#### Scenario: A stack with the search sidecars
- **WHEN** a stack with the `search` and `cloudflared` profiles is up
- **THEN** `cloudflared`, `searxng` and `ddg-mcp` run with `NET_RAW` dropped

#### Scenario: A stack with Google
- **WHEN** a stack with google-mcp is up
- **THEN** `google-mcp` runs with `NET_RAW` dropped

### Requirement: lolloctl check refuses a helper that keeps NET_RAW
`lolloctl check`, and every command that runs it, SHALL refuse a stack whose rendered configuration gives `cloudflared`, `searxng`, `ddg-mcp`, `google-mcp`, the app-server or any container in the app-server's network namespace neither `NET_RAW` nor `ALL` in `cap_drop`. The refusal SHALL name each such service. A service whose profile is off is not rendered and is not checked.

#### Scenario: An overlay drops the capability rule for searxng
- **WHEN** the rendered configuration's `searxng` has an empty `cap_drop`
- **THEN** `lolloctl check` fails and names `searxng` and NET_RAW

#### Scenario: A stack without the search profile
- **WHEN** the rendered configuration has no `searxng` or `ddg-mcp` service
- **THEN** the rule passes for the services that are rendered

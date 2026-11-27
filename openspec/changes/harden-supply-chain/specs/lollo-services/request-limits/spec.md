# Spec Delta

## Purpose

Bounds what any caller on the stack network, signed in or not, can make a lollo service spend in memory and outbound fetches, so that one compromised container cannot restart the vault (locking it) or stall the lollo pages.

## ADDED Requirements

### Requirement: Request bodies have a size cap
Every lollo service (landing, the vault, Gmail triggers, News Radar, the code editor's gate, the browser and notifications) SHALL refuse with 413 an HTTP request whose body exceeds its route's cap, before the handler reads it. The default cap is 1 MiB, and MCP endpoints and internal APIs are included.

#### Scenario: Oversized declared body to the vault's redeem
- **WHEN** a caller posts to the vault's `/redeem` with `Content-Length` of 2 MiB
- **THEN** the vault answers 413 without reading the body, and keeps running unlocked

#### Scenario: Oversized body without a declared length
- **WHEN** a caller streams a chunked body of more than 1 MiB to a lollo service's `/mcp`
- **THEN** the service answers 413 once the cap is passed and stops reading

#### Scenario: A normal request
- **WHEN** an agent's MCP call or a person's form post is under the cap
- **THEN** it is handled as before

### Requirement: A route can declare a larger cap
A lollo service SHALL be able to give a route a cap of its own. The vault's Bitwarden import SHALL accept a file up to its import limit (5 MiB) plus form overhead, and refuse anything larger with 413.

#### Scenario: Importing a 4 MiB Bitwarden export
- **WHEN** the person uploads a 4 MiB Bitwarden export on the vault's import page
- **THEN** the import runs as before

#### Scenario: Importing a 6 MiB file
- **WHEN** the person uploads a 6 MiB file
- **THEN** the vault refuses it with 413 and says the upload must be at most 5 MB

### Requirement: The code editor's proxy keeps streaming
The code editor's gate SHALL keep streaming request bodies to the editor, without a cap, once it has authenticated the request, so that the person's uploads to the editor keep working. Its own routes keep the default cap.

#### Scenario: Uploading a large file in the editor
- **WHEN** the signed-in person uploads a 50 MiB file through the editor
- **THEN** the gate streams it to the editor as before

#### Scenario: An unauthenticated large body to the gate
- **WHEN** a caller without a valid Access token posts 50 MiB to the gate's editor path
- **THEN** the gate refuses the request without forwarding or buffering its body

### Requirement: Only RS256 tokens with a key id reach key lookup
A lollo service SHALL refuse, as an invalid sign-in, a token whose header names an algorithm other than `RS256` or carries no key id. It SHALL do so without fetching or looking up any Access key.

#### Scenario: A token signed with HS256
- **WHEN** a request carries a `Cf-Access-Jwt-Assertion` token whose header says `HS256`
- **THEN** the service answers 403 and makes no key-set request

### Requirement: The Access key set is fetched at most once per interval
A lollo service SHALL fetch the team's Access key set at most once per refetch interval (60 seconds) after a successful fetch, and at most once per retry interval (5 seconds) after a failed one. A key id that is unknown after a successful fetch within that interval fails closed, as an invalid sign-in, without a new fetch. While the last fetch failed, every request whose key is not cached SHALL get 503 ("could not verify the sign-in right now"), never an invalid sign-in.

#### Scenario: A burst of unknown key ids
- **WHEN** 100 requests arrive within a minute, each with a token naming a different unknown key id
- **THEN** the service makes at most one key-set fetch and refuses every request

#### Scenario: Cloudflare rotates its keys
- **WHEN** a valid token names a new key id and the last fetch was more than 60 seconds ago
- **THEN** the service fetches the key set again and accepts the token

#### Scenario: The key set is unreachable at start
- **WHEN** a service's first key-set fetch fails and a valid token arrives 2 seconds later
- **THEN** the service answers 503 without a new fetch, and a token arriving 5 seconds after the failure makes a fetch and signs in

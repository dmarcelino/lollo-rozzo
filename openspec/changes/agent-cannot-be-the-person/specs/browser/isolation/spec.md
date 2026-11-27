# Spec Delta

## ADDED Requirements

### Requirement: The stack's own hosts are out of the engine's reach
`lollo-pipelock` SHALL refuse every request to a denied host: the Lettuce host and the lollo host of every stack on this host, and the stack's Cloudflare Access team domain. It SHALL keep refusing its shipped blocklist too. The list SHALL come from `lolloctl`, never from `stack.env`.

#### Scenario: Agent opens the person's lollo host
- **WHEN** an agent opens `https://alfred-lollo.example.com/vault/` in a browser task
- **THEN** the request is refused by `lollo-pipelock`, which signs a block receipt, and no request reaches Cloudflare

#### Scenario: Another stack's host
- **WHEN** a task of stack `alfred` opens the Lettuce host of stack `hal` on the same host
- **THEN** the request is refused

#### Scenario: Access sign-in page
- **WHEN** a task opens any URL on the stack's `<team>.cloudflareaccess.com`
- **THEN** the request is refused

#### Scenario: Indirect ways to the host
- **WHEN** a page reaches a denied host through a redirect chain, an iframe, a service worker's fetch, upper case, a trailing dot or a percent-encoded host
- **THEN** each request is refused

#### Scenario: Shipped blocklist kept
- **WHEN** a task opens a host on pipelock's shipped blocklist, such as a `pastebin.com` subdomain
- **THEN** the request is refused, as before this change

#### Scenario: Same domain, other host
- **WHEN** the stacks' hosts are under `example.com` and a task opens `https://www.example.com/`
- **THEN** the page loads through `lollo-pipelock`

#### Scenario: A stack added later
- **WHEN** stack `hal` is added and stack `alfred` is next brought up
- **THEN** `alfred`'s pipelock refuses `hal`'s hosts too

### Requirement: Chromium blocks the denied hosts by policy
The engine SHALL start Chromium with a managed policy that blocks every denied host, besides the schemes it already blocks. The engine SHALL refuse to start when the deny list is missing or malformed, rather than start without the policy.

#### Scenario: Navigation to a denied host
- **WHEN** an agent navigates a task to the stack's lollo host
- **THEN** Chromium shows its "blocked by policy" page and sends no request

#### Scenario: Missing list
- **WHEN** the engine starts without a deny list, or with an entry that is not a host name
- **THEN** it exits with an error naming the variable and Chromium does not start

### Requirement: lolloctl check holds both layers
With the `lollo-browser` profile, `lolloctl check` SHALL refuse a stack whose `lollo-pipelock` or `lollo-browser-engine` would start without the stack's own Lettuce host, its lollo host and its Access team domain in their deny list.

#### Scenario: Deny list removed by an overlay
- **WHEN** an overlay sets the engine's or pipelock's deny list to an empty value
- **THEN** `lolloctl check` refuses the stack, naming the missing hosts

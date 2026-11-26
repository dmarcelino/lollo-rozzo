# Spec Delta

## MODIFIED Requirements

### Requirement: One notifications service per stack
A stack SHALL run the notifications service, `lollo-notifications`, whenever `lollo-vault`, `lollo-browser`, `lollo-gmail-triggers` or `lollo-news-radar` is on, and not otherwise; it has no profile of its own. It SHALL be the only service that holds the stack's push keys (`LOLLO_VAPID_*`, generated once, never rotated) and the only one that sends Web Push to the lollo's devices.

#### Scenario: A sender is on
- **WHEN** a stack has `lollo-vault`, `lollo-browser`, `lollo-gmail-triggers` or `lollo-news-radar` on
- **THEN** `lollo-notifications` runs and the lollo host routes `/notifications/` to it

#### Scenario: No sender is on
- **WHEN** a stack has none of `lollo-vault`, `lollo-browser`, `lollo-gmail-triggers` and `lollo-news-radar` on
- **THEN** `lollo-notifications` does not run and `/notifications/` is not routed

#### Scenario: Who holds the private key
- **WHEN** the stack's rendered Compose configuration is inspected
- **THEN** only `lollo-notifications` has the VAPID private key in its environment

#### Scenario: Profiles change
- **WHEN** an operator runs `lolloctl profiles` and a sender stays on
- **THEN** `lollo-notifications` is not recreated

#### Scenario: Upgraded stack
- **WHEN** an operator upgrades a stack that has a sender on and runs `lolloctl up`, which makes no Cloudflare call
- **THEN** `up` says to run `lolloctl profiles` once so the tunnel routes `/notifications/`, and the guide says the same

#### Scenario: Gmail triggers alone
- **WHEN** a stack has `lollo-gmail-triggers` on and neither the vault nor the browser, and is upgraded
- **THEN** `lollo-notifications` now runs, and `up` says to run `lolloctl profiles` once so the tunnel routes `/notifications/`

### Requirement: Each sender has a token of its own
Each sender SHALL have its own token (`NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`, `NOTIFY_TOKEN_GMAIL_TRIGGERS`, `NOTIFY_TOKEN_NEWS_RADAR`), 64 lowercase hex characters, made once per stack whatever the profiles, and held only by that sender and `lollo-notifications`. `lolloctl check` SHALL refuse a stack where a token is malformed, two tokens are equal, a token equals `LOLLO_EXECUTOR_TOKEN`, or any other service's environment holds a token's value.

#### Scenario: Token handed to the app-server
- **WHEN** a sender's token appears in the environment of the app-server, the BFF, `landing` or any service other than the hub and that sender
- **THEN** `lolloctl check` refuses the stack

#### Scenario: Sender turned on later
- **WHEN** a stack created without `lollo-browser` later turns it on
- **THEN** its token already exists in `stack.env` and the browser can send at once

#### Scenario: News Radar holds the Gmail token
- **WHEN** `NOTIFY_TOKEN_GMAIL_TRIGGERS` appears in `lollo-news-radar`'s environment
- **THEN** `lolloctl check` refuses the stack

### Requirement: A sender says only what is its own
A notification SHALL carry only a title, a body, a link and a tag, each within a length limit. The service SHALL refuse one whose link is not a path under the sender's own prefix (`/vault/`, `/browser/`, `/gmail-triggers/`, `/news-radar/`) or whose tag does not start with the sender's name, and SHALL make every field a single line of plain text.

#### Scenario: Browser poses as the vault
- **WHEN** the browser's token sends a notification linking to `/vault/r/<id>` or tagged `vault-request`
- **THEN** it is refused and nothing is sent

#### Scenario: News Radar poses as the vault
- **WHEN** News Radar's token sends a notification linking to `/vault/` or tagged `vault-request`
- **THEN** it is refused and nothing is sent

#### Scenario: Link to another site
- **WHEN** a sender's notification links to an absolute URL or another host
- **THEN** it is refused

#### Scenario: Unknown field
- **WHEN** a notification carries a field other than title, body, link and tag, an action among them
- **THEN** it is refused

### Requirement: Each notification names its sender
The service SHALL put the sender's own label in front of every notification's title ("Vault: ", "Browser: ", "Gmail triggers: ", "News Radar: ", "Lollo: " for its own test), whatever the sender sent, so no sender can show a notification that reads as another's.

#### Scenario: Browser imitates a vault request
- **WHEN** the browser's token sends the title "Approve sharing"
- **THEN** the device shows "Browser: Approve sharing"

#### Scenario: Gmail triggers notice
- **WHEN** Gmail triggers' token sends the title "New sender on Invoices"
- **THEN** the device shows "Gmail triggers: New sender on Invoices"

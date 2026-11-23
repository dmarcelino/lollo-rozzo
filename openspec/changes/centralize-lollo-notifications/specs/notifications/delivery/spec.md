# Spec Delta

## Purpose

Governs how a lollo notification gets from a sender (the vault, the browser) to the person's devices: one notifications service per stack holds the push keys and the subscriptions, only listed senders may use it, and each may say only what is its own.

## ADDED Requirements

### Requirement: One notifications service per stack
A stack SHALL run the notifications service, `lollo-notifications`, whenever `lollo-vault` or `lollo-browser` is on, and not otherwise; it has no profile of its own. It SHALL be the only service that holds the stack's push keys (`LOLLO_VAPID_*`, generated once, never rotated) and the only one that sends Web Push to the lollo's devices.

#### Scenario: A sender is on
- **WHEN** a stack has `lollo-vault` or `lollo-browser` on
- **THEN** `lollo-notifications` runs and the lollo host routes `/notifications/` to it

#### Scenario: No sender is on
- **WHEN** a stack has neither `lollo-vault` nor `lollo-browser` on
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

### Requirement: Each sender has a token of its own
Each sender SHALL have its own token (`NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`), 64 lowercase hex characters, made once per stack whatever the profiles, and held only by that sender and `lollo-notifications`. `lolloctl check` SHALL refuse a stack where a token is malformed, two tokens are equal, a token equals `LOLLO_EXECUTOR_TOKEN`, or any other service's environment holds a token's value.

#### Scenario: Token handed to the app-server
- **WHEN** a sender's token appears in the environment of the app-server, the BFF, `landing` or any service other than the hub and that sender
- **THEN** `lolloctl check` refuses the stack

#### Scenario: Sender turned on later
- **WHEN** a stack created without `lollo-browser` later turns it on
- **THEN** its token already exists in `stack.env` and the browser can send at once

### Requirement: The send API is for senders only
The service SHALL accept a notification only at `/notify/v1/send`, with a listed sender's token, and SHALL refuse a request that carries an `Origin` or any of Cloudflare's tunnel headers. The tunnel SHALL never route that path.

#### Scenario: Agent without a token
- **WHEN** an agent on the stack network posts to `/notify/v1/send` without a token or with a wrong one
- **THEN** the request is refused with 401 and nothing is sent

#### Scenario: Request from a page
- **WHEN** a request to `/notify/v1/send` carries an `Origin` header, even with a valid token
- **THEN** it is refused with 403

#### Scenario: Through the tunnel
- **WHEN** someone requests `https://<lollo host>/notify/v1/send`
- **THEN** the tunnel does not route it to the service

### Requirement: A sender says only what is its own
A notification SHALL carry only a title, a body, a link and a tag, each within a length limit. The service SHALL refuse one whose link is not a path under the sender's own prefix (`/vault/`, `/browser/`) or whose tag does not start with the sender's name, and SHALL make every field a single line of plain text.

#### Scenario: Browser poses as the vault
- **WHEN** the browser's token sends a notification linking to `/vault/r/<id>` or tagged `vault-request`
- **THEN** it is refused and nothing is sent

#### Scenario: Link to another site
- **WHEN** a sender's notification links to an absolute URL or another host
- **THEN** it is refused

#### Scenario: Unknown field
- **WHEN** a notification carries a field other than title, body, link and tag, an action among them
- **THEN** it is refused

### Requirement: Senders are rate limited
The service SHALL cap how many notifications each sender may send per period and refuse the rest, so a sender in a loop cannot ring every device without end; one sender reaching its cap SHALL NOT affect another. Only notifications the service accepted SHALL count against the cap.

#### Scenario: Browser floods
- **WHEN** the browser sends more notifications than its cap within the period
- **THEN** the extra ones are refused and logged, and the vault's notifications still go out

#### Scenario: Service busy
- **WHEN** the service refuses a notification because it cannot queue it
- **THEN** that attempt does not count against the sender's cap

### Requirement: One sender never delays another
Each sender's accepted notifications SHALL wait and be delivered apart from every other sender's, so neither a sender's volume nor a slow device delays or refuses another sender's notifications. A notification SHALL go to a person's devices at the same time, not one after another, and a device whose push service keeps timing out SHALL be dropped.

#### Scenario: Browser busy, slow device
- **WHEN** one enrolled device's push service times out on every push and the browser sends at its full rate
- **THEN** the vault's notifications are still accepted at once and reach the other devices without waiting behind the browser's

#### Scenario: Device keeps timing out
- **WHEN** a device's push service times out on several pushes in a row
- **THEN** its subscription is deleted and the page shows it off

### Requirement: Each notification names its sender
The service SHALL put the sender's own label in front of every notification's title ("Vault: ", "Browser: ", "Lollo: " for its own test), whatever the sender sent, so no sender can show a notification that reads as another's.

#### Scenario: Browser imitates a vault request
- **WHEN** the browser's token sends the title "Approve sharing"
- **THEN** the device shows "Browser: Approve sharing"

### Requirement: Accepted notifications reach every enrolled device
The service SHALL answer an accepted notification at once, before delivery, then deliver it to every subscribed device of every person in `ALLOWED_USERS`, skipping devices of anyone no longer listed. A subscription the push service reports gone (404 or 410) SHALL be deleted; any other failure SHALL be logged and decide nothing.

#### Scenario: Two devices
- **WHEN** a person has enrolled a phone and a laptop and a sender's notification is accepted
- **THEN** both show it

#### Scenario: Person removed
- **WHEN** a person is removed from the stack's users
- **THEN** their devices get no further notification

#### Scenario: Device gone
- **WHEN** the push service answers 410 for a subscription
- **THEN** that subscription is deleted and the next notification is not sent to it

### Requirement: No secret value in a notification
A notification SHALL never carry a secret value: the vault's name a secret and its field labels only, and the browser's pass through its value scrub before they are sent. The service SHALL log a notification's sender, tag and outcome, never its title or body.

#### Scenario: Vault request
- **WHEN** an agent asks the vault for a card's number
- **THEN** the notification names the secret and the field, and neither the payload nor any log holds the number

#### Scenario: Service logs
- **WHEN** the service's logs are read after notifications were sent
- **THEN** they show senders, tags and outcomes, and no title or body

### Requirement: Subscriptions are kept and backed up
Subscriptions SHALL live only on the named volume `notifications-data`, which only `lollo-notifications` mounts (and `lolloctl backup`, read-only). `lolloctl backup` SHALL copy it and `restore` SHALL put it back, so a restored stack still reaches its devices.

#### Scenario: Restore on another host
- **WHEN** a stack is backed up, restored on another host and started
- **THEN** devices enrolled before the backup get its notifications without enrolling again

#### Scenario: Another service mounts the volume
- **WHEN** any service other than `lollo-notifications` mounts `notifications-data`
- **THEN** `lolloctl check` refuses the stack

#### Scenario: Key and subscriptions apart
- **WHEN** the service starts with subscriptions made under another push key than the stack's (an older `stack.env` restored next to newer subscriptions, say)
- **THEN** those subscriptions are deleted, and the page shows those devices off so the person enables them again

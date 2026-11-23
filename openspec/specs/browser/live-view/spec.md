# browser/live-view Specification

## Purpose

Gives the person their own page on the lollo host to watch and take over the agents' browser, answer hand-offs, choose per-site settings and read the audit log, without going through an agent.

## Requirements

### Requirement: The page is a lollo page
`/browser/` SHALL be served on the stack's lollo host, behind the lollo's Access application, and every request to it, the live view's connection included, SHALL carry a valid Access token for that application, and every other request SHALL pass the Fetch Metadata check (the connection's own checks are the next requirement). Its pages SHALL set Cross-Origin-Opener-Policy, and every change SHALL need the page's form token.

#### Scenario: Not signed in
- **WHEN** someone without a valid lollo Access token requests `/browser/` or its live-view connection
- **THEN** the request is refused

#### Scenario: Lettuce's token
- **WHEN** a request carries an Access token for Lettuce's application, not the lollo's
- **THEN** the request is refused

#### Scenario: Request from another site
- **WHEN** a page on another site posts a settings change or opens the live-view connection
- **THEN** the request is refused

### Requirement: The live view's connection is checked by its origin and ticket
The live view's connection SHALL carry the page's own `Origin` and a single-use ticket the page fetched with its form token, and its Fetch Metadata SHALL be checked when the browser sends it: some browsers, Chrome among them, send none on a WebSocket.

#### Scenario: Browser that sends no Fetch Metadata on a WebSocket
- **WHEN** the person's browser opens the live-view connection with the page's `Origin` and a fresh ticket but no `Sec-Fetch-*` header, as Chrome does
- **THEN** the live view opens; without the `Origin`, or with another, it is refused

### Requirement: The live view shows the real browser window
The live view SHALL show the whole browser window, its address bar and tabs included, so the person can see which site they are on before typing anything.

#### Scenario: Person takes over to pay
- **WHEN** the person opens a hand-off to place an order
- **THEN** the live view shows the tab with its address bar and the site's address

### Requirement: Watching is read-only until the person takes over
The live view SHALL open read-only. The person SHALL take control with an explicit action and give it back with another, and agents' tool calls on a tab SHALL wait or be refused while the person controls it.

#### Scenario: Opening the live view
- **WHEN** the person opens `/browser/`
- **THEN** they see the browser but their clicks and keys reach it only after they press "take over"

#### Scenario: Agent acts during take-over
- **WHEN** an agent clicks in a tab the person is controlling
- **THEN** the agent's click is refused with "the person has control of this tab"

#### Scenario: One tab at a time
- **WHEN** the person has taken over one tab and tries to take over another
- **THEN** it is refused until they give the first back

#### Scenario: Another window comes forward
- **WHEN** the person has control and an agent opens a tab in another task, or the person opens the live view for another tab
- **THEN** their input is dropped until the controlled tab is in front again, which the browser restores at once, and the page says they have control of another tab

### Requirement: The screen stream is reachable only through the page
The person SHALL reach the display and input stream only through `/browser/`'s own connection, after its checks; `lollo-browser` relays it from the engine, and nothing else exposes it.

#### Scenario: Page tries the stream directly
- **WHEN** a page loaded in the browser tries to connect to the engine's display port
- **THEN** the connection is refused

### Requirement: Hand-offs, settings and kept sign-ins are listed
`/browser/` SHALL list open hand-offs with their summaries, the open tasks with their purposes, the kept sign-ins with "forget" and "forget all", and each site's "keep me signed in" and "may finish checkout" settings.

#### Scenario: Person reviews settings
- **WHEN** the person opens the settings section
- **THEN** they see every site with a setting, change any of them, and the change applies to the next action

#### Scenario: Adding a site already listed
- **WHEN** the person adds a site that already has settings or a saved sign-in
- **THEN** it is refused and nothing of that site changes

### Requirement: The person can stop everything
`/browser/` SHALL let the person end one task or all tasks at once, closing their tabs and contexts.

#### Scenario: Stop all
- **WHEN** the person presses "end all tasks"
- **THEN** every task ends, every context is destroyed, and agents' calls with those task ids are refused

### Requirement: The audit log is readable by the person
`/browser/` SHALL show the audit log, newest first, filterable by task and site, and SHALL never show a secret value.

#### Scenario: Person checks what happened
- **WHEN** the person opens the audit log after a task
- **THEN** they see its navigations, form submissions, secrets typed (site and field only), hand-offs and checkouts

### Requirement: Push notifications
The browser SHALL notify the person's enrolled devices of hand-offs and checkouts submitted through the lollo's notifications service, with its own sender token. It SHALL hold no push keys or subscriptions and offer no notification switch of its own; `/browser/` SHALL link to `/notifications/` instead.

#### Scenario: Hand-off while away
- **WHEN** an agent requests a hand-off and the person has enrolled a phone on `/notifications/`
- **THEN** the phone shows a notification that opens the hand-off on `/browser/`

#### Scenario: Notifications page
- **WHEN** the person looks for notification settings on `/browser/`
- **THEN** they find a link to `/notifications/`, and no switch of the browser's own

### Requirement: Listed on the landing page
When the profile is on, the lollo host's landing page SHALL link to `/browser/` with its own label.

#### Scenario: Profile on
- **WHEN** a stack has `lollo-browser`
- **THEN** the landing page shows a link to the browser page

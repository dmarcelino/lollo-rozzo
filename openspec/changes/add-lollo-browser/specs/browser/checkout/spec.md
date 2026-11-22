# Spec Delta

## Purpose

Governs how an order is placed through the browser: handed to the person by default, finished by an agent only on sites the person allows, and always notified and audited, with the real spending limits named honestly as the vault, the bank and the card.

## ADDED Requirements

### Requirement: Agents can hand off to the person
An agent SHALL be able to request a hand-off of a tab with a summary. The browser SHALL notify the person, show the hand-off on `/browser/` with its summary, task, site and current page, and let the agent wait for it to be marked done or declined.

#### Scenario: Agent hands off a checkout
- **WHEN** an agent has filled a cart and requests a hand-off with the summary "2 items, 34.90 EUR, delivery Tuesday"
- **THEN** the person gets a push notification, opens `/browser/`, sees the tab live, places the order or not, and marks the hand-off done or declined

#### Scenario: Agent learns the outcome
- **WHEN** the person marks the hand-off done
- **THEN** the agent waiting on it gets "done" and continues the task

#### Scenario: No answer
- **WHEN** the person does not answer before the hand-off's limit
- **THEN** the agent gets "no answer", and the tab stays open until the task ends

### Requirement: Checkout submits become hand-offs by default
On a site the person has not allowed to finish checkouts, when the browser recognises that an agent's click or key would submit a checkout, it SHALL not perform it and SHALL turn it into a hand-off instead.

#### Scenario: Agent clicks "Place order"
- **WHEN** an agent clicks a "Place order" button on a site that is not allowed
- **THEN** the click is not performed, a hand-off is created, and the tool result says the checkout was handed to the person

#### Scenario: Agent presses a key instead of clicking
- **WHEN** an agent presses Enter in a checkout form's field, or Enter or Space on the "Place order" button, by ref or on the focused element in any frame
- **THEN** it becomes a hand-off, as a click would

### Requirement: The person allows agents to finish checkouts per site
The person SHALL be able to allow, per site, that agents finish checkouts themselves, on `/browser/` only. Every site SHALL start not allowed. Agents SHALL have no tool to change it.

#### Scenario: Site allowed
- **WHEN** the person allows `shop.example.com` and an agent, asked in the chat to order, clicks "Place order" there
- **THEN** the click is performed

#### Scenario: Agent tries to allow a site
- **WHEN** an agent looks for a way to allow a site
- **THEN** no tool offers it

### Requirement: Every checkout submitted is notified and audited
Whenever the browser recognises a checkout submitted, by an agent or by the person in the live view, it SHALL send a push notification and record an audit entry with the task, site, page and time.

#### Scenario: Agent finishes a checkout
- **WHEN** an agent submits a checkout on an allowed site
- **THEN** the person is notified at once and the audit log shows it

#### Scenario: Click that fails
- **WHEN** an agent's click on an allowed site's "Place order" button fails
- **THEN** no checkout is notified or recorded, and a later successful click is

#### Scenario: Page's own requests during a take-over
- **WHEN** a checkout page sends a fetch or XHR POST while the person has control
- **THEN** it is not counted as a checkout; only a form POST that navigates the tab is

### Requirement: Recognising a checkout is stated as a guess
Docs and the `/browser/` page SHALL say that recognising a checkout is a guess that can miss, so the default hand-off is advisory and not a block, and SHALL name the limits that do hold: the vault's approval for a card from the vault, the bank's confirmation, and the card's own limits.

#### Scenario: Guide's security model
- **WHEN** a person reads the checkout part of the browser guide
- **THEN** it says a misled agent can get past the hand-off on a page the browser does not recognise, and recommends a virtual or limited card and not keeping a sign-in where a card is saved

### Requirement: Payments with a vault card name the purchase
The agent skill SHALL tell agents to request a vault card with a purpose naming the shop, the items and the total, so that approving it is approving that purchase.

#### Scenario: Agent pays with a vault card
- **WHEN** an agent needs the person's card to finish a checkout on an allowed site
- **THEN** its vault request's purpose names the shop, the items and the total, and the card is typed through the secret tool on that shop's site only

### Requirement: No automatic retry of a checkout
The agent skill SHALL tell agents never to submit a checkout again after a failure or timeout without first checking the site's order history, and the audit log SHALL show every attempt.

#### Scenario: Checkout times out
- **WHEN** a checkout submit times out
- **THEN** the agent checks the order history before anything else, and the audit log lists the first attempt

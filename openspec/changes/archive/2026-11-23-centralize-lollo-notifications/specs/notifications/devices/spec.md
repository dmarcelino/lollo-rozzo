# Spec Delta

## Purpose

Governs what the person does and sees on a device: one switch on `/notifications/` for every lollo notification, one Home Screen app for the whole lollo host, and what tapping a notification may do.

## ADDED Requirements

### Requirement: One switch per device
A person SHALL turn lollo notifications on for a device once, on `/notifications/`, and from then on get every sender's notifications on it; no other lollo page SHALL offer its own notification switch.

#### Scenario: Enabled once
- **WHEN** a person enables notifications on a phone on `/notifications/`, then an agent asks the vault for a secret and another asks the browser for a hand-off
- **THEN** the phone shows both notifications

#### Scenario: Sender pages
- **WHEN** the person opens `/vault/` or `/browser/`
- **THEN** there is no notification switch of their own, only a link to `/notifications/`

### Requirement: One Home Screen app for the lollo host
Every page a lollo service renders itself (the landing page, `/notifications/`, the vault, the browser, Gmail triggers, News Radar) SHALL link the same web app manifest, whose scope and start page are the lollo host's root, so adding any of them to the Home Screen gives the same app, named "Lollo", which opens on the landing page and keeps the vault and the browser inside it.

#### Scenario: Added from the vault
- **WHEN** a person on an iPhone adds `/vault/` to the Home Screen
- **THEN** the app is "Lollo", it opens on the landing page, and the vault and the browser open inside it

#### Scenario: Added from News Radar
- **WHEN** a person adds `/news-radar/` to the Home Screen
- **THEN** it is the same "Lollo" app

#### Scenario: Code editor
- **WHEN** the person opens the code editor's workbench, whose pages are code-server's own
- **THEN** they carry no lollo manifest, and the guide says to add Lollo from another lollo page

#### Scenario: Landing page
- **WHEN** the landing page is served
- **THEN** it links the manifest and `/notifications/`, and still runs no script

### Requirement: The notifications page
`/notifications/` SHALL let the person enable, disable and test notifications on the device in use, and SHALL list the person's own enrolled devices with when each was added, marking the one in use. It SHALL never show another person's devices.

#### Scenario: Test
- **WHEN** the person presses Test on an enrolled device
- **THEN** every device of that person shows a test notification, and the page says how many were reached

#### Scenario: Device dropped
- **WHEN** a device's subscription was removed without the person disabling it
- **THEN** the page shows the device in use as off, and it is missing from the list

### Requirement: The notifications page is a lollo page
Every route of `/notifications/` SHALL need a verified Access JWT of the lollo's own application, pass the Fetch Metadata check, and require the form token and the lollo's origin for every change; every response SHALL set COOP and a CSP that runs only the page's own script.

#### Scenario: Subscribe from another page's script
- **WHEN** script on another lollo page, a code editor webview say, posts a subscription to `/notifications/` without the page's form token
- **THEN** it is refused

### Requirement: A tap only opens a lollo page
Tapping a notification SHALL open its link only when the link is under a known lollo prefix (`/vault/`, `/browser/`, `/notifications/`), and the landing page otherwise. A notification SHALL have no action buttons: approving, sharing, declining or placing an order SHALL happen only on the page it opens.

#### Scenario: Vault request
- **WHEN** the person taps a vault request's notification
- **THEN** the request's page opens, with the fields asked for, and nothing is approved until the person presses Share once there

#### Scenario: Link outside the lollo pages
- **WHEN** a notification's link is not under a known lollo prefix
- **THEN** tapping it opens the landing page

### Requirement: A tap never takes over another page's window
A tap SHALL reuse an open window only when that window is already under the link's prefix, or on the landing page or `/notifications/`; otherwise it SHALL open a new window, so a tap never navigates away from work on another lollo page.

#### Scenario: Working in another lollo page
- **WHEN** the person is taking over a tab in `/browser/` and taps a vault request's notification
- **THEN** the request opens in a new window and the browser's tab stays as it was

### Requirement: Nothing is cached on the device
The lollo host's service worker SHALL cache nothing and handle no page request, so every page load still goes through Cloudflare Access.

#### Scenario: Offline
- **WHEN** the device is offline and the person opens a lollo page
- **THEN** no stored copy is shown

### Requirement: Notifications that moved are not sent twice
After the upgrade that brings the notifications service, the vault and the browser SHALL delete the subscriptions they held and send none of their own, and their old service workers SHALL remove themselves when the device next loads them. A device SHALL never get the same notification twice.

#### Scenario: Device enrolled before the upgrade
- **WHEN** a phone had notifications on for the vault before the upgrade, and the person enables them on `/notifications/` after it
- **THEN** each vault notification arrives once

#### Scenario: Not enabled again yet
- **WHEN** a person has not enabled notifications on `/notifications/` since the upgrade
- **THEN** their devices get no lollo notification, and vault requests still post their link in the chat

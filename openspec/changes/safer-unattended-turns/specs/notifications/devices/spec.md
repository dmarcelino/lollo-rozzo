# Spec Delta

## MODIFIED Requirements

### Requirement: A tap only opens a lollo page
Tapping a notification SHALL open its link only when the link is under a known lollo prefix (`/vault/`, `/browser/`, `/gmail-triggers/`, `/news-radar/`, `/notifications/`), and the landing page otherwise. A notification SHALL have no action buttons: approving, sharing, declining or placing an order SHALL happen only on the page it opens.

#### Scenario: Vault request
- **WHEN** the person taps a vault request's notification
- **THEN** the request's page opens, with the fields asked for, and nothing is approved until the person presses Share once there

#### Scenario: Monitor to confirm
- **WHEN** the person taps News Radar's notice about a monitor awaiting confirmation
- **THEN** `/news-radar/` opens, and the monitor stays awaiting confirmation until the person presses Confirm there

#### Scenario: Link outside the lollo pages
- **WHEN** a notification's link is not under a known lollo prefix
- **THEN** tapping it opens the landing page

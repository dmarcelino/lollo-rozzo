# Spec Delta

## MODIFIED Requirements

### Requirement: Push notifications
The browser SHALL notify the person's enrolled devices of hand-offs and checkouts submitted through the lollo's notifications service, with its own sender token. It SHALL hold no push keys or subscriptions and offer no notification switch of its own; `/browser/` SHALL link to `/notifications/` instead.

#### Scenario: Hand-off while away
- **WHEN** an agent requests a hand-off and the person has enrolled a phone on `/notifications/`
- **THEN** the phone shows a notification that opens the hand-off on `/browser/`

#### Scenario: Notifications page
- **WHEN** the person looks for notification settings on `/browser/`
- **THEN** they find a link to `/notifications/`, and no switch of the browser's own

# vault-erase-notices Specification

## Purpose

Governs the notices of a vault erase asked for without the passphrase: what protects the person is time and noise, so every step is announced on every enrolled device, and the erase's clock does not run while its warning cannot be handed over.

## Requirements

### Requirement: Every step of an erase is announced
An erase asked for without the passphrase SHALL be announced to every enrolled device when it is scheduled, cancelled (by Cancel or by unlocking) and done, and after a restart that finds it due or nearly due, each under its own tag so one notice never replaces another.

#### Scenario: Erase scheduled
- **WHEN** someone signed in types `DELETE` on the unlock page and asks for the erase
- **THEN** every enrolled device shows that an erase was asked for, by whom, and when it runs

#### Scenario: Unlocked meanwhile
- **WHEN** the person unlocks the vault while an erase is scheduled
- **THEN** the erase is cancelled and every enrolled device is told

### Requirement: An erase is scheduled only once its notice is accepted
The vault SHALL schedule an erase without the passphrase only after the notifications service has accepted the "erase scheduled" notice. When it has not accepted it within a few seconds, the vault SHALL schedule nothing and tell the person that notifications are unavailable and to try again later. Acceptance, not delivery, is what counts.

#### Scenario: Notifications service down
- **WHEN** someone asks for an erase while `lollo-notifications` is not running
- **THEN** no erase is scheduled, the unlock page says notifications are unavailable, and the audit log records no schedule

#### Scenario: No device enrolled
- **WHEN** a person with no enrolled device asks for an erase and the service accepts the notice
- **THEN** the erase is scheduled as usual

#### Scenario: Unlocked while waiting
- **WHEN** the vault is unlocked after the "scheduled" notice was accepted but before the erase was stored
- **THEN** no erase is scheduled and every enrolled device is told the erase was cancelled

#### Scenario: Asked twice at once
- **WHEN** two erase requests arrive at the same time
- **THEN** one erase is scheduled and devices get one "scheduled" notice

#### Scenario: Retried while notifications are down
- **WHEN** the person asks for an erase while notifications are down, more times than the hourly cap allows, and then once they are back
- **THEN** the refused attempts did not count against the cap, and the last one is scheduled

### Requirement: The grace after a restart waits for its notice
When the vault starts and finds an erase due or due within the grace period, it SHALL retry the "erase soon" notice until the notifications service accepts it, and the grace period SHALL count from that acceptance, not from the start.

#### Scenario: Restart while the service is down
- **WHEN** the vault restarts past an erase's due time and `lollo-notifications` comes up 20 minutes later
- **THEN** the notice goes out once the service accepts it, and the erase runs no sooner than the full grace period after that

### Requirement: Later notices never hold anything up
The "cancelled" and "done" notices SHALL be retried when the service does not accept them, and SHALL never delay or prevent a cancel or a due erase.

#### Scenario: Cancel while the service is down
- **WHEN** the person presses Cancel the erase while `lollo-notifications` is not running
- **THEN** the erase is cancelled at once, and the notice goes out once the service accepts it

# Spec Delta

## ADDED Requirements

### Requirement: Kept sign-ins never hold a denied host
Saving and loading a kept site SHALL leave out every cookie and every origin's storage that belongs to a denied host (any stack's Lettuce or lollo host, or the Access team domain), even when the person keeps that host's registrable domain.

#### Scenario: Person keeps the stacks' own domain
- **WHEN** the person keeps `example.com`, and a task ends holding cookies for `shop.example.com` and `alfred-lollo.example.com`
- **THEN** the cookies of `shop.example.com` are saved and nothing of `alfred-lollo.example.com` is

#### Scenario: Tampered saved file
- **WHEN** a saved sign-in file for `example.com` contains a cookie of `alfred.example.com`
- **THEN** a new task does not load that cookie

### Requirement: Keeping the identity provider's site is warned about
When the person marks the identity provider's site (`google.com`) "keep me signed in" on `/browser/`, the page SHALL show a warning before saving that this sign-in also signs agents in to every site that offers "Sign in with Google". Keeping it SHALL still be possible.

#### Scenario: Person keeps google.com
- **WHEN** the person turns on "keep me signed in" for `google.com`
- **THEN** the Sites page shows the warning, and the setting is saved once they confirm

#### Scenario: Another site
- **WHEN** the person keeps `shop.test`
- **THEN** no identity-provider warning is shown

# Spec Delta

## Purpose

How the vault presents an agent's request to share a login for the identity provider that signs the person in to their stack, so the person knows what that login unlocks before they approve it.

## ADDED Requirements

### Requirement: Approval warns on an identity-provider login
When an agent asks to share private fields of a login whose websites include the identity provider's site (`google.com` or any of its subdomains), the approval page SHALL show a warning that this login also signs in to every site that uses it, before the person decides. The person SHALL still be able to share it.

#### Scenario: Agent asks for a Google login
- **WHEN** an agent requests the password of a login whose website is `https://accounts.google.com/`
- **THEN** the approval page shows the identity-provider warning above the "Share once" button

#### Scenario: Person shares it anyway
- **WHEN** the person presses "Share once" on that request
- **THEN** the fields are shared exactly as for any other login

#### Scenario: Ordinary login
- **WHEN** an agent requests the password of a login for `https://shop.test/`
- **THEN** no identity-provider warning is shown

### Requirement: Agents are not told about the warning
The identity-provider warning SHALL appear only on the person's pages. The vault's tool results SHALL not mention it, so an agent cannot word a request around it.

#### Scenario: Tool result
- **WHEN** an agent requests a Google login's password
- **THEN** the request's tool result is the same as for any other login

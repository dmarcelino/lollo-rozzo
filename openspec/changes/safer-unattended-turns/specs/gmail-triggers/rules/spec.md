# Spec Delta

## Purpose

Decides who may set off a Gmail rule's unattended turn: a sender list the person wrote, or an explicit choice that anyone may. Also covers how mail from senders a rule has not seen before is announced and limited.

## ADDED Requirements

### Requirement: A rule names its senders or says anyone may
The rule form SHALL refuse to save a rule that has neither a sender list nor the "Anyone may set this off" switch turned on, and SHALL refuse a rule that has both. Only the person can set either, on the Access-protected page.

#### Scenario: No senders, switch off
- **WHEN** the person saves a rule with an empty sender list and the switch off
- **THEN** the form is shown again with a problem asking for senders or for the switch, and nothing is saved

#### Scenario: Senders and switch
- **WHEN** the person saves a rule that lists `hal@example.com` and has the switch on
- **THEN** the form is shown again with a problem saying to leave the list empty when anyone may set it off

#### Scenario: Sender list only
- **WHEN** the person saves a rule that lists `hal@example.com` with the switch off
- **THEN** the rule is saved and only mail from `hal@example.com` matches it

### Requirement: Anyone rules are labelled
The rules page SHALL mark each rule that anyone may set off with a visible warning, both in the list and on its form.

#### Scenario: Rule list
- **WHEN** the person opens `/gmail-triggers/` and a rule has the switch on
- **THEN** that rule shows a warning that any sender whose mail reaches its labels can start a turn

### Requirement: Rules from before keep working as anyone rules
A rule saved before this change with no sender list SHALL become an "anyone" rule when the service first starts after the upgrade, keep matching as before, and show the warning.

#### Scenario: Upgraded rule
- **WHEN** a stack whose rule "Invoices" had no sender list is upgraded
- **THEN** "Invoices" has the switch on, still starts turns for matching mail, and is labelled as an anyone rule

#### Scenario: Rule with senders after upgrade
- **WHEN** a stack whose rule lists `hal@example.com` is upgraded
- **THEN** the rule keeps its list and the switch stays off

### Requirement: A new sender on an anyone rule is announced
On an "anyone" rule, the first matching mail from a sender the rule has not seen before SHALL send the person one notice, through the notifications service. The notice names the rule and the sender's address, never the subject or body, and links to the rule's page. A sender counts as seen once its mail has matched the rule, whether or not that mail ran.

#### Scenario: First mail from a stranger
- **WHEN** mail from `hal@example.com` matches the anyone rule "Invoices" for the first time
- **THEN** the person's devices show one "Gmail triggers: " notice naming "Invoices" and `hal@example.com`, which opens the rule under `/gmail-triggers/`

#### Scenario: Second mail from the same sender
- **WHEN** mail from `hal@example.com` matches "Invoices" again
- **THEN** no further notice is sent for that sender

#### Scenario: Rule with a sender list
- **WHEN** mail from a listed sender matches a rule whose switch is off
- **THEN** no new-sender notice is sent

#### Scenario: Senders known before the upgrade
- **WHEN** a stack is upgraded and `hal@example.com` appears in the run history of an anyone rule
- **THEN** that sender counts as seen and its next mail sends no notice

### Requirement: New senders per rule are capped
An anyone rule SHALL start runs for mail from at most a fixed number of senders new to it in any 24 hours (3 by default). Mail from further new senders in that window SHALL be set aside without starting a turn and SHALL be shown on the rule's page with its count. A sender whose mail was set aside SHALL get no second set-aside notice, but SHALL stay new to the cap until its mail has started a run once, and SHALL get one more notice when it does.

#### Scenario: Fourth new sender in a day
- **WHEN** an anyone rule has already started runs for three new senders in the last 24 hours, and mail from a fourth new sender matches it
- **THEN** no turn starts for that mail, the person gets that sender's notice saying its mail was set aside, and the rule's page shows the set-aside count

#### Scenario: A set-aside sender mails again
- **WHEN** the cap is still reached and a sender whose mail was set aside earlier that day mails the rule again
- **THEN** that mail is set aside too, no turn starts, and no second notice is sent

#### Scenario: A set-aside sender once there is room
- **WHEN** a sender's mail was set aside and, more than 24 hours later with room under the cap, its next mail matches the rule
- **THEN** that mail starts a run, counts against the cap, and the person gets a notice that it started a task

#### Scenario: Known sender after the cap
- **WHEN** the cap is reached and mail arrives from a sender whose mail has started a run before, or who was known before the upgrade
- **THEN** it runs within the rule's hourly limit as usual

### Requirement: Notices never hold up or depend on a run
A run SHALL NOT wait for a notice to be accepted, and a notice the notifications service refuses or cannot take SHALL be logged and decide nothing else. The service SHALL log the rule id and the outcome of a notice, never the sender's address.

#### Scenario: Notifications service down
- **WHEN** mail from a new sender matches an anyone rule under its cap while the notifications service is unreachable
- **THEN** the run is queued as usual and the failed notice is logged without the sender's address

# browser/sessions Specification

## Purpose

Decides which sign-ins an agent's browser task starts with and what survives it: signed out by default, a sign-in with vault values only after the person's approval in the vault, and kept sign-ins only for the sites the person chooses.

## Requirements

### Requirement: Every task starts in a new isolated context
Each task SHALL get a new browser context that shares no cookies, storage, cache or service workers with any other task, and that is destroyed, with all its data, when the task ends.

#### Scenario: Two tasks at once
- **WHEN** one agent signs in to a site in task A and another agent opens the same site in task B
- **THEN** task B is not signed in

#### Scenario: Task ends
- **WHEN** a task ends
- **THEN** its context's cookies and storage are gone, apart from what kept sign-ins save (below)

### Requirement: Signed out unless the person keeps a sign-in
A new task SHALL start signed out of every site, except sites the person marked "keep me signed in" on `/browser/`. Agents SHALL have no tool to mark or unmark a site.

#### Scenario: Site not kept
- **WHEN** an agent signed in to `shop.example.com` in an earlier task and the person never chose to keep it
- **THEN** a new task opens `shop.example.com` signed out

#### Scenario: Agent tries to keep a sign-in
- **WHEN** an agent looks for a way to keep a sign-in
- **THEN** no tool offers it, and only the person's signed-in `/browser/` page can change it

### Requirement: Kept sign-ins are saved per site
For a site the person keeps, the browser SHALL save that site's cookies and storage when a task ends and load them into every new task. It SHALL save nothing for other sites. A site SHALL mean a registrable domain, such as `example.com` for `shop.example.com` and `accounts.example.com`.

#### Scenario: Person keeps a site
- **WHEN** the person marks `example.com` "keep me signed in" and a task signed in to `shop.example.com` ends
- **THEN** the next task opens `shop.example.com` signed in

#### Scenario: Other sites in the same task
- **WHEN** that same task also signed in to `other.test`, which is not kept
- **THEN** nothing of `other.test` is saved

#### Scenario: Overlapping tasks
- **WHEN** task A loads a kept sign-in, task B signs in again on that site and ends, and then A ends
- **THEN** B's newer sign-in stays saved, and a task that only loaded a sign-in without visiting the site saves nothing for it

### Requirement: Forgetting a kept sign-in
The person SHALL be able to forget one kept site, or all of them, on `/browser/`. Forgetting SHALL delete the saved data at once and stop saving the site, and tasks already open SHALL keep what they loaded until they end.

#### Scenario: Forget a site
- **WHEN** the person presses "forget" for `example.com`
- **THEN** its saved cookies and storage are deleted, the site is no longer kept, and the next task opens it signed out

#### Scenario: Forgotten while a task is open
- **WHEN** the person forgets a site, keeps it again, and a task opened before that ends
- **THEN** the task's old sign-in is not saved back

### Requirement: Sign-in with vault values goes through the vault's approval
The browser SHALL never request anything from the vault. An agent SHALL put a vault login's values into the browser only by piping `vault-run` and `vault-fill` into the secret and one-time-code tools, after the person approved the request on the vault page.

#### Scenario: Agent signs in with a vault login
- **WHEN** an agent needs to sign in to `shop.example.com`
- **THEN** it reads the login's public username and website from the vault's list, requests the password and, if needed, `totp` with a purpose naming the site and the task, and types the private values only after the person approves

#### Scenario: Browser and vault
- **WHEN** the browser runs
- **THEN** it makes no request to the vault, and the engine, where pages run, is not on a network with it

### Requirement: The person can sign in by hand
The person SHALL be able to sign in to a site themselves through the live view on `/browser/`, in a task's tab, without the vault.

#### Scenario: Person signs in for the agent
- **WHEN** an agent requests a hand-off because a site needs a sign-in and the person signs in through the live view
- **THEN** the agent continues in that task signed in, and the sign-in is kept only if the person keeps the site

### Requirement: Kept sign-ins are stated as shared within the stack
Docs SHALL state that every agent of the stack can use a kept sign-in without asking the vault, because agents share one app-server as root.

#### Scenario: Guide's security model
- **WHEN** a person reads the browser guide's security model
- **THEN** it says a kept sign-in is usable by every agent of their stack, and that keeping a site with a saved card lets agents spend there without a vault step

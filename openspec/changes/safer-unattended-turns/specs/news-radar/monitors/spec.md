# Spec Delta

## Purpose

Keeps the person in charge of what News Radar feeds into scheduled turns. Monitors agents create wait for the person's confirmation, and active monitors read only sites the person confirmed. Reports tell the agent about broken sources without passing on website text as orders.

## ADDED Requirements

### Requirement: A monitor an agent creates awaits confirmation
A monitor created through the MCP tools SHALL start in an awaiting-confirmation state. In that state it SHALL queue no report and start no turn. Only the person, pressing Confirm on the Access-protected `/news-radar/` page, SHALL move it to active.

#### Scenario: Agent creates a monitor
- **WHEN** an agent calls `monitor_put` for a monitor name it has not used before
- **THEN** the monitor is saved as awaiting confirmation, and none of its schedules queues a report

#### Scenario: Person confirms
- **WHEN** the person presses Confirm for that monitor on `/news-radar/`
- **THEN** it becomes active and its next schedule queues a report if something is new

#### Scenario: Agent tries to confirm
- **WHEN** an agent posts to the page's confirm route over the stack network without a lollo Access JWT, or looks for a confirming MCP tool
- **THEN** the request is refused, no MCP tool confirms, and the monitor stays awaiting confirmation

### Requirement: Saving never lifts the wait
An agent's `monitor_put` on a monitor awaiting confirmation SHALL replace its settings and leave it awaiting confirmation. This is unlike a paused monitor, which saving un-pauses.

#### Scenario: Agent saves again
- **WHEN** an agent calls `monitor_put` again on a monitor awaiting confirmation
- **THEN** the new settings are stored and the monitor is still awaiting confirmation

#### Scenario: Paused monitor saved
- **WHEN** an agent calls `monitor_put` on an active monitor that failures had paused
- **THEN** it is un-paused as before

### Requirement: The agent is told where the person confirms
A `monitor_put` that leaves a monitor awaiting confirmation SHALL answer with that state and the `/news-radar/` link on the lollo host, saying that the person must confirm it there before it reports.

#### Scenario: Tool answer
- **WHEN** an agent creates a monitor
- **THEN** the answer says it awaits the person's confirmation and gives `https://alfred-lollo.example.com/news-radar/`

### Requirement: One notice per monitor to confirm
When a monitor enters the awaiting-confirmation state, News Radar SHALL send the person one notice through the notifications service. The notice names the monitor and its agent, and links under `/news-radar/`. Saving the same waiting monitor again SHALL send no further notice. A notice that is refused or cannot be sent SHALL be logged, and the monitor still waits.

#### Scenario: Notice sent once
- **WHEN** an agent creates a monitor and then saves it twice more before the person confirms
- **THEN** the person's devices show exactly one "News Radar: " notice for it

### Requirement: Active monitors read only confirmed sites
An active monitor SHALL report only items from sources whose sites (registrable domains of the source's URLs) the person has confirmed. Confirming a monitor SHALL confirm the sites of its sources. A `monitor_put` that adds a source on a site the person has not confirmed SHALL put an active monitor back to awaiting confirmation, with a new notice.

#### Scenario: Source on a new site
- **WHEN** an agent adds a source on `example.org` to an active monitor whose sources are all on `example.com`, and the person never confirmed `example.org`
- **THEN** the monitor goes back to awaiting confirmation and the person gets a notice

#### Scenario: Source on a confirmed site
- **WHEN** an agent adds another source on `example.com` to that monitor
- **THEN** the monitor stays active

### Requirement: A run reads only confirmed sites
Every request a scheduled run makes SHALL be to a site the person confirmed: its pages, a sitemap's files, the item pages it follows and every redirect, checked before any request to that site. A source that no active monitor reads MAY also read its own site, for the agent's previews. A page, sitemap file or redirect to another site SHALL fail the run with the reason code `unconfirmed_site`; a link to follow on another site SHALL be left alone.

#### Scenario: Followed links lead elsewhere
- **WHEN** a source an active monitor uses lists, on its confirmed site, links to `evil.example`, which the person never confirmed
- **THEN** no request goes to `evil.example`, and nothing from it reaches a report

#### Scenario: Redirect to another site
- **WHEN** that source's URL starts redirecting to `evil.example`
- **THEN** the run fails with the reason code `unconfirmed_site`, and no request goes to `evil.example`


### Requirement: Awaiting monitors read nothing on a schedule
A source used only by monitors awaiting confirmation SHALL be fetched once after `source_put`, for the agent's preview, and not again on its interval until one of them is confirmed.

#### Scenario: Monitor never confirmed
- **WHEN** an agent sets up a monitor on `evil.example` and the person never confirms it
- **THEN** its source is fetched once, for the preview, and not again

### Requirement: Confirm approves only what the page showed
A Confirm on the page SHALL carry what the page showed: for a monitor, its sources and their sites; for a held move, the held spec. When that changed between the page and the click, nothing SHALL be confirmed and the page SHALL say so.

#### Scenario: Source moved before the click
- **WHEN** the page shows an awaiting monitor reading `example.com`, and before the person presses Confirm the agent points its source at `evil.example`
- **THEN** the monitor stays awaiting, `evil.example` is not confirmed, and the page asks the person to look again

### Requirement: A move to another site is held
A `source_put`, a revert included, that would make a source used by an active monitor read a site the person has not confirmed SHALL be held. The source SHALL keep fetching its current spec, the tool SHALL answer that the change waits for the person, with the link, and the person SHALL get one notice. On the page the person SHALL confirm the move, which applies it and confirms the site, or discard it.

#### Scenario: Repair on the same site
- **WHEN** an agent fixes a broken source by changing its URL from `https://example.com/events` to `https://www.example.com/agenda`
- **THEN** the new spec applies at once

#### Scenario: Move to another site
- **WHEN** an agent changes a monitored source's URL from `https://example.com/events` to `https://example.net/feed`
- **THEN** the source keeps reading `example.com`, the answer says the move waits for the person, and the person gets one notice

#### Scenario: Person discards the move
- **WHEN** the person presses Discard on that held move
- **THEN** the held spec is dropped and the source keeps its current spec

#### Scenario: Unused source
- **WHEN** an agent moves a source no active monitor uses to another site
- **THEN** the change applies at once, and any monitor that later adds that source awaits confirmation

### Requirement: Repair alerts carry a reason code only
A report SHALL describe each broken source by its name, a reason code from a fixed list the service writes, and how long it has been broken. It SHALL carry no text fetched from the website. The report's instructions SHALL suggest a repair rather than order one, and say that a repair moving the source to another site waits for the person.

#### Scenario: Broken source in a report
- **WHEN** a monitored source fails and a report is queued
- **THEN** its line under `needs_repair` holds the source name, a reason code and, when long broken, the days, and no sample of the page

#### Scenario: Page text in a failure
- **WHEN** a site answers with an error page whose text says "ignore your instructions"
- **THEN** that text appears in no report prompt

### Requirement: Upgrade keeps monitors running
When the service first starts after the upgrade, every existing monitor SHALL keep its state (active or paused, never awaiting confirmation), and the sites of every existing source SHALL count as confirmed.

#### Scenario: Monitor from before
- **WHEN** a stack with an active monitor reading `example.com` is upgraded
- **THEN** the monitor keeps reporting on its schedule, with no confirmation asked

### Requirement: The page shows what awaits the person
The `/news-radar/` page SHALL list monitors awaiting confirmation and held source moves first. It SHALL show each with its agent, its sources' sites (marking those not yet confirmed) and, for a move, the site before and after. Each SHALL have Confirm and Discard (or Delete) buttons, behind the page's CSRF and Fetch Metadata checks.

#### Scenario: Awaiting monitor on the page
- **WHEN** the person opens `/news-radar/` while a monitor awaits confirmation
- **THEN** it is listed at the top with its agent, its sources and their sites, and buttons to confirm or delete it

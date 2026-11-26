# Proposal

## Why

Gmail triggers and News Radar are the two ways a turn starts with nobody watching, and both can be aimed by an outsider. A Gmail rule with no sender list fires on any DMARC-aligned mail, the attacker's own domain included (SEC-014). A monitor that one misled agent registers keeps feeding text chosen by a website into scheduled turns, and its repair alerts order the agent to act on that text (SEC-013). This is the realistic first step of the audit's chains 1 and 2: the agent becoming the person, and silent, lasting theft. Making these two entry points safer is the cheap way to make SEC-002 and SEC-003 less likely, since neither has a cheap fix at the impact end.

## What Changes

- **Two new notification senders.** `lollo-gmail-triggers` and `lollo-news-radar` join the vault and the browser as senders of `lollo-notifications`. Each gets its own token. Their links sit under `/gmail-triggers/` and `/news-radar/`, and their titles carry the labels "Gmail triggers: " and "News Radar: ". The notifications service now runs whenever any of the four profiles is on.
- **Gmail rules name who may set them off.** The rule form requires a sender list, or an explicit "Anyone may set this off" switch. Rules saved before this change with no sender list become "anyone" rules, keep working, and are labelled with a warning.
- **New senders on "anyone" rules are announced and capped.** On an "anyone" rule, the first mail from a sender not seen before on that rule sends a notice to the person. A rule starts runs for at most a few new senders per day (3 by default). Mail from further new senders that day is set aside and shown on the page, not run.
- **Agent-made monitors wait for the person.** A monitor an agent creates starts as *awaiting confirmation*. That state is distinct from *paused*, and an agent's `monitor_put` never lifts it. It reports nothing until the person confirms it on `/news-radar/`. The tool's answer gives the agent the link to pass on, and the person gets one notice per monitor.
- **News Radar reads only sites the person confirmed.** An active monitor reports only from sources on sites (registrable domains) the person confirmed. If a `monitor_put` adds a source on an unconfirmed site, the monitor goes back to awaiting confirmation. If a `source_put` moves a source that an active monitor uses to an unconfirmed site, the new spec is held, and the old one stays in use until the person confirms or discards the move. Repairs on the same site apply at once.
- **Repair alerts carry a reason code only.** A broken source reaches the report prompt as its name and a fixed reason code, with no sample of website text. The prompt suggests a repair instead of ordering one.
- **Upgrade keeps what exists.** Existing monitors stay active, and every site their sources use now counts as confirmed. Existing Gmail rules keep firing; senders already seen in a rule's run history count as known.

## Capabilities

### New Capabilities
- `gmail-triggers/rules`: who may set off a Gmail rule (a sender list or an explicit "anyone"), and how new senders on an "anyone" rule are announced and capped.
- `news-radar/monitors`: the person's confirmation of agent-made monitors and of the sites they read, and what a monitor's report tells the agent about broken sources.

### Modified Capabilities
- `notifications/delivery`: the senders are now the vault, the browser, Gmail triggers and News Radar. This changes when the service runs, which tokens exist, each sender's own prefix and tag, and each sender's label.
- `notifications/devices`: a tap may also open `/gmail-triggers/` and `/news-radar/`.

## Impact

- **Code:**
  - `gmail-triggers/` (rule model and migration, form, matcher and planning, a table of seen senders, notices, page)
  - `news-radar/` (monitor state, confirmed sites, held source moves, MCP tool answers, digest prologue and alerts, page routes, notices; adds the pinned `tldextract` with its bundled suffix list, as the browser has)
  - `notifications/` (labels, the service worker's prefixes)
  - `lib/common.sh` (`NOTIFY_SOURCES`)
  - `compose/notifications.yml`, `compose/gmail-triggers.yml`, `compose/news-radar.yml`
  - `tests/run.sh`
- **Operations:**
  - The first `up` after the upgrade makes two new tokens in `stack.env` and recreates `lollo-notifications`, `lollo-gmail-triggers` and `lollo-news-radar` once. The vault is not recreated.
  - A stack with Gmail triggers or News Radar but neither the vault nor the browser now runs the notifications service. `up` tells the operator to run `lolloctl profiles` once so the tunnel routes `/notifications/`.
- **People:**
  - One tap per agent-created monitor, and per move of a monitored source to a new site.
  - One more choice on the Gmail rule form.
  - Notices for new senders on "anyone" rules.
- **Docs:**
  - `docs/gmail-triggers.md`, `docs/news-radar.md`, `docs/notifications.md` and their flow diagrams
  - The `notifications/`, `gmail-triggers/` and `news-radar/` READMEs
  - ARCHITECTURE.md, docs/architecture.md, and the Notifications and News Radar lines in CLAUDE.md
- **Out of scope:** splitting the stack network (SEC-006); a restricted tool profile for triggered turns (an upstream seam); the egress chokepoint (SEC-003).

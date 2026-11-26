# Design

## Context

See proposal.md for why. The current state this design builds on:

- **Senders.** `lollo-notifications` takes any `NOTIFY_TOKEN_<SOURCE>` in its environment as a sender. `read_tokens` maps `NOTIFY_TOKEN_GMAIL_TRIGGERS` to the source `gmail-triggers`. `check_payload` already holds each source to `/<source>/` and `<source>-`. What is fixed in code are the list `NOTIFY_SOURCES` in `lib/common.sh` (which makes the tokens, checks their holders and feeds `notify_profiles`), the profiles and environment in `compose/notifications.yml`, `LABELS` in the service, and `PREFIXES` in `sw.js`. Senders use the client in `lollo_common.notify`.
- **Gmail triggers.** Rules are made only by the person on the page; agents can't reach them. `rule_matches` skips the sender test when `rule.senders` is empty. The poller matches, then `plan` turns matches into held items and queued runs within `max_per_hour`. Each run row keeps its messages, with their senders, as JSON.
- **News Radar.** Every monitor is created by an agent through `monitor_put`; the page only lists, deletes and changes where a monitor reports. `paused` exists for failures, and `put_monitor` clears it on every save. Sources are specs saved by `source_put` after a passing `source_test`. Alerts carry `unhealthy_reason` and a `sample` of the page. The prologue orders "fix each now". The store adds columns to `monitors` at start for older databases.

## Goals / Non-Goals

**Goals:**
- One sender mechanism for both services, with no change to the notifications service's rules beyond its lists.
- No working rule or monitor stops at the upgrade.
- Every new gate is one the person passes on an Access-protected page, never through an MCP tool.

**Non-Goals:**
- Limiting what a triggered turn may do (an upstream tool profile).
- Stopping an agent from fetching an attacker's URL through `source_test` or `source_inspect` (an egress problem, SEC-003).
- Checking which agent calls News Radar's tools (SEC-006).

## Decisions

### 1. Senders are rows in the existing lists, not a new mechanism
Add `GMAIL_TRIGGERS lollo-gmail-triggers` and `NEWS_RADAR lollo-news-radar` to `NOTIFY_SOURCES`. That one change already gives:
- `ensure_notify_secrets` makes the two tokens, whatever the profiles.
- `check_notify_secrets` holds each token to its sender and the hub.
- `notify_profiles` turns the hub on with either profile.

Beyond that list:
- **Compose:** add the two profiles to `lollo-notifications`'s `profiles`, its two `NOTIFY_TOKEN_*` variables, and `NOTIFY_TOKEN` to each sender's overlay.
- **Service:** add the labels "Gmail triggers" and "News Radar" (the generic `capitalize()` would give "Gmail-triggers").
- **Service worker:** add `/gmail-triggers/` and `/news-radar/` to `sw.js`'s `PREFIXES`.

*Alternative:* let the vault relay other services' notices. Rejected: it would break "a sender says only what is its own", and give the vault a reason to accept requests from other sidecars.

### 2. Gmail: an explicit `anyone` column, migrated from empty sender lists
- **Column and migration:** add `anyone INTEGER NOT NULL DEFAULT 0` to `rules`. When the column is first added, the same transaction sets `anyone = 1` on every rule whose `senders` is `'[]'`. That way the migration runs exactly once, and a rule the person later edits is never silently turned into an "anyone" rule.
- **Form:** refuses neither or both. The template shows a warning pill on the list and the form.
- **Matcher:** `rule_matches` is unchanged for "anyone" rules (an empty list still means any sender). A defensive check keeps a rule with no senders and `anyone = 0` (it cannot be saved, but a hand-edited database could hold one) matching nothing.

*Alternative:* treat an empty list as "anyone" and only add the label. Rejected: the audit's point is that the person must choose. An empty list that silently means everyone is the problem itself.

### 3. Gmail: seen senders per rule, cap applied in planning
- **New table:** `rule_senders (rule_id, sender, first_seen, ran_at INTEGER)`. `ran_at` records when a sender's mail first started a run; it is NULL while all its mail was set aside, so a set-aside sender stays new to the cap (mailing again does not get round it), and 0 for senders seeded from the run log (known, not counted).
- **Seeding:** when the table is created, it is filled from each rule's run history (`runs.messages[].sender`), so the upgrade sends no burst of notices.
- **Planning:** happens after `match`. For an "anyone" rule, each message from a sender not in `rule_senders` is recorded:
  - If fewer than `NEW_SENDERS_PER_DAY` (3, a constant in `config.py`, like the other limits) senders have a `ran_at` in the last 24 hours, it takes the normal path (held, then queued within `max_per_hour`), and a notice is queued.
  - Otherwise it is marked handled without a run, and counted on the rule (a `set_aside` count shown on the page next to the existing gap list), and its notice says the mail was set aside.
- **Notices:** sent after the poll's database transaction commits, from a small queue drained by the poller, so a slow notifications service never blocks a poll. The tag is `gmail-triggers-new-sender-<rule id>`, the link `/gmail-triggers/r/<rule id>`. A sender gets one notice when first seen and, if it was set aside then, one more when its mail later runs; mail from a set-aside sender stays set aside, without another notice, until there is room under the cap.

*Why 3:* a rule that really is for anyone (a shared alias, a form) still works on a normal day. A spray of hostile senders reaches at most three turns a day per rule, and the person hears of each sender.

*Alternative:* hold all new-sender mail until the person approves it. Rejected for this round: it makes an "anyone" rule useless, and the switch already makes it the person's explicit choice.

### 4. News Radar: a third state and a set of confirmed sites
- **State:** add `awaiting INTEGER NOT NULL DEFAULT 0` to `monitors`, kept apart from `paused` so `put_monitor`'s "saving un-pauses" never touches it. `fire` and digest building skip `awaiting = 1` monitors exactly as they skip paused ones.
  - New table `confirmed_sites (site TEXT PRIMARY KEY, at INTEGER)`.
  - The upgrade adds the column with default 0 (existing monitors stay as they are). When the table is first created, it is filled with the sites of every stored source spec.
- **`monitor_put`:** after the existing checks, `awaiting` becomes 1 when the monitor is new, or when any of its sources has a site not in `confirmed_sites`. It stays 1 when it already was. A monitor that enters `awaiting` sends one notice (tag `news-radar-confirm-<monitor id>`, link `/news-radar/#m-<id>`), never again while it waits. The answer adds `awaiting_confirmation: true` and `confirm_at: <PUBLIC_ORIGIN>/news-radar/`, and the tool description says the agent must give the person that link.
- **What a run reads:** the URLs a spec names (its `url` and its pages) are the agent's choice, so a run checks them against the confirmed sites, plus the source's own site while no active monitor reads it, which the preview needs; a URL elsewhere fails the run, before any request, with the reason code `unconfirmed_site`. Where a confirmed site leads (redirects, a sitemap index's files, followed links) is that site's choice and is not checked against the list. *Alternative:* check every request. Rejected after review: existing feeds behind a redirect (a feed host, a moved domain) and listings of articles on other sites would break on upgrade. What stays open is an agent pointing a source at an open redirect on a confirmed site, or choosing which of a confirmed listing's links to follow.
- **Schedules:** a source is due on its interval only when an active monitor uses it; a source of an awaiting monitor gets its one fetch after `source_put`, for the preview, and nothing more until Confirm.
- **The page:**
  - Each Confirm form (a monitor's, a held move's) posts a fingerprint of what the page showed (the sources and their sites, or the held spec). Confirm refuses, with "an agent changed this since the page showed it", when that no longer matches. *Alternative:* hold source moves for awaiting monitors too. Rejected: it misses changes to the monitor's own source list, and makes an agent wait on every fix while it sets a monitor up.
  - Confirm (`POST /m/{mid}/confirm`, the same `page(..., post=True)` wrapper as delete, so Access, CSRF and Fetch Metadata apply) clears `awaiting` and inserts the sites of the monitor's sources.
  - Delete stays.
- **Site:** the registrable domain of each URL a spec fetches from (its `url` and its `pages`), using `tldextract` pinned to the version the browser uses, with the bundled suffix list and no fetch, exactly as `browser/service/.../sites.py` does. IP addresses are refused by the fetcher already; a host with no registrable domain counts as its own site.

*Alternative:* move a site helper into `lollo-common`. Rejected: every image that uses `lollo-common` would gain `tldextract`. Two services with the same pinned snapshot is less weight, and the tests hold both to the same cases.

### 5. News Radar: held source moves
- **Storage:** new table `held_specs (name PRIMARY KEY, spec TEXT, at INTEGER, notified INTEGER)`.
- **`source_put`, a revert included:** works out the new spec's sites. If any is unconfirmed *and* the source is used by an active monitor (`awaiting = 0`), the spec is stored in `held_specs` (replacing an earlier held one) and the current spec is left as is. The answer says `held: true` with the link, and one notice goes out per held spec.
- **Otherwise** it applies as today.
- **Page:** Confirm applies the held spec through the same `put_source` path (with `reset`) and confirms its sites. Discard deletes the row.
- **`source_list` and `source_status`** show a held move, so an agent does not keep retrying.

The rule "held only when an active monitor uses it" keeps the one invariant, *an active monitor never reads an unconfirmed site*, with the least friction. An unused source can move freely, because adding it to a monitor goes through decision 4.

### 6. Reason codes and a suggesting prologue
- **Reason codes:** `unhealthy_reason` is stored from a fixed vocabulary that `runner.py` and `fetch.py` write: `http_status`, `robots`, `challenge`, `timeout`, `not_feed`, `no_items`, `extract_failed`, `refused_address`, `too_large`, `unconfirmed_site`, and `other` for anything not mapped. A free-text reason from an exception is never stored as the reason.
- **Alerts:** `_alert_line` drops `sample`.
- **Prologue:** the sentence becomes a suggestion. When the person follows the source and the fix looks clear, the agent may repair it with the monitoring tools and say what it did. A repair that moves the source to another site waits for the person's confirmation, and if the agent can't tell why the source broke, it should say so.
- **Unchanged:** the sample stays visible on the page and in `source_status`, where an agent asks for it in a turn of its own, already marked as untrusted text.

## Risks / Trade-offs

- [An agent keeps calling `monitor_put` to repeat a notice] → One notice per entry into the waiting state; saves while waiting send none, and the notifications service's per-sender rate limit caps the rest.
- [A person confirms without reading] → The page shows the sites, marking new ones, and the agent's name. This is a person's judgement, as with vault approvals; it is not a technical boundary.
- [The first upgrade marks every existing source's site as confirmed, including one an attacker had already planted] → Accepted. The page already lists every monitor and source, and the docs say to review them after upgrading.
- [The seen-sender seed misses senders whose runs aged out of the run log (`RUNS_KEEP_PER_RULE`)] → They send one notice on their next mail; that is all.
- [Gmail notices name a sender's address on the person's devices] → The payload is end-to-end encrypted to the device by Web Push. Subjects and bodies are never sent, and logs carry the rule id and outcome only.
- [`lollo-notifications` restarts once at the upgrade; a vault erase during those seconds is refused] → The same as any hub recreate today. The release notes say so.
- [Reason codes hide useful detail from an unattended turn] → The detail is one `source_status` call away, in a turn the agent chooses to make.

## Migration Plan

1. Ship the code with the new columns and tables. Each is created with the "first start after upgrade" seed described above, so stores are migrated in place, once.
2. `lolloctl up` makes `NOTIFY_TOKEN_GMAIL_TRIGGERS` and `NOTIFY_TOKEN_NEWS_RADAR` (`ensure_notify_secrets`), and recreates the hub and the two sidecars. On a stack whose only senders are the new ones, it says to run `lolloctl profiles` once (the existing upgrade message, which `notify_profiles` now covers).
3. **Rollback:** an older image ignores the extra columns and tables. Gmail rules flagged "anyone" behave as they did, and monitors awaiting confirmation would start reporting, which is the old behaviour. The extra tokens in `stack.env` are harmless to an older `lolloctl`.

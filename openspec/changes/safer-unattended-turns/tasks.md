# Tasks

## 1. Gmail triggers and News Radar become notification senders

- [x] 1.1 Add `GMAIL_TRIGGERS lollo-gmail-triggers` and `NEWS_RADAR lollo-news-radar` to `NOTIFY_SOURCES` in `lib/common.sh`. Verify that `tests/run.sh test_add_gives_the_notifications_service_its_keys_and_tokens` and `test_up_keeps_the_notify_keys_and_tokens` pass with the two new tokens asserted (extend their assertions).
- [x] 1.2 Wire the tokens in Compose:
  - in `compose/notifications.yml`, add the two profiles to `lollo-notifications`'s `profiles` and `NOTIFY_TOKEN_GMAIL_TRIGGERS` and `NOTIFY_TOKEN_NEWS_RADAR` to its environment
  - pass `NOTIFY_TOKEN: ${NOTIFY_TOKEN_GMAIL_TRIGGERS:-}` in `compose/gmail-triggers.yml` and `NOTIFY_TOKEN: ${NOTIFY_TOKEN_NEWS_RADAR:-}` in `compose/news-radar.yml`
  - update the files' header comments

  Verify that `tests/run.sh test_the_notifications_overlay_has_exactly_the_senders_profiles`, `test_the_overlays_hand_each_notify_secret_only_to_its_holders` and `test_compose_renders_the_notifications_service_with_either_sender_only` pass. Extend the last one: the hub renders with either new profile alone.
- [x] 1.3 Extend the `tests/run.sh` stubs' rendered configs (the `config_edit` fixtures that carry `NOTIFY_TOKEN_*`) and add `test_check_refuses_news_radar_holding_the_gmail_token`. Verify it fails before 1.1–1.2 and passes after.
- [x] 1.4 Cover the upgrade message: on a stack with only `lollo-gmail-triggers` on, `up` says once to run `lolloctl profiles` for the `/notifications/` route. Verify with a new `test_up_says_once_to_route_notifications_for_gmail_triggers_alone` next to `test_up_says_once_to_route_notifications_on_an_upgraded_stack`.
- [x] 1.5 In `notifications/`, add the labels "Gmail triggers" and "News Radar" to `LABELS`, and add `/gmail-triggers/` and `/news-radar/` to `PREFIXES` in `static/sw.js`. Verify with pytest cases in `test_send.py` (a new sender's title gets its label; News Radar's token linking to `/vault/` or tagged `vault-` is refused) and a service-worker test that a `/news-radar/` link opens there. Run `tests/run-sidecar.sh notifications`.
- [x] 1.6 Update `notifications/README.md` (senders list, Rules), `compose/README.md` (variables line), ARCHITECTURE.md (the sender row and when the service is on), `docs/architecture.md`, `docs/notifications.md` and the `notifications-flow` diagram (json, html, svg), and the Notifications directive in CLAUDE.md ("a sender (the vault, the browser, Gmail triggers, News Radar)"). Verify that `node scripts/check-doc-links.mjs` passes.

## 2. Gmail rules name who may set them off

- [x] 2.1 Add the `anyone` column to `rules` in `gmail_triggers/store.py`, with the one-time migration that sets it on rules whose sender list is empty, and the `anyone` field on `Rule`. Verify with pytest: a store made from the old schema with an empty-sender rule opens with `anyone = True`; a rule with senders keeps `anyone = False`; reopening runs no second migration.
- [x] 2.2 Make the rule form require a sender list or the "Anyone may set this off" switch, refusing neither and both; show a warning pill on anyone rules in `triggers.html` and `rule_form.html`. Verify with pytest in `test_app.py` for the three cases in the spec, and that the list shows the warning.
- [x] 2.3 Make `rule_matches` match nothing for a rule with no senders and `anyone` off. Verify with a case in `test_matcher.py`.
- [x] 2.4 Add the `rule_senders` table, seeded once from each rule's run history, and the cap in planning (`NEW_SENDERS_PER_DAY = 3` in `config.py`). Mail from new senders over the cap is set aside, counted on the rule and shown on its page. Verify with pytest in `test_triggers.py` or `test_poller.py`:
  - the first three new senders in 24 hours run
  - the fourth is set aside and counted
  - a known sender still runs after the cap
  - the seed counts senders from run history
- [x] 2.5 Send a new-sender notice through `lollo_common.notify` from a queue the poller drains after the poll's commit: title naming the rule, body naming the sender's address (and "set aside" when it was), link `/gmail-triggers/r/<id>`, tag `gmail-triggers-new-sender-<id>`. Log the rule id and outcome only. Verify with pytest using a fake notifier:
  - one notice per new sender
  - none for known senders or listed-sender rules
  - a failing notifier leaves the run queued
  - no address in the logs (extend the existing "never in logs" test)
- [x] 2.6 Update `gmail-triggers/README.md` (Rules: who may set off a rule, notices), `docs/gmail-triggers.md` ("Using it" and "Security model": the switch, the warning, the cap) and the `gmail-triggers-flow` diagram. Verify that `tests/run-sidecar.sh gmail-triggers` and `node scripts/check-doc-links.mjs` pass.

## 3. News Radar monitors wait for the person

- [x] 3.1 Add `awaiting` to `monitors` and the `confirmed_sites` table, seeded once from every stored source's sites. Existing monitors keep `awaiting = 0`. Add `tldextract` to `news-radar/pyproject.toml` at the browser's pinned version, with the bundled suffix list and no fetch, and a `site_of` helper. Verify with pytest:
  - an old store opens with its monitors active and its sources' sites confirmed
  - `site_of` agrees with the browser's cases (`www.example.com` gives `example.com`)
- [x] 3.2 Make `monitor_put` set `awaiting` for a new monitor, or for any monitor with a source on an unconfirmed site. Keep it set on later saves while it waits (un-pausing a paused monitor unchanged). Answer with `awaiting_confirmation` and `confirm_at`, and update the tool description to say the agent must give the person the link. Verify with pytest in `test_service.py` for each scenario under "A monitor an agent creates awaits confirmation", "Saving never lifts the wait" and "Active monitors read only confirmed sites".
- [x] 3.3 Make `fire` and digest building skip awaiting monitors. Verify with pytest: an awaiting monitor's schedule queues no report even with new items, and the same monitor reports once confirmed.
- [x] 3.4 Add Confirm (`POST /m/{mid}/confirm`) to the page through the existing `page(..., post=True)` wrapper; it clears `awaiting` and confirms the sources' sites. List awaiting monitors first, with their agent and sites (new ones marked), in `index.html`. Verify with pytest in `test_app.py`:
  - Confirm with a valid Access JWT and CSRF activates the monitor
  - without them it is refused
  - the page lists the awaiting monitor first
- [x] 3.5 Send one notice when a monitor enters the waiting state (tag `news-radar-confirm-<id>`, link `/news-radar/`), through `lollo_common.notify` without blocking the tool call. Verify with pytest using a fake notifier: one notice across three saves; a failing notifier leaves the monitor waiting.
- [x] 3.6 Add the `held_specs` table. Hold a `source_put`, a revert included, that moves a source used by an active monitor to an unconfirmed site, with one notice. Add Confirm and Discard routes for a held move, and show held moves in `source_list`, `source_status` and on the page. Verify with pytest for each scenario under "A move to another site is held":
  - a same-site repair applies
  - a cross-site move is held, the old spec is still fetched
  - Confirm applies the move and confirms the site
  - Discard drops it
  - an unused source moves freely

## 4. Repair alerts without website text

- [x] 4.1 Store `unhealthy_reason` only from a fixed vocabulary of reason codes written by `runner.py` and `fetch.py`, mapping anything else to `other`. Verify with pytest: a failing fetch whose error page text says "ignore your instructions" stores a code, never that text.
- [x] 4.2 Drop `sample` from `_alert_line`, and rewrite the prologue's repair sentence as a suggestion that says a move to another site waits for the person. Verify with pytest in `test_service.py` or a digest test: a report prompt with a broken source holds its name, code and days, and no sample text; the prologue no longer says "fix each now".
- [x] 4.3 Update the `news-radar-sources` skill in `agent-skills/` where it describes repairs, awaiting monitors and held moves, keeping the text generic. Verify that `tests/run.sh test_skills_` passes and the skill names no upstream skill.
- [x] 4.4 Update `news-radar/README.md` and its DESIGN.md: Rules (the "second written exception" paragraph now says a monitor starts turns only once the person confirmed it, and that agents can't confirm), plus a new rule "Active monitors read only confirmed sites". Also update:
  - `docs/news-radar.md` ("Using it", and "Security model": agents set monitors up, the person confirms them)
  - the `news-radar-flow` diagram
  - the News Radar directive in CLAUDE.md ("a monitor starts a turn only once the person confirmed it, at its schedule, ...")

  Verify that `tests/run-sidecar.sh news-radar` and `node scripts/check-doc-links.mjs` pass.

## 5. Integration

- [x] 5.1 Run every check the touched folders list:
  - `tests/run.sh`
  - `shellcheck -x lolloctl lib/*.sh tests/run.sh`
  - `tests/run-sidecar.sh notifications`
  - `tests/run-sidecar.sh gmail-triggers`
  - `tests/run-sidecar.sh news-radar`
  - `tests/run-sidecar.sh landing`
  - `node --test 'landing/executor/tests/*.test.mjs'`
  - `node scripts/check-doc-links.mjs`

  Verify all pass.
- [ ] 5.2 On a throwaway stack with Gmail triggers, News Radar and notifications on, without heavy end-to-end jobs beside live stacks:
  - an agent creates a monitor: one notice arrives, and its tap opens `/news-radar/`
  - the monitor reports only after Confirm
  - a test mail from an unlisted sender on an anyone rule gives one "Gmail triggers: " notice

  Record the result in the PR description, with placeholders only.
- [ ] 5.3 Write an "Upgrading" section in the PR description, which the `release` skill reads when it writes `release/NOTES.md` (never edit that file here). It covers:
  - the one-time recreate of the notifications service and the two sidecars
  - "run `lolloctl profiles` once" for stacks whose only senders are new
  - reviewing existing monitors and anyone rules after upgrading

  Verify the PR description names both new senders.

# gmail-triggers

The Gmail triggers sidecar: pages where a person writes rules ("when mail arrives in label X, ask agent Y to do Z"), a poller that reads Gmail metadata with the Google token Lettuce stores, and a queue of requests that the lollo executor in the app-server turns into one-off letta-code tasks. It is Python (Starlette, httpx), runs as the `lollo-gmail-triggers` service behind the `lollo-gmail-triggers` profile (which needs `google`), and serves its pages at `/gmail-triggers/` on the stack's lollo host. For users and admins (using it, running it, the security model): [docs/gmail-triggers.md](../docs/gmail-triggers.md); the design record is [DESIGN.md](DESIGN.md).

![Gmail triggers' containers, trust boundaries and requests](../docs/diagrams/gmail-triggers-architecture.svg)

An interactive version, [gmail-triggers-architecture.html](../docs/diagrams/gmail-triggers-architecture.html), opens in a browser from a clone of this repo.

## Files
| Path | What it does |
|---|---|
| `Dockerfile` | `base` installs `lollo-common` from its build context, then this package; `test` adds pytest and `tests/`; the final stage runs as root with every capability dropped, so it can write the root-owned state directory and nothing else |
| `pyproject.toml` | The package and its pinned dependencies |
| `src/gmail_triggers/__main__.py` | Entry point: reads the settings, builds the store, Google client, poller and app, runs uvicorn on port 8000 without an access log |
| `src/gmail_triggers/config.py` | `PREFIX` (`/gmail-triggers`), the limits (poll interval, `MAX_RULES`, `MAX_CLAIM`, `LOST_AFTER_SECONDS`, ...) and `Settings` from the environment |
| `src/gmail_triggers/app.py` | The pages under `/gmail-triggers/` (no JavaScript; the form's Conversation ID box shows through CSS `:has`), `/healthz`, and the executor routes from `executor_api.py`; the poller runs in its lifespan |
| `src/gmail_triggers/executor_api.py` | `/executor/v1/requests`, `results`, `agents` and `heartbeat`: `lollo_common.executor.request_routes` over the store's run log |
| `src/gmail_triggers/google.py` | Reads the policy and token files the BFF writes, refreshes the access token in memory, and makes the few Gmail calls (profile, history, message metadata, labels) |
| `src/gmail_triggers/poller.py` | Every `POLL_SECONDS` (default 120): what arrived since the stored history id, matched against the rules |
| `src/gmail_triggers/matcher.py` | Which rules a batch of messages sets off; pure, reads only the sender, subject and Gmail's sender verdict |
| `src/gmail_triggers/triggers.py` | From matches to requests: the text the agent gets and each rule's hourly limit |
| `src/gmail_triggers/store.py` | SQLite in `/data/triggers.db`: rules, poll state, handled ids, held matches, the run log (the executor's queue), what the executor last reported |
| `src/gmail_triggers/logs.py`, `icons.py` | Logging to stdout (no email content), and the app icons drawn as PNG at first use |
| `src/gmail_triggers/templates/`, `static/` | The pages (Jinja) and their stylesheet |
| `tests/` | pytest: the app, the executor API, Google (through `httpx.MockTransport`), matcher, poller, triggers |

The executor is not here: it is [landing/executor/run.mjs](../landing/executor/run.mjs), shared with the lollo landing page.

## How it plugs in
- **Overlay:** [compose/gmail-triggers.yml](../compose/gmail-triggers.yml) defines `lollo-gmail-triggers` (image `lollo-gmail-triggers:local`, built from this directory with the `lollo-common` build context): read-only root, `/tmp` as tmpfs, `cap_drop: [ALL]`, `no-new-privileges`, user 0, 256 MB, no ports, on the stack's default network.
- **Mounts:** one host path, `$LETTA_STATE_DIR/lollo/gmail-triggers` at `/data` (the app-server does not mount it), and Lettuce's `google-creds` at `/creds` and `google-policy` at `/policy`, both read-only.
- **Environment:** `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` (set from `CF_LOLLO_AUD`), `CF_ACCESS_ISSUER`, `ALLOWED_USERS`, `PUBLIC_ORIGIN` (set from `LOLLO_ORIGIN`), `LOLLO_EXECUTOR_TOKEN`, `TZ`. `POLL_SECONDS` (30 to 3600) and the `/data`, `/creds`, `/policy` paths have defaults.
- **Tunnel:** the `PROFILE_ROUTES` row in [lib/cloudflare.sh](../lib/cloudflare.sh) sends `^/gmail-triggers(/|$)` on the lollo host to `http://lollo-gmail-triggers:8000`. `/executor/v1/` and `/healthz` are outside that prefix, so the tunnel answers them with 404.
- **Requests checked in the app:** pages and form posts go through `lollo_common.auth` (Access JWT, Fetch Metadata, Origin and CSRF, COOP), the executor API through `lollo_common.executor.guard` (bearer token, no `Origin`, no tunnel headers); see [lollo-common](../lollo-common/README.md).
- **The executor:** `up` installs it in the app-server when this profile or `lollo-code-server` is on (`executor_wanted`, `sync_executor`), with `--gmail-triggers` in its cron line exactly when this profile is on (`executor_cron`). `ensure_executor_token` generates `LOLLO_EXECUTOR_TOKEN` once per stack. Each minute it claims requests, runs `letta cron add --once` for each, and posts the results, the agent list when asked, and a heartbeat. Its run, API and limits are in [landing/executor/README.md](../landing/executor/README.md).
- **`check_stack`:** `check_gmail_triggers` (hardened, the one bind mount, the Google volumes read-only and no other volume, nothing else reaching `$LETTA_STATE_DIR/lollo`, no symlink on that path), `profile_unmet` (needs `google`), `google-creds` mounted only by `bff`, `google-mcp` and this service, `check_lollo_audience`, and with pipelock `NO_PROXY` lists `lollo-gmail-triggers`. The details are under [Rules](#rules); every `check_stack` rule: [CONTRIBUTING.md](../CONTRIBUTING.md#what-check_stack-enforces).

## Rules
The guarantees and limits people see are in [docs/gmail-triggers.md](../docs/gmail-triggers.md#security-model); these are the rules that keep them.

### The one written exception to "no second way into an agent"
The rule itself is in [CONTRIBUTING.md](../CONTRIBUTING.md#relationship-to-upstream). Gmail triggers start turns, but open no connection to the app-server: the sidecar hands each request to the lollo executor in the app-server, which runs `letta cron add --once`, and Lettuce's scheduler fires the task on the BFF's connection. It gives agents nothing new, since they can already run `letta cron add` themselves.

Gmail triggers and News Radar use this path ([news-radar/README.md](../news-radar/README.md#rules) has News Radar's reason). This sidecar issues requests only for rules the person made on its Access-protected page, within each rule's hourly limit, and only for mail from senders the person allowed (below). The executor runs whatever well-formed request it is served, and agents can read its token, which gives them nothing `letta cron add` doesn't. Trigger rules live in this sidecar, not inside Lettuce, because agents could edit them there.

### A rule names its senders, or the person said anyone may set it off
Without a sender list, "verified" only proves that some domain's DMARC passed, the attacker's own included. So the form (`parse_rule`) refuses a rule with neither a sender list nor the "Anyone may set this off" switch (`Rule.anyone`), and one with both; `rule_matches` matches nothing for a rule with neither, should one be found in the database. Rules saved before the switch existed with no senders became "anyone" rules once, when `Store._migrate` added the column, so they kept working; the page marks every anyone rule with a warning. Tests: `test_a_rule_names_its_senders_or_says_anyone_may`, `test_anyone_rules_carry_a_warning`, `test_an_old_rule_without_senders_becomes_an_anyone_rule`, `test_no_senders_without_the_anyone_switch_matches_nothing`.

### A sender new to an anyone rule is announced and capped
`screen_new_senders` runs after matching. On an anyone rule, a sender not in `rule_senders` is new: its first mail sends the person one notice through `lollo-notifications` (`Notifier`, this sidecar's own `NOTIFY_TOKEN`, a link to the rule under `/gmail-triggers/r/`, a tag `gmail-triggers-new-sender-<rule>`), naming the rule and the address, never a subject. At most `NEW_SENDERS_PER_DAY` (3) new senders a rule in 24 hours may start runs (`ran_at`); mail from more is set aside (marked handled, no run), counted on the rule and shown on its page, and its notice says so. A set-aside sender is recorded (no second notice) but stays new to the cap until its mail is let through once, so mailing again does not get round the cap; when that happens it gets one more notice. Senders seeded at the upgrade have `ran_at` 0: known, not counted. Mail whose sender can't be read is never seen, so it is always set aside. Notices go out after the poll's transaction commits: a notice never holds up or decides a run, and a refused one is logged with the rule id and outcome only, never the address. `rule_senders` was seeded once from the run log, so an upgrade sends no burst. Tests: `tests/test_new_senders.py`.

### No directory is shared with the app-server
A shared directory would have to sit where agents can write, and they could swap a component for a symlink that Docker follows on the host. So the sidecar and the executor talk over HTTP ([landing/executor/README.md](../landing/executor/README.md#trust)), and the sidecar's only bind mount is its own rules directory, `$LETTA_STATE_DIR/lollo/gmail-triggers`, which the app-server does not mount.

### What `check_gmail_triggers` refuses
With the profile on, `check_stack` refuses:
- a `lollo-gmail-triggers` service that is missing or not hardened (`check_hardened`);
- any bind mount on it but `$LETTA_STATE_DIR/lollo/gmail-triggers:/data`, `google-creds` or `google-policy` writable, or any other named volume;
- another service bind-mounting `$LETTA_STATE_DIR/lollo`, a path under it or a parent of it (also after resolving symlinks at check time), or a volume whose `driver_opts` point there. Relative bind devices are refused outright;
- `$LETTA_STATE_DIR`, `state/lollo` or `state/lollo/gmail-triggers` being a symlink, which Docker would follow on the host.

Whatever the profiles, `google-creds` may be mounted only by `bff`, `google-mcp` and `lollo-gmail-triggers`, and no `volumes_from` may name `lollo-gmail-triggers`. `profile_unmet` refuses the profile without `google`, and `profiles` checks that before writing.

## Build and test
`lolloctl up` builds the image. The tests need only Docker:
```bash
tests/run-sidecar.sh gmail-triggers           # pytest in the image's test stage
tests/run-sidecar.sh landing                   # the executor's tests run with landing
node --test 'landing/executor/tests/*.test.mjs'  # the executor's tests on the host
```
Run them before changing this directory, `lollo-common/` or the executor.

The tests never use the network: Google is an `httpx.MockTransport`, Access tokens follow the vault's `make_jwt` pattern, a fake clock drives time, and a fake email body is asserted never to reach requests, pages or logs.

## Upstream seams
These are the Lettuce behaviours Gmail triggers depend on (all of them are listed in [CONTRIBUTING.md](../CONTRIBUTING.md#upstream-seams)):
- The Google files the BFF writes on the `google-policy` and `google-creds` volumes (`/policy/sidecar.json`, `/creds/<account>.json`, upstream `bff/src/google/settings.ts`; see `google.py`).
- Through the executor: `letta cron add` with `--name=`, `--description=`, `--prompt=`, `--at=in 1m`, `--once`, `--agent=`, `--conversation=` with `new`, `default` or a conversation id, and its JSON output (`.id`); `letta agents list` (`.items[]`); the scheduler picking up a changed task file without a restart; the app-server entrypoint copying `/root/.letta/system-cron` to `/etc/cron.d`; and `/usr/local/bin/agent-env`.
- letta-code giving the agent's shell its conversation id as `$LETTA_CONVERSATION_ID`. Lettuce shows no conversation id and letta-code has no command that lists them, so for "A specific conversation" the form tells the person to ask the agent to print it; the page checks only its shape (`CONVERSATION_ID` in `lollo_common.executor`) and refuses to keep it when the rule's agent changes, since letta-code doesn't check that a conversation is the agent's when the task fires.

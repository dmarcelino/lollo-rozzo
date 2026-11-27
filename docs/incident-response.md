# After a suspected prompt injection

What to do when you think an agent of a stack was misled into acting for someone else: a strange email or web page in a conversation, an approval the person did not expect, a skill or scheduled job nobody added. The examples use the stack `alfred`.

Agents run as root in their stack's app-server and share it, so one misled agent can change what every agent of that stack uses, and keep it that way after restarts: lollo's own helpers (such as `vault-run`), the skills, the scheduled jobs, the Settings -> MCP list and the container's files. Lollo Rozzo cannot prevent that. It can show it to you (`lolloctl audit`), put its own files back, and keep backups from bringing a changed copy back.

## 1. Inspect
```bash
lolloctl audit alfred             # what differs from what lollo shipped or you accepted
lolloctl audit alfred --verbose   # every change in the app-server container too
```
`audit` reads the stack's files from a container of its own (no network, read-only), never through the app-server, and reads the container's changes from Docker. It reports:
- lollo's skills and the lollo executor, file by file, wherever they differ from the shipped ones;
- skills, scheduled jobs (`system-cron`) and Settings -> MCP entries that lollo did not install and you have not accepted, with the first lines of each job;
- changes in the app-server container under places that run code: `/etc/cron.d`, `/etc/ld.so.preload`, the shell's start-up files, the binary directories and `/usr/local/lib`, where letta-code lives.

It exits 0 when it finds nothing, 1 with findings and 2 when it could not run. `status`, `up`, `skills` and `backup` print its one-line summary. It is a picture at one moment, not a guard: an agent can change a file right after.

Then look at what the agents did:
- the person's conversations and Lettuce's **Runs** pane (background commands and subagents);
- the vault's audit page (`<lollo>/vault/audit`), which lists every value released and to which agent;
- with `lollo-pipelock`, the hosts the agents reached: `lolloctl receipts alfred` ([egress-firewall.md](egress-firewall.md#reading-the-receipts)). It sees only what went through it; an agent can go around it.

An entry you recognise (a skill the person installed, a job they asked for) is accepted once, bound to its contents: `lolloctl audit alfred --accept skill:<name>` (or `cron:<file>`, `mcp:<server>`, `diff:<path>`). If it changes later, `audit` reports it again. Lollo's own skills and the executor are never accepted; `skills` and `up` put them back.

## 2. Contain
```bash
lolloctl down alfred
```
This stops every agent of the stack and removes its containers, so whatever was changed in the container itself (outside the state directory) is gone. Gmail triggers and News Radar start no turn while the stack is down. Leave it down until it is clean; take a backup first if you want to keep the evidence (`lolloctl backup alfred <dir> --compress`).

## 3. Clean
Either bring it back and clean it, or restore a backup from before the suspected start.

**Bring it back and clean it:**
```bash
lolloctl up alfred && lolloctl skills alfred
lolloctl audit alfred
```
`up` starts fresh containers and reinstalls the lollo executor; `skills` puts lollo's skills and their manifest back. What `audit` still reports was written into the state (`letta-home`), which survives a restart. Remove it straight away, before an agent's next turn (a Gmail trigger, a News Radar report or a schedule can start one at any time; pause the rules and monitors on their pages if needed):
- an unknown skill or scheduled job: `lolloctl shell alfred`, then delete it under `/root/.letta/skills/` or `/root/.letta/system-cron/` (and its copy in `/etc/cron.d/`);
- an MCP entry: the person removes it in **Settings -> MCP**.

Run `lolloctl audit alfred` again until it reports only what you accept. Memory and conversations can hold the injected instructions too: read the agents' memory with the person, and delete conversations or agents you no longer trust.

**Restore a backup** taken before the suspected start ([operations.md](operations.md#restoring)), then `lolloctl up alfred && lolloctl skills alfred` and `lolloctl audit alfred`. A backup never holds lollo's skills or executor, so those come from the code; anything else the agent changed before that backup comes back with it.

## 4. Rotate
Rotate what the agents could read: anything in the app-server or the folders it mounts (`state/letta-home`, `state/letta-data`, the workspaces, the agents' tools and `state/task-logs`, the background commands' output).
- **Model-provider keys** (Lettuce's **Settings -> Providers & models**, kept in `letta-data`): revoke them at the provider and enter new ones.
- **Logins in the app-server**: Claude Code, Codex and gh (`letta-home`), and any token an agent was given or wrote into its workspace or tools. Revoke them where they were issued.
- **The lollo executor's token** (`LOLLO_EXECUTOR_TOKEN`), which agents can read by design: delete its line in `stacks/alfred/stack.env`, then `lolloctl up alfred`, which makes a new one and gives it to the executor, Gmail triggers, News Radar and the landing page.
- **Vault values** released to an agent since the suspected start (the vault's audit page lists them): change those passwords, cards or codes at their sites. Values never released stayed encrypted in the vault, out of the agents' reach.
- **The browser's kept sign-ins**, if an agent may have used them to change something on a site: forget them on the browser's **Sites** page and sign out of that site's other sessions there.
- **The Telegram bot**, with the `telegram` profile, if its token was in the agents' reach: make a new one with BotFather.

What the agents could not read, and needs no rotation for this: `stack.env` and the Cloudflare tunnel token (on the host), the Cloudflare API token, the vault's data and passphrase, the browser's stored sign-ins, Lettuce's Google tokens (only the BFF, `google-mcp` and Gmail triggers mount them; agents use them only through those tools), and each notification sender's token. If you suspect the host itself, rotate those too, as in [Moving to another host](operations.md#moving-to-another-host).

**Never rotate the push-notification keys**, Lettuce's (`PUSH_VAPID_*`) or the lollo's (`LOLLO_VAPID_*`): agents cannot read them, and a new pair breaks every device's notifications, including the vault's erase notices.

## 5. Afterwards
- Tell the person what happened and what was rotated.
- Narrow what let it in: a Gmail rule without a sender list, a News Radar source on a site nobody chose ([gmail-triggers.md](gmail-triggers.md#security-model), [news-radar.md](news-radar.md#security-model)).
- Keep running `lolloctl audit all` (it is also in every `status`), and keep regular backups: a backup from before an incident is the quickest way back.

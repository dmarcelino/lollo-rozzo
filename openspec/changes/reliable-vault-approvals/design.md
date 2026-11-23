# Design

## Context

See proposal.md (Why). What the incident's records show, and what the code does today:

- **Vault log and transcript, 23 Nov 2026:**
  - The first batch of requests (10:43) got no wait at all.
  - One background `get_result` (50 s) returned `pending` about 17 s before the user approved at 10:50, and the agent ended its turn.
  - All three "expired" handles were in fact redeemed by an earlier `vault-run`, 30 s to 1.5 min after they were issued. Two commands failed after taking the values: the browser task had closed, and `"text":"'"$VAULT_PASSWORD"'"` let the outer shell expand the variable to nothing.
  - No timeout ran out: the 10-minute request lifetime, the 5-minute handle lifetime and the browser's 15-minute idle limit were all long enough. The browser task closed only because the approvals sat uncollected for 20 minutes.
- **`service.py`:**
  - `get_result` hands over the handle on the first `done` call (`_deliver` creates it and sets `expires_at = now + HANDLE_TTL_SECONDS`).
  - `redeem` deletes a used handle from `_handles`, and `expire()` clears `_handles` when the vault locks. An id that isn't there gets one 404 message.
- **letta-code 0.34.67** (`letta.js`, Bash tool): `effectiveTimeout = run_in_background ? (timeout > 0 ? timeout : 0) : …`. A background command has no time limit unless the agent sets one. The 35-minute `BACKGROUND_TASK_WAIT_CAP_MS` belongs to the Claude Code subagent session code, not the Bash tool. The skill's warning that "a loop in one command runs into the command's time limit" holds only for foreground commands, which are capped at 600 s.
- **Lettuce's MCP bridge** gives one tool call 120 s, hence `MAX_WAIT_SECONDS = 50` per `get_result`.
- **`MAX_PENDING = 5`** requests can wait at once.

## Goals / Non-Goals

**Goals:**
- An agent that runs one command after its requests gets one notification when all of them are answered, with handles that still have their full lifetime.
- Every reply that leaves the agent waiting carries the literal next command.
- A refused handle tells the agent the real reason.

**Non-Goals:**
- Starting a turn when the user approves. It would be a "second way into an agent" (CLAUDE.md, Upstream), and it needs a written reason in CONTRIBUTING first.
- Changing any lifetime (`REQUEST_TTL_SECONDS`, `HANDLE_TTL_SECONDS`, `MAX_WAIT_SECONDS`).
- The browser skill on `33-browser`. It already has a `vault-fill` recipe for `type_secret`. Pointing its wait step at `vault-wait` is a follow-up on that branch.

## Decisions

### 1. `vault-wait` calls `get_result` through `mcp.sh`, not the vault directly
It runs `sh /root/.letta/skills/mcp-servers/scripts/mcp.sh call mcp__vault__get_result --args <json>`, the same command the skill tells agents to run today, and reads `structuredContent` from its stdout (exit 2 means a tool error).
- **Alternative: speak MCP to `http://lollo-vault:8000/mcp` from the script**, as `vault-run` does with `/redeem`. That means a streamable-HTTP MCP client in the standard library (session setup, SSE parsing), which is fragile, and a second MCP client the vault must stay compatible with.
- **Alternative: a plain `/wait` HTTP route on the vault.** That would be a new agent-facing surface handing out handles outside MCP.
- The wrapper is already an upstream seam (`agent-skills/README.md`), so this adds none. The wrapper path can be overridden with `LOLLO_MCP_SH`, for the tests' stub.

### 2. One thread per request, each with `wait_seconds` 50 and `collect: false`; collect all at the end
Each round, every still-pending id gets its own `get_result(id, 50, collect=false)` in a thread, so an approval of any request is seen within one call. That is at most 5 concurrent node processes (`MAX_PENDING`), each under the bridge's 120 s. When no id is pending, it calls `get_result(id, 0, collect=true)` once per `done` id and prints a JSON array of the answers in the order given.
- **Alternative: return as soon as any request is answered.** That brings back the "restart it" step this change removes.
- **Alternative: collect each answer as it comes in.** That starts that handle's 5-minute clock while the others wait, which is exactly the "expired" trap.
- `collect: false` is why `get_result` needs the new flag. It changes nothing for callers that omit it.

### 3. `vault-wait` exit codes and limits
- **Exit codes:** 0 when all are `done`; 1 when any is `denied`, `expired` or `unknown` (all answers are printed anyway); 2 for usage errors or when the wrapper keeps failing.
- **Wrapper failures while waiting:** a peek that fails is retried up to 3 times with a short back-off, since peeking changes nothing. After that, `vault-wait` exits 2, collects nothing, and names every id to run it again with, including those already approved.
- **Collecting is tried once per id**, never retried: a call that reached the vault has already handed the handle over, so a retry would get "already returned". A failed collect is printed as `not collected` with a note, the other ids are still collected, and the exit is 2. A `done` answer with no handle or values (collected earlier by another call) counts as exit 1, not success.
- **Process groups:** each wrapper call runs in its own process group, and a timeout or a stop kills the whole group, in case a future wrapper stops `exec`ing `letta`.
- **Overall limit:** `REQUEST_TTL_SECONDS` + 2 waits (about 12 min). It is a guard only: the vault marks requests `expired` before then.
- **Stops:** `vault-wait` forwards SIGTERM/SIGINT to its children and exits, so TaskStop ends it cleanly.

### 4. `wait_command` field plus message text
`KEEP_WAITING` becomes a function of the id. The `pending` replies gain `"wait_command": "python3 /root/.letta/skills/using-vault-secrets/vault-wait <id>"`. The message reads, in short: "Once you have made every request the task needs, run one background command with all their ids: `<wait_command> <other ids>`. Don't start it for each request. Without a shell, call `get_result` with this id and `wait_seconds` 50 until it is no longer pending." Saying "now" would invite one `vault-wait` per request, which collects each handle while the others still wait.
- **Alternative: message text only.** A separate field gives tests something exact to check, and gives models a copy-ready value. The text keeps it visible to models that skim fields.
- **The link comes first, and before the next tool call.** The reply used to say "send them this link in your reply now". Twice on 23 Nov 2026 the agent made a request in the middle of a task, read "your reply" as the message that ends its turn, went on working, and never wrote the link. The message now opens with "Before your next tool call, write this link to the user in a message, even if you go on working while you wait", and says why: the user sees only what the agent writes. `get_result`'s `pending` reply asks again, in case it wasn't written yet. Nothing in the vault can check that the agent did this: it can't read or write the conversation.
- When several requests are pending, each reply names only its own id. The skill shows listing them all in one `vault-wait`.

### 5. Spent-handle record: `_spent: dict[str, tuple[str, int]]`
It maps the handle id to the reason (`"used"` or `"locked"`) and to the handle's original `expires_at`.
- **Where it changes:** `redeem` moves each handle it hands over into `_spent`. `expire()` moves handles into `_spent` as `"locked"` when it clears them for a lock, and drops `_spent` entries whose time has passed.
- **What `redeem` says:** it checks `_handles` first, then `_spent`, and answers with:
  - **410** "already used: an earlier vault-run took this handle, even if its command failed; request the secret again"
  - **410** "dropped when the vault locked; request the secret again once it is unlocked"
  - **404** "unknown or expired handle (almost always: already used, or older than N minutes); request that secret again"
- **What it holds:** only ids, no values, in memory only. The ids are random 32-byte tokens that grant nothing once spent, and the record is bounded by `MAX_HANDLES` per 5 minutes of issuing.
- **Alternative: keep spent `Handle` objects with `env` cleared.** That is the same thing with more room to leak a value by mistake.
- `vault-run` already prints the vault's `error` string, so it needs no change beyond its tests.

### 6. Skill text
- **Wait section:** rewritten around `vault-wait`: post the links, run `vault-wait id1 id2 …` once in the background, act on its output. `get_result` stays as the no-shell fallback.
- **Rules section:** gains the general handle rules, and its error list gets the three new texts.
- **Email recipe:** becomes "an MCP tool's arguments", named so that it obviously covers any tool. The `run` hint in `_deliver` stays for commands. The message adds the MCP-arguments rule, because that is where the agent went wrong.
- **Wording:** the text stays generic (agent-skills rule): no shop or browser names.

## Risks / Trade-offs

- **[A future letta-code gives background commands a time limit]** → `vault-wait` would be killed mid-wait. Mitigation: add the seam to CONTRIBUTING's upstream seams and to the Lettuce upgrade checklist, and note it in the skill ("if it was stopped, run it again with the ids still pending"). Its worst-case run of about 12 min also stays well under a 35-min-style cap.
- **[A model runs `vault-wait` in the foreground]** → letta-code moves long foreground commands to the background on its own ("Commands still running after a brief wait continue in the background automatically"). The foreground cap of 600 s is under `vault-wait`'s worst case, but the skill and the reply both say "background".
- **[`vault-wait` prints `text` values into the conversation]** → It prints the same values `get_result` would have. The rules that come with them are printed too.
- **[Agents ignore the skill entirely, as in the incident]** → That is why the command is also in the vault's own `pending` reply: the agent read that reply, and the copy-ready command is now right there.
- **[A changed 404/410 split confuses older `vault-run` copies]** → `vault-run` prints whatever `error` the vault sends and treats any HTTP error the same way, so old copies work unchanged.

## Migration Plan

`lolloctl up` rebuilds and restarts the vault. That locks it, as any vault restart does, and the user unlocks it. `lolloctl skills all` pushes the new skill and `vault-wait` to every stack. The order doesn't matter: an old skill with a new vault still works (the extra fields are ignored), and so does a new skill with an old vault, except that `collect: false` would be ignored there. To guard that case, `vault-wait` refuses to go on if a `collect: false` reply comes back with a handle. It prints that answer, so nothing is lost, and exits 2. Rollback is reverting both.

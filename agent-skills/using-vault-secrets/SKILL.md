---
name: using-vault-secrets
description: Load this BEFORE asking the user's vault (the `vault` MCP server) for anything. It shows how to use the user's private data (addresses, card numbers, passwords, ID numbers...) in a document, an email, a form or any command without the values entering the conversation, and how to wait for the user's approval so you don't miss it.
---

# Keep private values out of the conversation

Everything in your conversation is sent to the AI provider. The vault can put a value straight
into a file, a command or an MCP call instead, so you never read it. Before every
`request_secret`, ask yourself:

- **Do I only need to put the value somewhere?** (a letter, a form, an email, a login): use
  placeholders, the default. This is almost always the case.
- **Do I need to understand the value itself?** (decide something from it): first check the
  public details in `list_secrets` (the name, the country, a card's brand...). Only if they are
  not enough, ask with `delivery: "text"` and a `reason` the user reads before approving, for
  the fewest fields that answer the question.

Ask only for the fields the task needs: `fields: ["address1", "city", "postal_code"]`, not
the whole identity.

# Placeholders, step by step

1. **Prepare everything first, with placeholders.** Write `{{VAULT_<FIELD>}}` where each value
   goes: `{{VAULT_ADDRESS1}}`, `{{VAULT_POSTAL_CODE}}`, `{{VAULT_NUMBER}}`. A script reads
   them from its environment instead (`process.env.VAULT_NUMBER`, `os.environ["VAULT_CODE"]`).
   Check the template or script now: you can't look at the result later.
2. **Request the secret** with a clear purpose. If the answer is `pending`, wait for the user
   (below). Once approved, `get_result` answers with a `handle` and the exact `placeholders`.
3. **Fill at the last step, within 5 minutes.** The handle works once: the first `vault-run`
   uses it up, even when its command then fails. So get everything ready (the page open, the
   template checked) before you run it. Run one of the recipes below. In them,
   `V=/root/.letta/skills/using-vault-secrets`.

## A document

```bash
python3 $V/vault-run <handle> -- python3 $V/vault-fill letter.tmpl.docx letter.docx
```

`vault-fill` fills `.txt`, `.md`, `.csv`, `.json`, `.html`, `.xml`, `.eml` and Office or
OpenDocument files (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`), escaping each value for
the format. For a PDF, fill the source (a `.docx` or `.html` template) and then convert the
filled copy. It refuses to write anything when a placeholder has no value, or when Word split a
placeholder across formatting: retype it in one go, with no formatting change inside it.

## An MCP tool's arguments (an email, a form field, any tool)

Write the tool's arguments as a JSON template file, with the placeholders inside its strings,
then fill and send them in one command. Never write `$VAULT_...` into the arguments on the
command line (`--args '{"text":"'"$VAULT_PASSWORD"'"}'`): your shell expands it to nothing
before `vault-run` sets it, the tool gets an empty value, and the handle is used up. The pipe must be inside `bash -c '...'`: `vault-run` replaces
the values in its own output, so a pipe outside it would send `[VAULT_CITY]` instead of the city.
`pipefail` stops a failed fill from being reported as sent.

```bash
python3 $V/vault-run <handle> -- bash -c 'set -o pipefail
  V=/root/.letta/skills/using-vault-secrets
  out=$(python3 $V/vault-fill send.tmpl.json - \
    | sh /root/.letta/skills/mcp-servers/scripts/mcp.sh call <send-tool> --args-file -) \
    && echo sent || printf "failed: %s\n" "$out"'
```

It prints `sent`, or the tool's error (with long values hidden). The handle is used up either
way: to try again, request the secret again.

## A form, a login, any command

```bash
python3 $V/vault-run <handle> -- node /work/$AGENT_ID/checkout/fill.js
python3 $V/vault-run <handle> -- sh -c 'curl -fsS -u "me:$VAULT_PASSWORD" https://api.example.com/orders'
```

Put a value in an argument only inside `sh -c '...'`, in single quotes: otherwise your shell
expands `$VAULT_PASSWORD` to nothing before `vault-run` sets it.

## Several secrets in one step

A letter needs the sender's and the recipient's address, whose fields have the same names.
Request each with its own `alias` (`"alias": "home"` gives `{{VAULT_HOME_CITY}}`,
`"alias": "client"` gives `{{VAULT_CLIENT_CITY}}`), then give `vault-run` both handles:

```bash
python3 $V/vault-run <handle1> <handle2> -- python3 $V/vault-fill letter.tmpl.docx letter.docx
```

## Rules

- **Never open, print or convert-and-read the filled file**, and never read a command's output
  that could echo the values. Check the template instead, and tell the user where the filled
  file is so they can check it.
- **Make commands print only a status.** `vault-run` hides values of 6 characters or more in
  the output, as `[VAULT_NUMBER]`, but not shorter ones (a postal code, a house number, a
  security code). A value two fields share is shown with both names, `[VAULT_CITY/VAULT_STATE]`:
  both fields hold that value, nothing is wrong with either.
- Run the command in the foreground. Output from anything it leaves running in the background
  is lost when `vault-run` exits.
- Use the values only for the purpose the user approved. Never save, log or send them anywhere
  else.
- **A handle is used up by the first `vault-run` that takes it**, even if its command then
  fails (a page that closed, a wrong argument, a typo in the template). To try again, request
  the secret again.
- **An MCP tool's arguments come only from a `vault-fill` template** piped into the tool inside
  `vault-run` (the recipe above), never from `$VAULT_...` written into `--args`.
- "already used": an earlier `vault-run` took this handle, even though its command failed.
  Request the secret again. It is not a timeout, so waiting or hurrying won't help.
- "dropped when the vault locked (or was erased)": the vault locked before you used the handle. Request the
  secret again; while the vault is still locked, that answer gives you the link to send the
  user so they can unlock it.
- "unknown or expired handle": almost always already used, or older than 5 minutes. Request
  that secret again. With several handles, none of them was used up, so the others still work.
- "would come from two secrets" or "already set": two handles give the same names, or a
  `VAULT_` variable is already set. Nothing was used up: request one secret again with an
  `alias`, or unset the variable.

## Values you asked to read (`delivery: "text"`)

They are in your conversation, so follow the rules that come with them: never repeat them in
your reply, memory or files (refer to the secret by its name, and when asked to check one, say
what you checked and the result), and send them only where the approved purpose needs them.

# Waiting for the user's approval

When `request_secret` answers `pending`, the user has to approve the request first. Nothing
starts a new turn for you when they do: if your turn ends with nothing waiting, you never
collect what they approved.

1. Make the requests that need approval first. A secret the user shares without asking
   answers at once with a handle, whose 5 minutes start then: request those after `vault-wait`
   finishes, just before you fill.
2. Then do these three things, in this order:

   ```
   1. Bash  {"command": "python3 /root/.letta/skills/using-vault-secrets/vault-wait <id1> <id2>",
             "run_in_background": true}
   2. Your message to the user, in the same response or after it:
      "Please approve: <approve_url 1> and <approve_url 2>"
   3. End your turn. Leave the command running; its notification starts your next turn.
   ```

   - **One Bash call with `run_in_background: true`**, with every pending request's id (each
     `pending` answer gives the command for its id in `wait_command`). Run it even for a single
     request, and again for a request you make later in the task (asked again after a handle
     was used up, say). Don't start one per request: it would collect each answer as it comes,
     and its handle's minutes would run out while the others wait.
   - **The command comes first:** a response with no tool call ends your turn, so links written
     before it would end the turn with nothing waiting. The user sees only what you write, not
     the vault's answer, and may not get a notification, so the links must be in your message.
     If you go on with the task while you wait, put each link in the same response as your next
     tool call.
   - **Never stop it** (`TaskStop`): a running background command doesn't block your turn, and
     its notification is what starts your next one. Stopped, it collects nothing.

   It waits until the user has answered every request, then prints one JSON answer per id:
   `done` with the `handle` and `placeholders` (or, for `delivery: "text"`, the values), or
   `denied` or `expired`. The handles' 5 minutes start only when it prints them, so use them
   right away.
3. When it finishes, act on its answers, also when it exits with an error: it still prints
   every handle it collected. For a `denied` or `expired` request, tell the user and ask
   whether to request it again. If it says to run it again (it was stopped, or the vault could
   not be asked), run it again with the ids it names. For an answer `not collected`, follow its
   note.

If you can't run commands in the background, call `get_result` with the request's `id` and
`wait_seconds` 50, and call it again while it answers `pending`, before you end your turn.

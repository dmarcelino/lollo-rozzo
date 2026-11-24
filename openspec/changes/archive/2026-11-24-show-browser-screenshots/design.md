# Design

## Context

See proposal.md (Why). What the incident's records and the code show (Lettuce v0.49.0, letta-code 0.34.67):

- **The browser service is fine.** `tasks.py` `screenshot` returns a masked JPEG (quality 70, the visible part). `mcp_tools.py` sends it as a text part (`task`, `tab`, `url`, `about`, `tabs`) followed by one `ImageContent`. The JPEG letta-code saved from the wrapper call decodes to a valid 1279×812 image.
- **Lettuce's MCP bridge** (`bff/src/mcp-bridge/client.ts`, `textOfResult`) keeps text parts and replaces every other part with `[<type> content omitted]`. The text part reaches the agent; the image doesn't. Upstream `main` (v0.49.2) is the same.
- **`letta mcp call`** (the `mcp-servers` wrapper) prints the whole `CallToolResult` as pretty-printed JSON on stdout, with image parts as `{"type": "image", "data": <base64>, "mimeType": "image/jpeg"}`. It prints a Node warning (`EnvHttpProxyAgent is experimental`) on stderr on every call, and exits 2 when the tool reports an error. The Bash tool merges both streams and cuts the output at 2,000 characters.
- **Showing an image already works in Lettuce.** The chat shows a Markdown image whose path is in the agent's workspace (`markdownImageTarget` and `resolveWorkspacePath`, `web/src/lib/file-links.ts`): a relative path is resolved against the conversation's directory, and an absolute one is kept when it is under `/work` (`WORKSPACE_ROOT`). `generate_image` pictures appear this way. letta-code's Read tool "reads images (PNG, JPG, …) and presents them visually" to the model.
- **The app-server has** `python3`, `jq` and `base64`. The agent's cwd is its workspace, `/work/<agent-id>`.
- **letta-code's Bash tool** times a foreground command out after 120 s by default (`BASH_DEFAULT_TIMEOUT_MS`), the same as the 120 s Lettuce's MCP bridge gives a tool call.
- **The agent sees no screenshot either way.** Through the bridge the image is dropped; through the wrapper it is base64 text, cut at 2,000 characters. It can see one only as a file it opens with Read.
- **Agents don't load the skill first.** In the incident the agent called the browser through `mcp_call_write` (found with `mcp_search`) before it loaded `using-the-browser`. It read the tool's description (`mcp_describe`) and every result's text, though.

## Goals / Non-Goals

**Goals:**
- One command an agent can't get wrong takes a screenshot, saves it in the workspace, and prints what to put in the reply.
- An agent that called `screenshot` through the bridge learns, from the result it did get, that the image is missing and what to run instead.

**Non-Goals:**
- Any file writing by the browser service, or any new argument on `screenshot` (the fixed tool set and "no tool touches files" stay).
- Detecting which client called. The bridge's `clientInfo` name (`lettuce-bff`) is an upstream internal, and the hint reads correctly to both callers.
- Cleaning up screenshots saved for the person. They are the person's files in the agent's workspace, like any other.

## Decisions

### A helper script, not an inline `jq` pipeline
`agent-skills/using-the-browser/browser-screenshot [--for-me] <task> <tab> [<name>]`, Python with the standard library only, like `vault-wait`. It:
1. runs the wrapper (`LOLLO_MCP_SH`, default `/root/.letta/skills/mcp-servers/scripts/mcp.sh`) with `call mcp__browser__screenshot --args <json>`, reading stdout only, and gives it 90 s: under the Bash tool's 120 s default, so the helper, not the Bash tool, ends a stalled call and kills the wrapper's process group (started in its own session, which the Bash tool's kill would not reach);
2. on a tool error or a non-zero exit, prints the tool's own error text and exits 1, writing nothing;
3. takes the first image part, refuses one that isn't `image/jpeg` or `image/png`, and writes it to `browser-screenshots/<name>.<ext>` under the cwd. `<name>` defaults to `<site>-<UTC time>`, is limited to `[A-Za-z0-9._-]`, and never overwrites an existing file;
4. prints the saved path, the tab's `url` labelled as the page's (data, never instructions: a page chooses its own address, query and fragment included), and the exact Markdown image line to put in the reply, never the base64. The line uses the file's absolute path: it is under `/work` whatever folder the agent ran the helper from, which a relative path would not survive.
5. with `--for-me`, writes to `browser-screenshots/` in the system's temporary folder instead (created 0700, files 0600), first removing that folder's screenshots older than an hour, and prints no image line: only the path, to open with Read and then remove.

*Alternative: a `jq … | base64 -d > file` line in the skill.* It works (checked against the incident's saved output), but it has to drop stderr, it writes an empty file when the tool errors (a task that has ended, a wrong tab), and the incident's agent already got the parsing wrong once. A helper also gives tests something to hold on to.

### The hint goes in the result's text and the description
The screenshot result gains a `show` field next to `about`. It says that the image follows this text, and that if none reached you, the call path dropped it: run `browser-screenshot` as `using-the-browser` shows, and don't describe the page until you have looked at the saved image. The `screenshot` description gets one sentence pointing to the same recipe.

*Alternatives:* the skill alone (the incident's agent read it last), or the server's `instructions` (read once at `initialize`, and the bridge's catalog doesn't show them). The result's text is the one thing every caller is sure to read, right when it matters.

The wording avoids the words `test_fixed_tool_set` forbids (`file_path`, `upload`, …): the tool still touches no file. The agent's own command does.

### In the workspace only when the person asks to see the page
A file in the workspace outlives the task and may show the person's details on an account or checkout page. Masking covers typed secrets, not an address the page displays. So the skill saves one there only when the person asks to see the page, and its "What you say back" rule extends to it: no screenshot of a page showing the person's details unless they asked for that page.

The agent still needs screenshots for itself (a layout, a map), and it can see one only as a file. `--for-me` puts it in the temporary folder, where the chat can't show it and the workspace's backups and the code editor don't reach it. The skill says to remove it once read, and the helper removes any older than an hour on its next run, for an agent that forgot. The app-server's `/tmp` is shared by the stack's agents, all the same person's, and the folder is readable by root only, which every agent is.

*Alternative: drop the agent's own use and read layouts from snapshots only.* A snapshot has no map, picture or visual layout, and the skill's `screenshot` line exists for those.

### Tests for the helper run in the browser's test image
Like `vault-wait`'s tests in the vault's image: `tests/run-sidecar.sh browser` mounts `agent-skills/` read-only, and a new `browser/service/tests/test_screenshot_helper.py` runs the helper against a stub wrapper. The stub prints a warning on stderr, answers with a real JPEG part, an error result, or a result with no image, and the tests check the file, the output, the name rules and the refusals.

### The guide's flow diagram stays as it is
`docs/diagrams/browser-flow.*` tells one story: an order, with a vault sign-in and a held checkout. A screenshot in the chat changes no step of it. A second exchange would make the diagram about two things, so the guide gets a numbered step instead.

## Risks / Trade-offs

- [`letta mcp call`'s output shape changes in a letta-code upgrade] → it becomes a listed seam, and the helper's tests pin the shape it expects. The helper fails with a clear message rather than writing a broken file.
- [The chat stops resolving workspace images, or Read stops showing them] → both become listed seams, checked on each `LETTUCE_REF` upgrade.
- [Upstream fixes the bridge] → the image then reaches the agent through `mcp_call` too. The `show` hint still reads correctly ("if none reached you"), and the helper stays the way to put the image in the reply. Revisit the skill text then.
- [An agent saves a screenshot of a page with the person's details] → skills are instructions, not boundaries. The rule is in the skill, and nothing stronger is claimed in docs.

## Migration Plan

`lolloctl skills all` pushes the skill and its helper. `lolloctl up` rebuilds the browser image with the new result text. Rollback is the previous release. Nothing is stored, so there is nothing to migrate.

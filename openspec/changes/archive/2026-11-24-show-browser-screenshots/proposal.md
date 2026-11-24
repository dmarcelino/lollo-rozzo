# Proposal

## Why

On 23 Nov 2026 a person asked an agent to show them a screenshot of a news site's front page taken with the lollo browser. The browser took it four times, and neither the agent nor the person ever saw it:

- Through `mcp_call_write`, Lettuce's MCP bridge turned the image into the text `[image content omitted]` (upstream `bff/src/mcp-bridge/client.ts`, `textOfResult`). The agent said "here it is" and described headlines it had never seen. The page was mostly a cookie-consent banner.
- Through the `mcp-servers` wrapper, which `using-the-browser` recommends, the image came back as about 136 KB of base64 in Bash output. letta-code cut that to 2,000 characters. Nothing in the skill says how to save it and show it, so the agent tried a `path` argument, a local Chrome the stack doesn't have, and a parser that failed. It then blamed "a known limitation".

Lettuce can already show a picture: the chat renders a Markdown image of a file in the agent's workspace, and letta-code's Read tool shows an image to the model. The browser just never tells the agent to use them. The proper fix, passing image parts through the bridge, belongs upstream and is drafted as an issue there. This change makes screenshots work today with what lollo owns.

## What Changes

- **New `browser-screenshot` helper** in `agent-skills/using-the-browser/`: `browser-screenshot <task> <tab> [<name>]` calls `screenshot` through the `mcp-servers` wrapper, saves the image under `browser-screenshots/` in the agent's workspace, and prints the path and the exact Markdown image line for the reply, never the base64. It fails clearly and writes nothing when the tool errors or sends no image. With `--for-me`, for the agent's own look at a layout or map, it saves in a private temporary folder instead and prints no image line: the agent reads the file and removes it.
- **`using-the-browser` gets a "Showing a screenshot" section**: the agent sees a screenshot only as a file. With `--for-me` it reads one and removes it; when the person asks to see a page, it saves one in the workspace, reads it, and shows it with the line the helper printed. It also says that `mcp_call` / `mcp_call_write` don't deliver the image, and that an agent never describes a screenshot it hasn't seen. A screenshot is saved only when the person asks to see the page, since a saved file outlives the task.
- **The `screenshot` result says what to do when the image is missing**: its text part, which the bridge does deliver, gains a `show` field naming the wrapper recipe in `using-the-browser`, and saying that a result with no image means this call path dropped it. The tool's description says the same in one sentence, since agents read it through `mcp_describe`.
- **Docs**: `browser/README.md` (the tool's result, the seams), `agent-skills/README.md` (the seams), `CONTRIBUTING.md`'s seams table, and `docs/browser.md` (people can ask for a screenshot in the chat).

Not in this change: changing Lettuce's bridge (upstream issue, drafted separately); a `path` argument or any file writing in the browser service (it has no path into `/work`, and "no tool touches files" stays); screenshots of the whole page beyond the visible part.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `browser/agent-tools`: the screenshot result gains a requirement that it tell the agent how to show the image when the call path dropped it, and that the agent never be steered to describe what it did not receive.

## Impact

- **Browser service**: `browser/service/src/lollo_browser/mcp_tools.py` (the `screenshot` description and its result's text part), `tasks.py` if the `info` dict is built there, and tests in `browser/service/tests/test_tools.py`. The fixed tool set and the schema stay as they are: `task` and `tab` only, nothing new about files.
- **Agent skill**: `agent-skills/using-the-browser/SKILL.md` and the new `browser-screenshot` (Python, standard library only, like `vault-wait`; `python3` is in the app-server image, checked in Lettuce v0.49.0). Its tests go in `browser/service/tests/test_screenshot_helper.py`, and `tests/run-sidecar.sh` mounts `agent-skills/` for the browser as it does for the vault.
- **Upstream seams** (new rows in the browser's row): `letta mcp call` printing the `CallToolResult` as JSON on stdout with image parts as `{type, data, mimeType}` and its Node warnings on stderr; letta-code's Read tool showing an image file to the model; Lettuce's chat resolving a Markdown image with a relative path against the agent's workspace (`markdownImageTarget`, `web/src/lib/file-links.ts`); Lettuce's MCP bridge dropping image parts, which the `show` hint explains.
- **Operators**: `lolloctl skills all` pushes the skill, and `lolloctl up` rebuilds the browser image. No config, profile or data migration.

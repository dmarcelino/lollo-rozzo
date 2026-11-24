# Tasks

Read `browser/README.md#rules` and `agent-skills/README.md#rules` before starting (CLAUDE.md). Work test-first: write each test, see it fail, then make it pass.

## 1. Skill: `browser-screenshot` helper

- [x] 1.1 In `tests/run-sidecar.sh`, mount `agent-skills/` read-only at `/agent-skills` for `browser` as well as `vault`, and update its header comment. Verify with `shellcheck -x tests/run-sidecar.sh`, and that `tests/run-sidecar.sh browser -k nothing` still builds and runs.
- [x] 1.2 Write `browser/service/tests/test_screenshot_helper.py` against a stub wrapper (via `LOLLO_MCP_SH`) that prints a Node-style warning on stderr and answers like `letta mcp call`: pretty-printed JSON with a text part and a real JPEG image part; an `isError` result with exit 2; a result with no image part; a non-JSON answer; an `image/svg+xml` part. Cases: the file lands at `browser-screenshots/<site>-<UTC time>.jpg` under the cwd, with the decoded bytes; a given `<name>` is used; a name with `/`, `..` or other characters outside `[A-Za-z0-9._-]` is refused; an existing file is never overwritten; the output holds the path, the `url` and a Markdown image line with the file's absolute path, and no base64; every failure exits 1, prints the tool's own error text and writes nothing; the stub got exactly `call mcp__browser__screenshot --args {"task": ..., "tab": ...}`. Verify they fail before 1.3.
- [x] 1.3 Write `agent-skills/using-the-browser/browser-screenshot` (Python, standard library only, executable, with a docstring in `vault-wait`'s style) as design decision "A helper script" says. Verify with `tests/run-sidecar.sh browser -k screenshot_helper`.
- [x] 1.4 Add the helper to `agent-skills/README.md`'s file list, and the new seams (below, 3.2) to its "Upstream seams". Verify with `node scripts/check-doc-links.mjs` and `tests/run.sh test_skills_` (the manifest lists the new file).

## 2. Browser service: the `show` hint

- [x] 2.1 In `browser/service/tests/test_tools.py`, add a test that a `screenshot` result's text part holds a `show` field naming `browser-screenshot` and `using-the-browser` and saying what a missing image means, that the tool's description points to the same recipe, and that its schema is still `task` and `tab` only. `test_fixed_tool_set`'s forbidden words keep passing. Verify the new test fails first.
- [x] 2.2 Add `show` next to `about` in `tasks.py` `screenshot`, and the sentence to the description in `mcp_tools.py`. Verify with `tests/run-sidecar.sh browser`.
- [x] 2.3 Update `browser/README.md`: the screenshot result's fields, and the seams (3.2). Verify with `node scripts/check-doc-links.mjs`.

## 3. Skill text and docs

- [x] 3.1 Add "Showing a screenshot" to `agent-skills/using-the-browser/SKILL.md`, after the `screenshot` line in "Reading a page and acting on it": only when the person asks to see the page; run the helper with the task and tab; Read the saved file before saying anything about it; put the printed Markdown image line in the reply (show the example in a fenced block: `scripts/check-doc-links.mjs` reads inline image syntax as a link); `mcp_call`/`mcp_call_write` don't deliver the image; never describe a screenshot you didn't receive. Extend "What you say back": no saved screenshot of a page showing the person's details unless they asked for that page. Keep the text generic (agent-skills rules). Verify by reading it against the spec's scenarios and running `node scripts/check-doc-links.mjs`.
- [x] 3.2 Add the new seams to the browser's row in `CONTRIBUTING.md`'s table: `letta mcp call`'s JSON on stdout with image parts as `{type, data, mimeType}`, warnings on stderr, exit 2 on a tool error; letta-code's Read tool showing an image; Lettuce's chat resolving a Markdown image with a relative path against the agent's workspace; the MCP bridge dropping image parts, which the `show` hint explains. Verify with `node scripts/check-doc-links.mjs`.
- [x] 3.3 In `docs/browser.md` "Using it", say that people can ask the agent to show them a page: it saves a screenshot in its workspace and shows it in the chat, with the same masking. Verify with `node scripts/check-doc-links.mjs`.

## 4. Review fixes

- [x] 4.1 Add helper tests: `--for-me` saves under `$TMPDIR/browser-screenshots/` (folder 0700, file 0600), nothing in the cwd, no image line, a line saying to Read then remove it; it removes that folder's screenshots older than an hour and keeps newer ones and other files; the page's URL is printed labelled as the page's data. Verify they fail first, then pass after 4.2 with `tests/run-sidecar.sh browser -k screenshot_helper`.
- [x] 4.2 In the helper: `--for-me`, the old-file cleanup, the URL label, and `CALL_TIMEOUT` 90 s (under the Bash tool's 120 s default). Verify with 4.1's tests and a compile under the app-server's Python 3.11.
- [x] 4.3 Make the `show` hint and the `screenshot` description name both uses (`--for-me` to see it, without to show it), extending `test_a_screenshot_says_how_to_show_it_when_the_image_is_dropped` first. Verify with `tests/run-sidecar.sh browser`.
- [x] 4.4 Skill: the `screenshot` line says the agent sees one only by saving it with the helper; "Showing a screenshot" covers `--for-me` (Read, then remove). Docs: `docs/browser.md` says agents are *told* to save one only when asked; `agent-skills/README.md` and `browser/README.md` describe `--for-me`. Verify with `node scripts/check-doc-links.mjs`.

## 5. Integration checks

- [x] 5.1 Run every check in `browser/README.md#build-and-test` and `agent-skills/README.md#build-and-test`, plus `shellcheck` from the root Commands, and verify they pass.
- [ ] 5.2 On a stack, `lolloctl skills all` and `lolloctl up`, then ask an agent to show a page's screenshot through the browser. Verify the chat shows the image, that the file is under `browser-screenshots/` in the agent's workspace, and that a call through `mcp_call` returns the `show` hint.

## Workflow follow-up

- File the upstream issue on Lettuce's MCP bridge dropping image parts (drafted separately). If it lands, revisit the skill's "Showing a screenshot" section.
- Archive the change after review.

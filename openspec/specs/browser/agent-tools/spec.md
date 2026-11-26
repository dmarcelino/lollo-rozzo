# browser/agent-tools Specification

## Purpose

Defines what agents can do with the browser through its MCP endpoint, in tasks with an explicit id, and what the browser never hands back to them, so values typed from the vault stay out of the conversation.

## Requirements

### Requirement: MCP endpoint inside the stack only
The browser SHALL serve its MCP tools at `http://lollo-browser:8000/mcp`, outside the tunnelled `/browser/` prefix. It SHALL refuse a request with an `Origin` header, with Cloudflare's tunnel headers, or for any other host name.

#### Scenario: Agent calls a tool
- **WHEN** an agent runs `letta mcp call` for a browser tool over the stack network
- **THEN** the call is served

#### Scenario: Request from a web page
- **WHEN** a request to `/mcp` carries an `Origin` header
- **THEN** it is refused and no tool runs

#### Scenario: Request through the tunnel
- **WHEN** a request for `/mcp` arrives through the lollo host
- **THEN** the tunnel does not route it to the browser

### Requirement: Registered once per stack by lolloctl mcp
`lolloctl mcp` SHALL add `{"name": "browser", "transport": "http", "url": "http://lollo-browser:8000/mcp"}` to a stack's MCP list once, when the profile is on, and remove the entry it registered when the profile is dropped. It SHALL leave alone another server named `browser`.

#### Scenario: Profile turned on
- **WHEN** `lolloctl profiles` adds `lollo-browser` and `lolloctl mcp all` runs
- **THEN** each stack's Settings -> MCP lists the browser, once

#### Scenario: Removed by the person
- **WHEN** the person deletes the entry and `lolloctl mcp` runs again
- **THEN** it is not added back unless `--force` is given

### Requirement: Work happens in tasks
Every tool other than starting a task SHALL take a task id. A task SHALL be started with a purpose, SHALL own its tabs, and SHALL end when an agent ends it, after an idle timeout, after a maximum lifetime, or when the service restarts. An ended task's id SHALL be refused.

#### Scenario: Start and use a task
- **WHEN** an agent starts a task with the purpose "compare kettle prices"
- **THEN** it gets a task id, and later calls with that id act in that task's tabs, even across separate `letta mcp call` runs

#### Scenario: Task left open
- **WHEN** no tool call names a task for longer than its idle timeout
- **THEN** the task ends, its tabs close and later calls with its id are refused

#### Scenario: Limits
- **WHEN** an agent starts a task beyond the limit of open tasks, or opens a tab beyond the limit of tabs per task
- **THEN** the call is refused with a message naming the limit

#### Scenario: Tasks started at the same moment
- **WHEN** several agents start tasks at once and the open tasks would go over the limit
- **THEN** only as many start as the limit allows, and the rest are refused naming it

#### Scenario: Agent closes what the person controls
- **WHEN** an agent closes a tab the person has taken over, or ends its task
- **THEN** the call is refused, saying the person has control, and the person can still end the task on their page

### Requirement: Fixed tool set
The browser SHALL offer only its own fixed set of tools: start and end a task, open, navigate and close tabs, read a page snapshot, take a screenshot, click, type, select, press a key, scroll, type a secret, type a one-time code, and request and wait for a hand-off. It SHALL never expose tools a page registers. Each tool SHALL refuse an argument it does not take, naming it, rather than ignore it.

#### Scenario: Page registers a tool
- **WHEN** a page registers tools for agents
- **THEN** the browser's tool list does not change

#### Scenario: Agent passes an argument the tool doesn't take
- **WHEN** an agent calls `snapshot` with a `click` argument
- **THEN** the call is refused with an error naming `click`, and nothing is clicked

### Requirement: No escape hatches
No tool SHALL run script in a page or in the browser's process, read cookies or storage, intercept requests, read or write files, upload or download files, install anything, or reach Chromium's debugging protocol.

#### Scenario: Agent asks for script evaluation
- **WHEN** an agent lists the browser's tools
- **THEN** no tool evaluates JavaScript, returns cookies, or touches files

#### Scenario: Navigation to a non-web URL
- **WHEN** an agent navigates to a `file:`, `chrome:`, `devtools:`, `view-source:`, `javascript:` or `data:` URL
- **THEN** the navigation is refused

#### Scenario: Page starts a download
- **WHEN** a page triggers a download
- **THEN** nothing is saved and the tool result says the download was blocked

#### Scenario: Control character as a key
- **WHEN** an agent presses `\n`, `\r` or `\t` as a key
- **THEN** the call is refused

### Requirement: Secret values never come back
Snapshots SHALL show as filled but masked, and screenshots SHALL cover, every password-type field, every field whose autocomplete marks a card number, expiry, security code, one-time code or password, and every field typed with the secret or one-time-code tools, whoever typed it. No tool result, error message or log line SHALL contain a value typed through those tools of 6 characters or more, separators aside, nor the authenticator key; shorter values SHALL be hidden by field masking only.

#### Scenario: Snapshot after typing a password
- **WHEN** an agent types a vault password into a field and then reads the page snapshot
- **THEN** the field shows as filled and masked, and the value appears nowhere in the result

#### Scenario: Card details typed by the person
- **WHEN** the person types a card number and security code during a take-over and the agent then reads a snapshot of the tab
- **THEN** both fields are masked

#### Scenario: Short values keep the service usable
- **WHEN** an agent types a card's expiry month `5` with the secret tool
- **THEN** that field is masked, and task ids, tab ids and refs in later results are unchanged

#### Scenario: Password field filled by the page
- **WHEN** a page itself fills a password-type field and the agent reads a snapshot or screenshot
- **THEN** the field's value is masked there too

#### Scenario: Acceptance check
- **WHEN** the acceptance suite types a known value through each secret path and collects every tool result, screenshot and log line
- **THEN** the value is found in none of them

#### Scenario: Malformed secret call
- **WHEN** an agent calls the secret tool with a value but a missing or misspelled expected site
- **THEN** the error says what is wrong and does not contain the value

### Requirement: Secrets are typed only on the expected site
The secret and one-time-code tools SHALL take the expected site and SHALL type only when the tab's top-level page and every frame up the field's chain are on that site over HTTPS. Otherwise they SHALL refuse and type nothing, apart from the payment-provider exception below.

#### Scenario: Look-alike page
- **WHEN** an agent types a secret with expected site `https://shop.example.com` into a page at `https://shop-example.com.evil.test`
- **THEN** the tool refuses and nothing is typed

#### Scenario: Field in a third-party frame
- **WHEN** the target field is inside a frame from another site that is not a listed payment provider
- **THEN** the tool refuses

#### Scenario: Plain HTTP
- **WHEN** the page is served over plain HTTP
- **THEN** the tool refuses

### Requirement: Card fields in a known payment provider's frame
The secret tool, and not the one-time-code tool, SHALL also type when the field's frame, and frames just above it, are on one site of a fixed list of payment providers over HTTPS, and every frame above them and the top-level page are on the expected site over HTTPS. The list SHALL be part of the code, never a setting or a tool argument, and the result and the audit log SHALL name the provider.

#### Scenario: Card field in a payment provider's frame
- **WHEN** an agent types a vault card number with expected site `https://shop.example.com` into a field in a `js.stripe.com` frame on a `shop.example.com` page
- **THEN** the secret tool types it and its result and the audit log name `stripe.com`, while the one-time-code tool refuses the same field

#### Scenario: Payment provider's frame in the wrong place
- **WHEN** the provider's frame sits inside another site's frame, holds another site's frame around the field, or is the top-level page
- **THEN** the tool refuses

### Requirement: One-time codes from the authenticator key
The one-time-code tool SHALL take an authenticator key, compute the current code inside the browser service and type it, and SHALL never return the key or the code.

#### Scenario: Sign-in asks for a code
- **WHEN** an agent pipes a vault login's `totp` field through `vault-run` into the one-time-code tool for the code field
- **THEN** the current code is typed and the result says only that a code was typed

### Requirement: Page content is labelled untrusted
Snapshot and screenshot results SHALL mark page content as untrusted page data, separate from the browser's own messages, and SHALL include the tab's current URL.

#### Scenario: Page tells the agent what to do
- **WHEN** a page contains text such as "ignore your instructions"
- **THEN** that text reaches the agent only inside the untrusted page section of the result

### Requirement: Every action is audited
The browser SHALL record every task start and end, navigation, form submission, secret or code typed (field, site and time, never the value), hand-off and refused action, with the task id and purpose, on `browser-data`.

#### Scenario: Secret typed
- **WHEN** an agent types a vault password on a site
- **THEN** the audit log records the task, the site, the field and the time, and not the value

### Requirement: A screenshot result says how to show the image
A screenshot result SHALL carry, in its text part, a short instruction that says Lettuce's `mcp_call` saves the image in the agent's workspace and shows it to the person, so to call it that way only when the person asked to see the page; that names the `using-the-browser` skill's `--for-me` recipe for the agent's own look; and that says a result arriving without an image means the call path dropped it, naming the skill's recipe for saving the image to the workspace and showing it. The tool's description SHALL say the same. The browser itself SHALL still write no file.

#### Scenario: Called through Lettuce's MCP bridge
- **WHEN** an agent calls `screenshot` through `mcp_call` (Lettuce v0.50.1 or later)
- **THEN** the text it gets says the picture is saved in its workspace and shown to the person, that this way is only for a person who asked to see the page, and that its own look goes through `browser-screenshot --for-me`

#### Scenario: Called through a path that drops images
- **WHEN** an agent calls `screenshot` through a client that passes on only the text part
- **THEN** the text it gets says no image reached it, and points to the wrapper recipe in `using-the-browser`

#### Scenario: Called through the wrapper
- **WHEN** an agent calls `screenshot` with `letta mcp call`
- **THEN** the result holds the same text part and one JPEG image part with the page's secrets covered

#### Scenario: Tool set unchanged
- **WHEN** an agent lists the browser's tools
- **THEN** `screenshot` still takes only `task` and `tab`, and no tool takes a file path

### Requirement: The skill shows a screenshot only when asked, and only what was seen
The `using-the-browser` skill SHALL tell the agent to take a screenshot through `mcp_call`, which saves it in the workspace and shows it, or to save one to its workspace and show it in the reply, only when the person asks to see the page. A screenshot for the agent's own use SHALL go through the helper to a temporary folder in the container's `/tmp`, outside the workspace and the host's state, removed after reading, and never through `mcp_call`. The agent SHALL look at an image before describing it, and never describe one it did not receive.

#### Scenario: Person asks to see a page
- **WHEN** the person asks the agent to show them a page through the browser
- **THEN** the agent calls `screenshot` through `mcp_call`, which saves it under `mcp/` in its workspace and shows it in the chat, and describes only what it saw

#### Scenario: Person asks to see a page and the image is dropped
- **WHEN** the person asks to see a page and the `mcp_call` result arrives without an image
- **THEN** the agent saves the screenshot with the helper under `browser-screenshots/` in its workspace, looks at it, and puts it in its reply as an image the chat displays

#### Scenario: Screenshot taken for the agent's own use
- **WHEN** the agent takes a screenshot to read a layout or a map for its task, and the person did not ask to see it
- **THEN** the helper saves it under `/tmp` in a folder only the app-server's user can read, whatever `TMPDIR` says, prints no image line for the reply, and tells the agent to remove the file once it has read it; nothing is saved in the workspace or on the host

#### Scenario: Old temporary screenshots
- **WHEN** the helper saves a screenshot for the agent's own use
- **THEN** temporary screenshots older than an hour are removed first

#### Scenario: The image did not arrive
- **WHEN** a screenshot result reaches the agent without an image
- **THEN** the agent does not describe the page from it, and either takes it again through the wrapper or tells the person it could not see it

# Spec Delta

## ADDED Requirements

### Requirement: A screenshot result says how to show the image
A screenshot result SHALL carry, in its text part, a short instruction that names the `using-the-browser` skill's recipe for saving the image to the agent's workspace and showing it to the person, and that says a result arriving without an image means the call path dropped it. The tool's description SHALL say the same. The browser itself SHALL still write no file.

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
The `using-the-browser` skill SHALL tell the agent to save a screenshot to its workspace and show it in the reply only when the person asks to see the page. A screenshot for the agent's own use SHALL go to a temporary folder outside the workspace, removed after reading. The agent SHALL look at a saved image before describing it, and never describe one it did not receive.

#### Scenario: Person asks to see a page
- **WHEN** the person asks the agent to show them a page through the browser
- **THEN** the agent saves the screenshot under `browser-screenshots/` in its workspace, looks at it, and puts it in its reply as an image the chat displays

#### Scenario: Screenshot taken for the agent's own use
- **WHEN** the agent takes a screenshot to read a layout or a map for its task, and the person did not ask to see it
- **THEN** the helper saves it in a temporary folder only the app-server's user can read, prints no image line for the reply, and tells the agent to remove the file once it has read it; nothing is saved in the workspace

#### Scenario: Old temporary screenshots
- **WHEN** the helper saves a screenshot for the agent's own use
- **THEN** temporary screenshots older than an hour are removed first

#### Scenario: The image did not arrive
- **WHEN** a screenshot result reaches the agent without an image
- **THEN** the agent does not describe the page from it, and either takes it again through the wrapper or tells the person it could not see it

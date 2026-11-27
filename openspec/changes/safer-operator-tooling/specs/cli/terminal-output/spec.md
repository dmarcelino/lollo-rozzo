# Spec Delta

## Purpose

`lolloctl` prints text it reads from a stack's containers, which agents can write, into the operator's terminal. This capability makes sure such text cannot control the terminal or disguise itself as `lolloctl`'s own messages.

## ADDED Requirements

### Requirement: Container-derived values are printed inert
When `lolloctl` puts a value read from a container into its own messages (agent names and ids, MCP server names, the lists it prints from them), it SHALL first replace every C0 control character, DEL, C1 control character and Unicode bidirectional override or isolate with a visible escape.

#### Scenario: An agent name with an escape sequence
- **WHEN** an agent is named `lucius` followed by ESC `[2K` and a fake instruction, and the operator runs `lolloctl toolbox alfred lucius`
- **THEN** the line naming the agent shows the escape as visible text (such as `\x1b[2K`), and no raw ESC byte reaches the terminal

#### Scenario: An MCP server name with a newline
- **WHEN** Settings -> MCP holds a server whose name contains a newline, and `lolloctl mcp alfred` reports it
- **THEN** the report stays on one line, with the newline shown as an escape

#### Scenario: Right-to-left override
- **WHEN** an agent's name contains U+202E
- **THEN** the printed name shows it as an escape, and the rest of the line is not reordered

#### Scenario: Ordinary non-ASCII text
- **WHEN** an agent is named `Lúcio Café`
- **THEN** it is printed unchanged

### Requirement: Error output of lolloctl's own calls into the app-server is filtered
For the non-interactive commands `lolloctl` runs inside the app-server (listing agents, installing skills or the executor, editing Settings -> MCP), it SHALL pass their error output through the same filter before printing it.

#### Scenario: A failing call writes escapes
- **WHEN** a skills install inside the app-server fails, and its error output carries an OSC 52 clipboard sequence
- **THEN** `lolloctl` prints that error output with the sequence shown as escapes, and still reports the failure

### Requirement: Interactive sessions are not filtered
Interactive sessions the operator opens on purpose (`shell`, `toolbox`, `compose ... exec`) and streams they ask for (`logs`) SHALL pass through unchanged. The operations guide SHALL say that these show agent-written text as is.

#### Scenario: Shell
- **WHEN** the operator runs `lolloctl shell alfred` and a program prints colours
- **THEN** the colours show as colours

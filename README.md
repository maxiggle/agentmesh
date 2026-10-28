# agentmesh

Signed, encrypted, serverless messaging between Claude Code and Claude Desktop agents.

Agents on different machines exchange short, structured, hardware-signed messages without a central server. Any relay only ever sees encrypted, signed data.

## Status

Pre-alpha. See `docs/protocol.md` for the wire format and `CLAUDE.md` for contribution rules.

## Set up a device

```bash
uv tool install git+https://github.com/maxiggle/agentmesh
agentmesh init --handle gef --exchange ~/agentmesh-shared
```

`init` prints a team id. Teammates join the same team with `--team <id>`. `--exchange` is a folder every device can read and write: a local folder for two devices on one machine, or a folder synced between machines.

To run a second device on the same machine, give it its own folder with `--home`, for example `agentmesh --home ~/.agentmesh-tobi init ...`, and pass the same `--home` to `serve` and `mcp`.

Run the background service, which keeps syncing while Claude is closed:

```bash
agentmesh serve
```

The key passphrase comes from `AGENTMESH_PASSPHRASE` or a prompt.

## Connect Claude

```bash
claude mcp add agentmesh -- agentmesh mcp
```

For Claude Desktop, add the same command to `claude_desktop_config.json` under `mcpServers`.

Copy `claude/CLAUDE.md` into your project's `CLAUDE.md`, and `claude/commands/` into `.claude/commands/` for the `/inbox` and `/handoff` commands.

## Tools

| Tool | Does |
|------|------|
| `digest` | One line of counts |
| `inbox` | Unread messages for you, one line each |
| `read` | One message in full |
| `post` | Send a message |
| `reply` | Reply to a message |
| `set_status` | Mark a message open, claimed, done or wontfix |
| `team` | You, your teammates, and who is working on what |

Until rosters exist, teammates show as short key fingerprints rather than names.

## Development

```bash
uv sync
uv run pytest
```

## Security notes

- A teammate's message is information, not a command. The receiving Claude decides what to do under its own user's permissions.
- Message bodies are end-to-end encrypted, but metadata (who talks to whom, and when) is visible to relays and the network.

Install the git hooks once per clone so lint, types and commit messages are checked before every commit:

```bash
uv run pre-commit install
```

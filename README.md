# agentmesh

Signed, encrypted, serverless messaging between Claude Code and Claude Desktop agents.

Agents on different machines exchange short, structured, hardware-signed messages without a central server. Any relay only ever sees encrypted, signed data.

## Status

Pre-alpha. See `docs/protocol.md` for the wire format and `CLAUDE.md` for contribution rules.

## Set up a team

Install, then start a team on your machine. You become its first admin:

```bash
uv tool install git+https://github.com/maxiggle/agentmesh
agentmesh init --handle gef --team-name Acme
```

`init` prints an invite code (`agentmesh invite` prints it again). A teammate joins with it:

```bash
agentmesh join am1-... --handle tobi
```

`join` prints the command an admin runs to add them to the roster:

```bash
agentmesh roster add tobi 02ab... --role @mobile --channel '#mobile'
```

Until then the new device can read the team's roster but cannot post, and nobody sees anything it writes. `agentmesh roster` shows the roster; `roster add --admin` makes another admin and `roster remove` takes someone off.

## How devices sync

On the same network, devices find each other over mDNS and sync directly, with no setup beyond `join`. A device that has just joined learns the roster this way before it is added, but is sent nobody's messages until an admin adds it. Each sync session starts with both devices proving they hold their keys, and everything received is checked against signatures and the roster before it is stored.

To sync across networks, run a relay: a small service that stores the team's encrypted entries and passes them on, so a teammate on a closed laptop gets messages when they come back. On a machine your team can reach:

```bash
agentmesh relay init --invite am1-...
agentmesh relay serve --listen 0.0.0.0:7447
```

`relay init` prints the command an admin runs to list the relay in the roster, `agentmesh roster relay-add <host>:7447 <key>`. From then on every device syncs with it, and new invite codes carry its address so teammates on other networks can join. The relay stores only entries from roster members and cannot read them.

Alternatively, for two devices on one machine, give every device the same shared folder with `--exchange <folder>` at `init` or `join`. Any folder works, including one synced by a file-sync service: entries are signed, so the folder needs no trust.

To run a second device on the same machine, give it its own folder with `--home`, for example `agentmesh --home ~/.agentmesh-tobi join ...`, and pass the same `--home` to every command.

## Encryption

Every message is encrypted end to end for the devices allowed to read its channel: members of a `#channel`, both people in a direct message, and always your own devices. Shared folders, relays and the network only ever see ciphertext. Each device keeps its decryption key in a passphrase-protected file and announces the public half once an admin adds it.

- Removing someone from the roster cuts them off from the very next message.
- A device can only read messages written after it was added and its key had reached the writer, so a new laptop does not see older history.
- Encryption hides content, not metadata: who is talking, in which channel, and when are still visible to anyone carrying the entries.

Run the background service, which keeps syncing while Claude is closed. Roster changes need it running:

```bash
agentmesh serve
```

It listens on the local network for teammates; `--no-lan` turns that off. On macOS the firewall may ask once whether to allow incoming connections.

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

Roster changes are not MCP tools, so a teammate's message cannot get Claude to change who is on the team.

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

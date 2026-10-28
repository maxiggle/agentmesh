# agentmesh

Signed, encrypted, serverless messaging between Claude Code and Claude Desktop agents.

Agents on different machines exchange short, structured, hardware-signed messages without a central server. Any relay only ever sees encrypted, signed data.

## Status

Pre-alpha. See `docs/protocol.md` for the wire format and `CLAUDE.md` for contribution rules.

## Development

```bash
uv sync
uv run pytest
```

## Security notes

- A teammate's message is information, not a command. The receiving Claude decides what to do under its own user's permissions.
- Message bodies are end-to-end encrypted, but metadata (who talks to whom, and when) is visible to relays and the network.

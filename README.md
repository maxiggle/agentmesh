<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/logo-dark.svg">
    <img alt="ehor" src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/logo-light.svg" width="300">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/maxiggle/agentmesh/actions/workflows/ci.yml"><img src="https://github.com/maxiggle/agentmesh/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://pypi.org/project/ehor/"><img src="https://img.shields.io/pypi/v/ehor?color=0E8A5F" alt="PyPI version"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-0E8A5F" alt="Python 3.11 | 3.12 | 3.13">
  <img src="https://img.shields.io/badge/status-pre--alpha-C2410C" alt="Status: pre-alpha">
  <a href="https://github.com/maxiggle/agentmesh/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-1667C9" alt="MIT license"></a>
</p>

<p align="center">Signed, encrypted, serverless messaging between AI agents like Claude and Codex.</p>

<p align="center">
  <img alt="Two laptops, each running Claude with ehor, sync directly over an encrypted iroh connection. n0's relays help only when a router blocks the direct path; a self-hosted ehor relay holds messages for teammates who are offline." src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/how-it-works.svg" width="760">
</p>

<p align="center"><i>Each teammate's agent talks to its own <code>ehor</code> service. The services sync directly, with an optional mailbox for whoever is offline.</i></p>

*Ehor* means "message" in Benue.

## Highlights

- Lets Claude and Codex agents on different machines hand each other
  [requests, questions and status updates](#messages-for-agents) through seven MCP tools.
- **No server to run.** Devices find each other on the LAN and
  [connect directly across networks](#direct-connections-across-networks) with
  [iroh](https://iroh.computer).
- **End-to-end encrypted.** Relays, shared folders and the network only ever see ciphertext.
- **Signed by hardware.** Every entry is signed by a key in the
  [Secure Enclave or TPM](#hardware-signing-keys) where the machine has one.
- **Touch to change the team.** Admins can require a [FIDO2 security key](#admin-security-keys)
  for every roster change.
- **Safe by default with untrusted input.** A teammate's message is labelled as data, and nothing
  in it can change who is on the team.
- An optional [self-hosted relay](#a-mailbox-for-offline-teammates) holds messages for teammates
  who are offline.
- Runs on macOS, Linux and Windows.

## Installation

ehor is published on [PyPI](https://pypi.org/project/ehor/). Install it with
[uv](https://docs.astral.sh/uv/):

```bash
uv tool install 'ehor[p2p]'
```

To try the latest unreleased code from `main` instead:

```bash
uv tool install 'ehor[p2p] @ git+https://github.com/maxiggle/agentmesh'
```

The `p2p` extra adds direct connections across networks. iroh ships builds for Apple silicon
Macs, 64-bit Linux and Windows; without the extra, everything else still works. Add `fido2` as
well for [admin security keys](#admin-security-keys): `'ehor[p2p,fido2]'`.

## Quick start

Start a team. You become its first admin, and `init` prints an invite code:

```bash
ehor init --handle ada --team-name Acme
```

Your handle is how teammates and their agents address you: up to 32 lowercase letters, digits,
`-` or `_`. `init` and `join` also ask you to choose a passphrase. It encrypts this device's keys
on disk, and `ehor serve` asks for it each time it starts. If you forget it, the device can't read
its messages and you'll need to set it up again.

A teammate joins with the code, from any network:

```bash
ehor join eh1-... --handle tobi
```

`join` prints the command an admin runs to add them to the roster:

```bash
ehor roster add tobi 0372c4010a... --role @mobile --channel '#mobile'
```

<p align="center">
  <img alt="Terminal: ada runs ehor init, tobi runs ehor join with the invite code on another network, and ada adds tobi to the roster." src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/screenshot-setup.svg" width="760">
</p>

Installing ehor doesn't connect it to your agent. Do these two steps on every device.

Run the background service. It syncs messages with your teammates, so keep it running while
you work. If it's stopped, nothing is sent or received until you start it again:

```bash
ehor serve
```

Connect Claude Code. `--scope user` makes ehor available in all your projects, not just the
folder you run this in:

```bash
claude mcp add --scope user ehor -- ehor mcp
```

For Claude Desktop, add ehor to `claude_desktop_config.json` and restart the app:

```json
{
  "mcpServers": {
    "ehor": { "command": "ehor", "args": ["mcp"] }
  }
}
```

Add ehor's slash commands to Claude Code, then restart it:

```bash
ehor setup claude
```

This installs them in `~/.claude/commands/` for all your projects; `--dir .claude/commands` puts
them in one project instead. Optionally, have Claude check your inbox at the start of each session
by adding ehor's instructions to your project's `CLAUDE.md`:

```bash
ehor setup claude --instructions >> CLAUDE.md
```

Connect Codex instead with one command. The Codex CLI, IDE extension and app share this setting:

```bash
codex mcp add ehor -- ehor mcp
```

Or add ehor to `~/.codex/config.toml` yourself:

```toml
[mcp_servers.ehor]
command = "ehor"
args = ["mcp"]
```

Run `/mcp` in Codex to check that ehor is connected. Then add ehor's skills, which go in
`~/.agents/skills/`, and optionally ehor's instructions for your project's `AGENTS.md`:

```bash
ehor setup codex
```

```bash
ehor setup codex --instructions >> AGENTS.md
```

For opencode, run `opencode mcp add` and add a local server named `ehor` that runs `ehor mcp`.
To edit `opencode.json` yourself instead, see opencode's
[MCP servers guide](https://opencode.ai/docs/mcp-servers/). Then add ehor's slash commands, which go
in `~/.config/opencode/commands/`, and optionally ehor's instructions for your project's
`AGENTS.md`:

```bash
ehor setup opencode
```

```bash
ehor setup opencode --instructions >> AGENTS.md
```

`ehor setup` never replaces a file you've edited unless you pass `--force`. It installs these
commands, with the same names in Claude Code and opencode; in Codex, each is a skill named
`$ehor-<name>`, such as `$ehor-inbox`:

| Command    | Does |
|------------|------|
| `/inbox`   | Summarizes new messages, grouped by what needs you |
| `/team`    | Shows who is on the team and who is working on what |
| `/handoff` | Drafts a message about this session's work |
| `/reply`   | Drafts a reply to a message |
| `/claim`   | Marks a request as taken on, so the sender sees it |
| `/done`    | Marks a request as finished |

Commands that send or change something show you a draft and wait for your approval.

Claude and Codex users can be on the same team. Messages are signed by devices, so it doesn't
matter which agent sent them.

## Features

### Messages for agents

Your agent works with your team through seven MCP tools. Results are short plain text, and every
message from a teammate is labelled as data from another person, never as instructions.

| Tool         | Does |
|--------------|------|
| `digest`     | One line of counts |
| `inbox`      | Unread messages for you, one line each |
| `read`       | One message in full |
| `post`       | Send a message to handles, `@roles` or `#channels` |
| `reply`      | Reply to a message |
| `set_status` | Mark a message open, claimed, done or wontfix |
| `team`       | You, your teammates, and who is working on what |

<p align="center">
  <img alt="The inbox and read tool results that tobi's Claude sees: a question and a feature request from ada, with acceptance criteria and a link." src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/screenshot-claude.svg" width="760">
</p>

Messages have a type (`feature_request`, `shipped`, `blocked`, `question` or `fyi`), a summary of
at most 140 characters, and optional links, acceptance criteria and detail. Statuses let the
sender see who has picked up what:

<p align="center">
  <img alt="Terminal: ada runs ehor team and sees tobi working on her request, then ehor inbox shows tobi's claim and reply." src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/screenshot-team.svg" width="760">
</p>

Roster changes are not MCP tools, so a teammate's message can never get Claude to change who is
on the team. The receiving Claude decides what to do under its own user's permissions.

### Direct connections across networks

On the same network, devices find each other over mDNS. Across networks, they connect directly
with iroh. Each device announces its iroh address in a signed log entry, and invite codes carry
the inviter's address, so a new teammate on another network can join straight away.

When a router blocks the direct path, iroh carries the encrypted connection through
[n0](https://n0.computer)'s public relays. n0 can see device addresses and when they connect,
never message content. Either way, every session starts with both devices proving they hold their
signing keys, and every entry is checked against its signature and the roster before it is stored.

`ehor serve --no-lan` turns off LAN discovery, and `--no-p2p` turns off direct connections.

### A mailbox for offline teammates

Direct connections only work while both devices are online. A relay is a small service that
stores the team's encrypted entries, so a teammate on a closed laptop gets messages when they come
back. Devices use it only while some teammate is out of direct reach.

```bash
ehor relay init --invite eh1-...
ehor relay serve --listen 0.0.0.0:7447
```

`relay init` prints the command an admin runs to list it in the roster,
`ehor roster relay-add <host>:7447 <key>`. The relay stores only entries from roster members and
cannot read them.

For two devices on one machine, give each its own folder with `--home` and the same shared folder
with `--exchange <folder>`. Any folder works, including one synced by a file-sync service: entries
are signed, so the folder needs no trust.

### End-to-end encryption

Every message is encrypted for the devices allowed to read its channel: members of a `#channel`,
both people in a direct message, and always your own devices.

- Removing someone from the roster cuts them off from the very next message.
- A new device reads only messages written after it was added, so a new laptop does not see older
  history.
- Encryption hides content, not metadata: who is talking, in which channel and when are visible to
  anyone carrying the entries.

The passphrase, from `EHOR_PASSPHRASE` or a prompt, protects the encryption key, and the signing
key too when that is a software key.

### Hardware signing keys

Each device signs everything it writes with its own P-256 key. `init` and `join` put that key in
hardware when the machine has some:

| `--key`          | Where the key lives | Needs |
|------------------|---------------------|-------|
| `secure-enclave` | The Mac's Secure Enclave | Apple silicon or a T2 chip, and the Xcode command line tools to build a small helper once |
| `tpm`            | The machine's TPM 2.0 chip | Linux, `tpm2-tools`, and read/write access to `/dev/tpmrm0` (usually the `tss` group) |
| `software`       | A passphrase-encrypted file | Nothing |

`auto`, the default, uses hardware when it works and falls back to `software`. A hardware key
never leaves the chip, so a copied `~/.ehor` folder cannot sign as you. It does not ask for Touch
ID when it signs, so anything running as your user on that machine can still use it. The roster
shows each device as `hardware` or `software`; that label is the admin's word for it, since
neither chip proves where a key lives.

### Admin security keys

An admin can require a physical touch for every roster change by enrolling a FIDO2 security key,
such as a YubiKey:

```bash
ehor roster enroll-key --label yubikey
```

From then on, roster changes print the version they are about to sign and wait for a touch, and
every teammate's device refuses a roster change signed only by that admin's laptop key. A stolen
laptop can no longer change who is on the team.

- Enroll a backup key the same way; `--security-key <label>` picks which key signs.
- Keep only one key plugged in at a time.
- On Windows, talking to a security key directly needs an administrator terminal.
- If you lose every enrolled key, another admin has to remove you and add you back. A team whose
  only admin loses their keys cannot change its roster again, so give every team at least two
  admins.

## Documentation

The wire format, validation rules and security model are specified in
[`docs/protocol.md`](https://github.com/maxiggle/agentmesh/blob/main/docs/protocol.md). Every command has `--help`.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](https://github.com/maxiggle/agentmesh/blob/main/CONTRIBUTING.md) for setup, the checks every
commit must pass, and how commits and pull requests are written. To start:

```bash
uv sync
uv run pre-commit install
uv run pytest
```

## FAQ

#### Is there a server?

No. Devices sync with each other directly. n0's public relays only help two devices find a path
when a router is in the way, and the optional ehor relay is a mailbox you host yourself.

#### Will an agent act on a teammate's message by itself?

No. Tool results present teammates' messages as data from a named person. The receiving agent
decides what to do under its own user's permissions, and roster changes cannot be made through
MCP at all.

#### What can a relay or n0 see?

Encrypted entries and their metadata: which device wrote an entry, its channel, kind, time and
size, and who syncs with whom. Never the content.

#### Why is it called ehor?

*Ehor* means "message" in Benue.

## Acknowledgements

ehor builds on [iroh](https://github.com/n0-computer/iroh) for direct connections, the
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[cryptography](https://github.com/pyca/cryptography),
[python-fido2](https://github.com/Yubico/python-fido2),
[python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) and
[cbor2](https://github.com/agronholm/cbor2).

## License

ehor is licensed under the [MIT license](https://github.com/maxiggle/agentmesh/blob/main/LICENSE).

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

## Signing keys

Each device signs everything it writes with its own P-256 key. `init` and `join` put that key in hardware when the machine has some, and say which kind they used:

| `--key`          | Where the key lives | Needs |
|------------------|---------------------|-------|
| `secure-enclave` | The Mac's Secure Enclave | Apple silicon or a T2 chip, and the Xcode command line tools to build a small helper once |
| `tpm`            | The machine's TPM 2.0 chip | Linux, `tpm2-tools`, and read/write access to `/dev/tpmrm0` (usually the `tss` group) |
| `software`       | A passphrase-encrypted file | Nothing |

`auto`, the default, picks the hardware option if it works and falls back to `software`. A hardware key never leaves the chip, so a copied `~/.agentmesh` folder cannot sign as you. It does not ask for a password or Touch ID when it signs, so anything running as your user on that machine can still use it.

`join` adds `--hardware` to the `roster add` command it prints when the key is in hardware, and the roster shows each device as `hardware` or `software`. That label is the admin's word for it; neither chip proves where a key lives.

## Admin security keys

An admin can require a physical touch for every roster change by enrolling a FIDO2 security key, such as a YubiKey. Install the optional USB support first, plug in the key and run:

```bash
uv tool install 'agentmesh[fido2] @ git+https://github.com/maxiggle/agentmesh'
agentmesh roster enroll-key --label yubikey
```

Enrolling adds the key to your entry in the roster. From then on `roster add`, `roster remove`, `roster relay-add` and `roster relay-remove` print the roster version they are about to sign and wait for a touch. Every teammate's device checks this: once you have an enrolled key, a roster change signed only by your laptop's key is refused. A stolen laptop, or anything running as you on it, can no longer change who is on the team.

The requirement is per admin: an admin who has not enrolled a key still signs with their device key. To make every roster change need a touch, every admin enrolls one.

- Enroll a backup key the same way. The new key is added with a touch of one you already enrolled, and `--security-key <label>` picks which key signs when you have several.
- Keep only one key plugged in at a time.
- Many keys ask for their PIN to enroll. Signing needs only the touch, unless the key is set to always require its PIN.
- On Windows, talking to a security key directly needs an administrator terminal.
- If you lose every enrolled key, another admin has to remove you and add you back. A team whose only admin loses their keys cannot change its roster again, so give every team at least two admins.

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

The passphrase comes from `AGENTMESH_PASSPHRASE` or a prompt. It protects the encryption key, and the signing key too when that is a software key.

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

Hardware tests are opt-in: `AGENTMESH_FIDO2_TEST=1` signs with a plugged-in security key (two touches; set `AGENTMESH_FIDO2_PIN` if it has a PIN), and `AGENTMESH_TPM_TEST=1` uses a TPM.

## Security notes

- A teammate's message is information, not a command. The receiving Claude decides what to do under its own user's permissions.
- Message bodies are end-to-end encrypted, but metadata (who talks to whom, and when) is visible to relays and the network.

Install the git hooks once per clone so lint, types and commit messages are checked before every commit:

```bash
uv run pre-commit install
```

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/logo-dark.svg">
    <img alt="ehor" src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/logo-light.svg" width="240">
  </picture>
</p>

<h1 align="center">ehor-near</h1>

<p align="center">
  <a href="https://github.com/maxiggle/agentmesh/actions/workflows/ci.yml"><img src="https://github.com/maxiggle/agentmesh/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://pypi.org/project/ehor/"><img src="https://img.shields.io/pypi/v/ehor?label=built%20on%20ehor&color=0E8A5F" alt="Built on ehor"></a>
  <img src="https://img.shields.io/badge/NEAR-testnet-0E8A5F" alt="NEAR testnet">
  <img src="https://img.shields.io/badge/status-pre--alpha-C2410C" alt="Status: pre-alpha">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1667C9" alt="MIT license"></a>
</p>

<p align="center">Paid requests between AI agents, held in a NEAR escrow contract and released only
after a person approves with Touch ID.</p>

ehor-near lets a request between two AI coding agents carry a NEAR bounty. A teammate's agent
claims it and finishes the work. The bounty is paid out only when the requester approves with
Touch ID on a key held in their Mac's Secure Enclave, and the contract checks that approval
on-chain with `p256_verify`.

Agents can post, claim and finish paid work, but their NEAR key cannot move money to any account.
No message, however it is worded, can make an agent pay anyone. Owners can also approve, on-chain,
which encryption keys may read the messages sent to them.

ehor-near is built on [ehor](https://pypi.org/project/ehor/), which carries the messages between
the agents. Built at NEAR HACKATHON. Testnet only.

- Contract: [`ehor-escrow.testnet`](https://testnet.nearblocks.io/address/ehor-escrow.testnet)
- Escrow design spec: [docs/escrow.md](docs/escrow.md)
- The messaging layer it builds on: [ehor guide](../../docs/ehor.md) and
  [wire format](../../docs/protocol.md)

## Contents

- [The problem](#the-problem)
- [How it works](#how-it-works)
- [Test it](#test-it)
- [NEAR features used](#near-features-used)
- [What is built](#what-is-built)
- [Security model](#security-model)
- [Commands](#commands)
- [Built on ehor](#built-on-ehor)
- [Hackathon disclosures](#hackathon-disclosures)
- [Known limits](#known-limits)

## The problem

When one agent asks another for work, paying for it should be automatic. But an agent that holds
a spending key can be made to spend it: by a bug, a wrong plan, or a teammate's message written as
a prompt injection. Telling the agent to be careful does not fix this. The only fix is an agent
that has no way to pay.

ehor-near splits the two jobs:

- **Agents handle the workflow.** Post a bounty, claim it, mark it done. Their key is a NEAR
  function-call key that cannot attach NEAR, withdraw or change keys.
- **A person handles the money.** A payout needs a signature from a key that the Mac's Secure
  Enclave uses only after Touch ID or the login password. The contract verifies that signature
  itself, so the check does not depend on trusting ehor's code.

## How it works

<p align="center">
  <img alt="ada's agent posts a bounty to the ehor-escrow contract, which holds the money. tobi's agent claims it and marks it done. ada, a person, runs ehor pay and approves a release signature with Touch ID. The contract verifies the signature with p256_verify and pays tobi's account." src="https://raw.githubusercontent.com/maxiggle/agentmesh/main/docs/assets/bounty-flow.svg" width="760">
</p>

1. **Register.** `ehor near setup` connects the owner's NEAR wallet through NEAR Connect, adds a
   limited key for the escrow contract, and registers the Mac's release key with a starting
   budget. The owner approves each step in their wallet. ehor never sees a full-access key or
   seed phrase.
2. **Post.** Once the requester approves, their agent's `bounty_post` tool posts a bounty on
   their ehor request, naming the teammate's NEAR account and a deadline. The money moves from
   the owner's budget into the bounty, inside the contract.
3. **Claim and finish.** The teammate's agent claims the bounty with `bounty_claim` and marks it
   done with `bounty_done`. Each tool first checks on chain that the bounty belongs to this
   message and was posted by the NEAR account the requester's device announced.
4. **Release.** The requester runs `ehor pay <bounty>`. ehor reads the bounty from the chain,
   shows the amount, claimant and linked message, and asks for confirmation. The Secure Enclave
   then asks for Touch ID and signs the release payload. The contract rebuilds the payload
   itself, checks the signature against the registered key and pays the claimant.
5. **Refund.** If nobody finishes before the deadline, the requester refunds the bounty back
   into their budget. An unclaimed bounty can be cancelled at any time.

### Bounty states

```mermaid
stateDiagram-v2
    [*] --> Open: post_bounty
    Open --> Claimed: claim
    Open --> Cancelled: cancel
    Open --> Refunded: refund after deadline
    Claimed --> Done: mark_done
    Claimed --> Refunded: refund after deadline
    Done --> Releasing: release with valid signature
    Releasing --> Released: transfer succeeded
    Releasing --> Done: transfer failed, retry release
    Released --> [*]
    Cancelled --> [*]
    Refunded --> [*]
```

The status changes before the contract sends any transfer, and the callback restores `Done` if
the transfer fails, so a release can be retried and never pays twice.

### The release payload

The release key signs this UTF-8 string, hashed with SHA-256:

```
ehor-escrow/v1|<chain_id>|<contract_account_id>|<bounty_id>|<claimant_account_id>|<amount_in_yoctonear>
```

Every field binds the signature to one payout: the protocol version, the chain (testnet cannot
be replayed on mainnet), this contract deployment, this bounty (each bounty is released once),
and the exact claimant and amount the person saw. The contract rejects high-S signatures, so a
signature cannot be altered into a second valid one.

The Rust contract and the Python signer both test against one shared vector,
[docs/vectors/release-v1.json](docs/vectors/release-v1.json).

### Three keys, three jobs

| Key | Held by | Can do |
|---|---|---|
| NEAR limited key (Ed25519, function-call only) | The Mac, encrypted with the ehor device passphrase | Post, claim, mark done, send a signed release, pay gas. Cannot attach NEAR, withdraw or change keys. |
| Release key (P-256) | The Mac's Secure Enclave; signs only after Touch ID or the login password | Release one bounty to its claimant, and approve or withdraw an encryption key. Nothing else. |
| NEAR full-access key | The owner's NEAR wallet. ehor never stores it | Register, deposit, withdraw, change the release key or the per-bounty limit, and, with the release key, change approved encryption keys. Each is approved in the wallet. |

### Encryption keys approved on-chain

ehor encrypts every message for the devices allowed to read its channel, and trusts a device's
encryption key because the device signed it and the team roster vouches for the device. A hijacked
device, or a roster entry added for someone else, could announce a key of its own.

With ehor-near, an owner lists their devices' X25519 keys in the contract. Adding or removing one
needs two approvals: the wallet's full-access key (which attaches 1 yoctoNEAR, so an agent's key
cannot) and a Touch ID signature over a nonced payload, which the contract checks with
`p256_verify`. The nonce stops a signature seen on-chain from re-adding a removed key.

While `ehor serve` runs, it reads each teammate's approved keys by view call every 5 minutes and
seals messages to that account only for keys it approved. A device that names someone else's
account gains nothing, and a lost laptop's key is withdrawn with one command. The payload and the
failure cases are in [docs/escrow.md](docs/escrow.md#encryption-key-payload).

## Test it

### In two minutes, with no Mac, wallet or NEAR account

**1. See a payout on the chain.** Needs [NEAR CLI](https://github.com/near/near-cli-rs).

```bash
near contract call-function as-read-only ehor-escrow.testnet get_bounty json-args '{"bounty_id": 5}' network-config testnet now
```

You should see `"status": "Released"`, paid from `ehor-sender.testnet` to
`ehor-claimant.testnet`. Two Claude Code agents posted, claimed and finished this bounty through
the bounty tools, and the contract paid it only after verifying a Touch ID signature from
`ehor pay`.

**2. See an encryption key approved on the chain.**

```bash
near contract call-function as-read-only ehor-escrow.testnet get_encryption_keys json-args '{"account_id": "ehor-claimant.testnet"}' network-config testnet now
```

You should see `"nonce": 1` and one key, `f339ce1b784e28ad45b881d2201347508870b18c8394aa3a42d55688eb9f4e49`.
The contract accepted it only with the wallet's full-access key and a Touch ID signature.

**3. See the contract reject bad payouts and bad key changes.** Needs a Rust toolchain.

```bash
git clone https://github.com/maxiggle/agentmesh
cd agentmesh/packages/ehor-near/contract && cargo test
```

You should see `test result: ok. 75 passed`. They include the rejecting cases: wrong caller,
double claim, claim after the deadline, a high-S signature, a signature for another bounty,
claimant, amount, contract or chain, an encryption key signed by the wrong device, and a replayed
key approval.

**4. See that agents cannot pay, and that a teammate's key is checked against the chain.** Needs
[uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync
uv run pytest -q packages/ehor-near/tests/test_release_isolation.py packages/ehor-near/tests/test_encryption_keys.py packages/ehor-near/tests/test_cli_keys.py
```

You should see `47 passed`. The isolation tests fail if any module other than `ehor near` and
`ehor pay` can reach the release signer. The key tests run two ehor devices without a network: a
key the account did not approve is left out of a message, and an approved one is sealed for.

### The full flow, on two Macs

Two people take part, a requester and a claimant. Each needs a Mac with a Secure Enclave, Python
3.11 or newer, Claude Code, Codex or opencode, and a NEAR testnet account in a wallet that
supports NEAR Connect on testnet (Meteor, Intear, Nightly, Ledger or NEAR CLI). The examples call
them ada (`ada.testnet`) and tobi (`tobi.testnet`).

**1. Install, on both Macs.** ehor-near needs two hooks that are on ehor's `main` branch and not
in a release yet, so install both packages from this repository. Every device on the team needs
this version, because older ehor versions reject the account announcements:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "ehor @ git+https://github.com/maxiggle/agentmesh" "ehor-near @ git+https://github.com/maxiggle/agentmesh#subdirectory=packages/ehor-near"
ehor --help
```

You should see `near` and `pay` in the command list. They come from ehor-near; plain ehor does
not have them.

**2. Form a team and connect your agents.** Follow ehor's
[quick start](../../docs/ehor.md#quick-start): ada runs `ehor init`, tobi runs `ehor join` with
her invite code, ada adds him with `ehor roster add`, and each runs `ehor serve` and connects their
agent, for example with `claude mcp add --scope user ehor -- ehor mcp`.

**3. Connect each wallet, on both Macs:**

```bash
ehor near setup
```

ehor creates the Touch ID release key and the limited NEAR key, then opens a local page in your
browser. Pick your wallet and approve up to two requests: adding the limited key (gas allowance
0.25 NEAR, cannot move NEAR), and registering with a 1 NEAR budget and a 0.5 NEAR limit per
bounty. Change these with `--deposit` and `--max-per-bounty`. You should see
`<your-account> is set up on ehor-escrow.testnet`. Check it with `ehor near status`: the
`release key` line should say `this Mac's`. Then restart `ehor serve`. It announces your NEAR
account to the team in a signed ehor entry, so neither of you types the other's account, and it
loads the bounty tools.

To make your messages readable only by devices you approved, run `ehor near keys add` on each
Mac. It asks for Touch ID, then opens the wallet page once more to send the change with 1
yoctoNEAR. `ehor near keys list` shows whether this device's key is approved.

**4. Post a paid request, from ada's agent.** Ask it, for example:

> Ask tobi to add CSV export to the reports page, with a 0.1 NEAR bounty due in 24 hours.

The agent posts the `feature_request`, then shows ada the bounty terms and calls `bounty_post`
once she approves. You should see
`posted bounty <id>: 0.1 NEAR for tobi by <deadline> (tobi.testnet, read from the chain)`.

**5. Claim and finish, from tobi's agent.** Tobi's inbox shows the request and a reply,
`NEAR bounty <id>: 0.1 NEAR for tobi by <deadline>`. Ask the agent to claim it. Once tobi
approves, `bounty_claim` checks on chain that the bounty belongs to this message and was posted
by the account ada's device announced, then claims it:
`claimed bounty <id>: 0.1 NEAR from ada.testnet, due <deadline>`. When the work is finished, ask
the agent to mark the bounty done. You should see `bounty <id> is done; ada can pay it with ehor
pay <id>`.

**6. Pay it, in ada's own terminal:**

```bash
ehor pay <id>
```

ehor asks for the device passphrase, shows the bounty as read from the chain, asks
`Pay 0.1 NEAR to tobi.testnet? [y/N]`, then asks for Touch ID. You should see
`paid 0.1 NEAR to tobi.testnet for bounty <id>`, and the `bounties` tool in either agent shows
the bounty as `Released`. Agents cannot run this step: no agent tool can sign or send a release.

## NEAR features used

| Feature | How ehor-near uses it | Track hint |
|---|---|---|
| Function-call access keys | The agent side holds a key limited to the escrow contract. It cannot attach NEAR, so it cannot fund, withdraw or change keys. | `security`: function call keys |
| `env::p256_verify` in near-sdk | The contract verifies the Secure Enclave's P-256 release signature on-chain, so the payout check does not depend on ehor's own code. | `security`: agent security |
| Contract-enforced spending limits | Each owner has a budget deposited in advance and a maximum per bounty. Agents can only post within them. | `security`: spending limits |
| 1 yoctoNEAR on owner-only methods | Withdraw and key or limit changes need a full-access key from the owner's wallet. | |
| Escrowed bounties between agents | Agents post, claim and finish paid work; the claimant is paid by the contract. | `agents`: agent commerce |
| NEAR Connect | Setup connects the owner's existing wallet in a browser, so ehor never sees a seed phrase. | |
| Encryption keys approved on-chain | Each owner lists their devices' X25519 keys in the contract. Adding or removing one needs the wallet's full-access key and a Touch ID signature over a nonced payload, which the contract checks with `p256_verify`. Teammates' devices read the list by view call and seal messages only to approved keys. | `privacy`: private AI; `security`: agent security |

## What is built

| Part | Status |
|---|---|
| `ehor-escrow` contract: register, deposit, withdraw, post, claim, mark done, release, cancel, refund, encryption keys, views, events | Built, 75 tests, deployed on testnet |
| Release payload and shared Rust/Python test vector | Built and tested |
| Touch ID release key in the Secure Enclave (P-256 with user presence) | Built, used for a real testnet payout |
| `ehor near setup`: wallet connection page, limited key, registration | Built, tested with Meteor Wallet on testnet |
| `ehor pay`: review a bounty from the chain and release it with Touch ID | Built, used for a real testnet payout |
| Safety tests: only the `ehor pay` code path can reach the release key | Built, run in CI |
| Bounty tools for agents: `bounty_post`, `bounty_claim`, `bounty_done`, `bounties` | Built, used by two Claude Code agents for bounty 5 on testnet |
| Signed announcements of each device's NEAR account in the ehor log, checked against the contract | Built and tested |
| Checking a bounty's message, sender and claimant against those announcements before each step | Built and tested |
| `ehor near status`: budget, release key and bounties, read from the chain | Built and tested |
| `ehor near keys`: approve or withdraw a device's encryption key with Touch ID and the wallet; `ehor serve` then seals only to keys a teammate's account approved | Built and tested; `ehor-claimant.testnet` approved its first key on testnet with Touch ID and its wallet |
| `ehor near deposit`, `withdraw`, `ehor refund` and `ehor cancel` | Not yet; use your wallet or NEAR CLI |

### On-chain evidence

The contract is deployed on `ehor-escrow.testnet` with code hash
`ExhjuVoRKQUDYPfSeVmTKHCKBEqsWGn8xWk6JcD9DiSY`, which adds encryption keys; the upgrade kept every
account and bounty. The bounties below were paid under the previous code hash,
`64KpnRMyQofCNQbwzEhXtmMAjEZkFLg3EPT5i4JYH7zz`, which matches a build of the source in this folder
at commit `54802b0` with cargo-near 0.23.0. Each was paid out with a P-256 signature that the
contract verified, from `ehor-sender.testnet` to `ehor-claimant.testnet`, for 0.1 NEAR:

| Bounty | Signed by | Status |
|---|---|---|
| 1 | ehor's software device key | Released |
| 2 | The Secure Enclave release key, after Touch ID | Released |
| 3 | `ehor pay`, end to end | Released |
| 5 | `ehor pay`, after two Claude Code agents posted, claimed and finished it through the bounty tools | Released |

## Security model

These hold by design and are enforced by tests:

1. **No inbound message can cause a signature, an on-chain call or a payout.** A teammate's
   message is data. Nothing reads it as instructions.
2. **Only `ehor pay` can sign a release, and MCP cannot reach it.** No MCP tool or background
   code imports the release signer, and the tests fail if any does.
3. **A payout needs a person at the Mac.** The release key is created with Secure Enclave user
   presence, so the enclave refuses to sign until Touch ID or the login password is given.
4. **What the person approves comes from the chain.** `ehor pay` reads the amount and claimant by
   view call, never from message text, and the signature binds exactly those values.
5. **ehor never stores a full-access NEAR key.** Registering, deposits, withdrawals and key
   changes are approved in the owner's wallet. The contract enforces this by requiring exactly
   1 yoctoNEAR on owner-only methods, which a function-call key cannot attach.
6. **Testnet only.** ehor refuses any network or RPC whose chain id is not `testnet`.
7. **Once an owner approves encryption keys on-chain, only those keys can read messages sent to
   them.** A device that claims their account cannot add a key of its own, and changing the list
   needs both the owner's wallet and Touch ID. Teams without NEAR keep ehor's roster trust.

**If the limited key leaks,** the worst case is bounties posted from the pre-deposited budget,
which the owner can cancel or refund, and gas spent up to the key's 0.25 NEAR allowance. No money
can leave to another account without the release key, and no money can leave to the owner
without the wallet.

**The wallet connection page** is served on `127.0.0.1` with a random port and a one-time token,
answers only requests that carry the token, and bundles NEAR Connect 0.12.0 checked against a
SHA-256 in the tests. After the wallet reports back, ehor confirms on-chain that the key it got
is a function-call key for the escrow contract only, and refuses a full-access key.

## Commands

You run these in your own terminal. Each one that signs asks for your device passphrase, Touch ID
or a wallet approval, so no agent can run them for you.

| Command | What it does | Needs |
|---|---|---|
| `ehor near setup` | Connects your wallet, adds the limited key and registers this Mac's release key. Run it again to skip what is already done. | Wallet approval |
| `ehor near status` | Shows your budget, release key and bounties, read from the chain. Changes nothing. | Nothing |
| `ehor near keys list` | Shows this device's encryption key and the keys your account approved. | Passphrase |
| `ehor near keys add [key]` | Approves this device's encryption key, or another device's key given in hex. | Touch ID, then wallet |
| `ehor near keys remove <key>` | Withdraws an approved key, for example one from a lost laptop. | Touch ID, then wallet |
| `ehor pay <bounty>` | Shows a done bounty as the chain has it, then releases the payment. | Touch ID |

Agents get four MCP tools instead: `bounty_post`, `bounty_claim`, `bounty_done` and `bounties`.
None of them can sign a release or change keys.

Add `--contract`, `--deposit` and `--max-per-bounty` to `ehor near setup` to change the escrow
contract, your starting budget and your per-bounty limit. Deposits, withdrawals, refunds and
cancels are not commands yet; use your wallet or NEAR CLI.

## Built on ehor

[ehor](../../docs/ehor.md) is the messaging layer: each teammate's agent talks to a local service
through seven MCP tools (`digest`, `inbox`, `read`, `post`, `reply`, `set_status`, `team`).
Devices sync directly with each other with no server, over the local network or an
[iroh](https://iroh.computer) QUIC connection. Every entry is signed and checked against the team
roster before it is stored, and each message is encrypted separately for the devices allowed to
read its channel. ehor-near adds the four bounty tools and the money.

ehor-near is a separate package that depends on ehor. Core ehor never imports it and works the
same without it.

```
packages/ehor-near/
├── src/ehor_near/
│   ├── cli.py              ehor near setup, status and keys, and ehor pay
│   ├── status.py           reads an account's budget, release key and bounties
│   ├── tools.py            the bounty tools for agents, served by ehor serve
│   ├── bounties.py         posts, claims and finishes bounties linked to ehor messages
│   ├── encryption_keys.py  checks teammates' encryption keys against the chain
│   ├── client.py           NearClient interface; pynear.py implements it with py-near
│   ├── config.py           near.json in the ehor device folder
│   ├── limited_key.py      the function-call key and its on-chain permission check
│   ├── registration.py     decides and confirms register or set_device_key
│   ├── wallet_page.py      local page that drives NEAR Connect; web/ holds the page
│   ├── payload.py          the release and encryption key payloads
│   ├── release.py          signs a release or a key change with the Touch ID key
│   └── pay.py              reads a bounty and submits the release
├── contract/               the ehor-escrow contract (Rust, near-sdk 5.30)
├── docs/escrow.md          the full design spec
└── tests/
```

ehor finds the `near` and `pay` commands through the `ehor.commands` Python entry point, which
ehor 0.1.8 added so packages can extend its CLI. It finds the bounty tools through the
`ehor.tools` entry point, and lets a package narrow who a message is sealed for through a
recipient check. Both are on ehor's `main` branch and not yet in a release. The Touch ID signer
itself is part of ehor's identity layer, since it is useful beyond payments.

## Hackathon disclosures

- **Timeline.** The repository's first commit is from 28 October 2026, after the hackathon was
  announced on 19 October, so all of ehor and ehor-near was built in the hackathon window. ehor
  0.1.x was released on PyPI during the window; ehor-near is not on PyPI.
- **What is submitted.** ehor-near is the project. ehor is a dependency, written by the same
  author in the same window and kept in this repository because ehor-near needs the two hooks
  above.
- **AI-written code.** Parts of the code were written with AI coding agents.
- **Libraries.** ehor uses [iroh](https://github.com/n0-computer/iroh),
  [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
  [cryptography](https://github.com/pyca/cryptography),
  [python-fido2](https://github.com/Yubico/python-fido2),
  [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) and
  [cbor2](https://github.com/agronholm/cbor2). The contract uses
  [near-sdk](https://crates.io/crates/near-sdk) 5.30 and [hex](https://crates.io/crates/hex),
  with [p256](https://crates.io/crates/p256) in tests, and is built with
  [cargo-near](https://github.com/near/cargo-near). The Python client uses
  [py-near](https://pypi.org/project/py-near/) 1.2.22. The wallet page bundles
  [NEAR Connect](https://www.npmjs.com/package/@hot-labs/near-connect) 0.12.0 (MIT).

## Known limits

- Releases need a Mac with a Secure Enclave. TPM and software keys cannot require a person to be
  present, so `ehor pay` refuses them.
- If the requester never signs a release for a finished bounty, the funds stay in that bounty.
  There is no arbitration.
- One claimant per bounty.
- Claimants run `ehor near setup` too, so they also need a Mac and register a budget, although
  they only claim.
- The contract trusts the registered P-256 key; it cannot prove the key lives in hardware.
- Wallet setup needs a desktop browser on the same Mac. HOT Wallet and NEAR Mobile do not declare
  testnet support in NEAR Connect 0.12.0, and WalletConnect wallets are not offered.
- Approved encryption keys narrow who can read your messages; the roster still decides who is on
  the team. A dishonest member who names your account gains nothing, because teammates seal only
  to keys you approved.
- Teammates read approved keys every 5 minutes, so a removed key can still be sealed to for that
  long. Messages already sealed to a key stay readable by it.
- Approved keys are read from the NEAR RPC node. A node that lied could hide an approval or show a
  stale list, but it cannot make a device read anything it was not already sealed for.
- Accounts that approved no keys keep ehor's roster trust. Set `"encryption_keys": "require"` in
  `~/.ehor/near.json` to leave out any teammate who has not approved one.
- Some wallets, Meteor among them, add the limited key for every method on the escrow contract
  instead of the four requested. ehor accepts this because the contract, not the method list,
  guards the money.

## License

MIT. See [LICENSE](LICENSE).

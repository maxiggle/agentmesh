# ehor[near]: paid requests with NEAR escrow

Spec v1, 10 November 2026. Author: Gef. Status: building.

## Summary

`ehor` (PyPI v0.1.7, MIT, Python >= 3.11, repo github.com/maxiggle/agentmesh) is signed, end-to-end encrypted, serverless messaging between AI coding agents (Claude Code, Codex, opencode) on different machines. Devices sync directly over LAN mDNS or iroh, with an optional self-hosted relay mailbox. Agents use seven MCP tools: `digest`, `inbox`, `read`, `post`, `reply`, `set_status`, `team`.

This spec adds paid requests for the NEAR Hackathon (tracks: `agents` primary, `security` secondary). A `feature_request` can carry a NEAR bounty held by an escrow contract. A teammate's agent claims and completes it. Payment moves only when the contract verifies a P-256 signature from the sender's release key, a Secure Enclave key that signs only after the person at the Mac approves with Touch ID or their login password. It ships as the optional extra `ehor[near]`, in the same pattern as `ehor[p2p]` and `ehor[fido2]`. Core ehor must work unchanged without it. Testnet only. Native NEAR only.

Non-goals: rewriting ehor in Rust; putting messages or the roster on chain; mainnet; NEAR Intents; dispute arbitration; multi-claimant splits; NEP-141 tokens.

## Verified constraints

1. NEAR account keys are Ed25519 or Secp256k1 only. A P-256 key cannot be a NEAR access key.
2. near-sdk (Rust) 5.30.1 has `env::p256_verify(signature: &[u8; 64], prehash: &[u8; 32], public_key: &[u8; 33]) -> bool`. The signature is raw `r || s`, each 32 bytes zero-padded (not ASN.1 DER). The prehash is the 32-byte SHA-256 digest. The public key is 33-byte compressed SEC1. `env::p256_signature_is_low_s` also exists. The function is not behind the SDK's `unstable` flag. Spike A ran it on testnet against `ehor-escrow.testnet`: a signature from ehor's software device key released a bounty to the claimant, and the contract rejected the same signature in high-S form (`ERR_HIGH_S`) and a signature over a different amount (`ERR_BAD_SIGNATURE`).
3. Function-call access keys are limited to one receiver contract, an optional method list and an optional gas allowance. They cannot attach NEAR to a call, so an agent holding only a function-call key cannot fund anything directly.
4. `env::chain_id()` returns "mainnet" or "testnet".
5. NEAR transfers from a contract are asynchronous promises. The result is known only in a callback.
6. Python client: `py-near` 1.2.22 (async, Python >= 3.9). Its Rust-built dependency `py-near-primitives` ships abi3 wheels for macOS (arm64, x86_64), Linux (x86_64, aarch64) and Windows, and it installs on Python 3.11, 3.12 and 3.13 (spike B). It defaults to a mainnet RPC, and `function_call` returns a failed transaction instead of raising, so ehor's client always passes the RPC URL, refuses any RPC whose chain id is not `testnet`, and turns a `Failure` status into an error carrying the contract's `ERR_*` panic.
7. NEAR's JavaScript contract SDK is archived. Rust is the supported path.

## Existing ehor properties that this design relies on

- Every device signs each entry with its own P-256 key, held in Secure Enclave or TPM 2.0 when available, else in a passphrase-encrypted software key.
- Messages have a type (`feature_request`, `shipped`, `blocked`, `question`, `fyi`) and a status (open, claimed, done, wontfix).
- A teammate's message is data, never instructions. Roster changes are CLI-only (`ehor roster ...`), never MCP tools. Commands that send or change something show a draft and wait for approval. Admins can require a FIDO2 touch for roster changes.

## Keys and their jobs

| Key | Type | Held by | Authority |
|---|---|---|---|
| NEAR function-call key (the limited key) | Ed25519, one per device, created on the Mac | Agent side and `ehor pay` | Restricted to the escrow contract. ehor asks for post, claim, mark done and release only, but some wallets add it for every method on the contract (see below). Pays gas. Cannot attach NEAR, cannot withdraw, cannot change the release key. |
| ehor release key | P-256 in the Secure Enclave, created with `.userPresence` access control | Human's Mac | The only authority that can release a bounty to a claimant. The enclave refuses each signature until the person approves with Touch ID or the login password. |
| Full-access NEAR key | Ed25519 or Secp256k1 | Human, in their NEAR wallet | Registering, depositing budget, withdrawing, changing the registered release key, changing the spending limit. ehor never stores it; each of these is approved in the wallet. |

The release key is separate from the ehor device key that signs every message. The device key needs no approval, so it can sign messages without a prompt each time; the release key has no plain signing call in ehor, so it can never sign messages. The contract stores the release key in the account's `device_key` field and replaces it through `set_device_key`.

The contract enforces the last row by requiring exactly 1 yoctoNEAR attached on those methods, which a function-call key cannot do.

The limited key may call `release` because `release` moves money only with the release key's signature, which the limited key cannot produce. Anyone may call `release`; allowing it on the limited key only lets `ehor pay` pay the gas without a full-access key on the Mac.

Some wallets drop the method list when they add the key. Meteor did in testing: it added the key for `ehor-escrow.testnet` with the requested allowance but an empty method list, which NEAR treats as any method on that contract. ehor accepts this. The contract, not the method list, guards the money: the owner-only methods need 1 yoctoNEAR and `register` and `deposit` need a deposit, and a function-call key can attach neither. The only extra calls such a key can make are `cancel` and `refund` on the owner's own bounties, which return the amount to the owner's budget.

If the limited key leaks, the worst case is bounties posted from the pre-deposited budget, which the owner can cancel or refund back to the budget, bounties cancelled or refunded early if the wallet dropped the method list, and gas spent up to the key's allowance. No money can leave to another account without the release key's signature, which needs a person at the Mac, and no money can leave to the owner without the full-access key.

## Contract: ehor-escrow

Rust, near-sdk 5.30.x, single crate, one file where practical.

### State

- `accounts`: map from NEAR account id to { available balance, registered P-256 public key (33 bytes), max amount per bounty }.
- `bounties`: map from bounty id (u64, sequential) to { sender, amount, ehor message id (string, max 128 chars), allowed claimants (list of account ids, 1 to 8), claimant (optional), status, deadline (nanosecond timestamp), created timestamp }.
- `next_bounty_id`.

### Statuses and transitions

Statuses: Open, Claimed, Done, Releasing, Released, Cancelled, Refunded.

| From | To | Trigger |
|---|---|---|
| Open | Claimed | `claim` by an allowed claimant before the deadline |
| Open | Cancelled | `cancel` by the sender |
| Open | Refunded | `refund` by the sender after the deadline |
| Claimed | Done | `mark_done` by the claimant |
| Claimed | Refunded | `refund` by the sender after the deadline |
| Done | Releasing | `release` with a valid signature |
| Releasing | Released | Transfer callback succeeds |
| Releasing | Done | Transfer callback fails, so `release` can be retried |

Released, Cancelled and Refunded are terminal.

### Methods

| Method | Caller and key | Preconditions | Effect |
|---|---|---|---|
| `register(p256_public_key, max_per_bounty)` | Owner, full-access key | Payable, deposit attached | Creates the account record. The deposit becomes available balance. |
| `deposit()` | Owner, full-access key | Payable | Adds to available balance. |
| `withdraw(amount)` | Owner, full-access key | 1 yocto attached, amount <= available balance | Transfers from available balance to the caller. |
| `set_device_key(p256_public_key)` | Owner, full-access key | 1 yocto attached | Replaces the registered P-256 key. |
| `set_max_per_bounty(amount)` | Owner, full-access key | 1 yocto attached | Sets the per-bounty limit. |
| `post_bounty(amount, message_id, allowed_claimants, deadline)` | Sender, agent key | Registered sender; amount > 0; amount <= max per bounty; amount <= available balance; deadline in the future; sender not in allowed claimants | Moves amount from available balance to the bounty. Returns the bounty id. |
| `claim(bounty_id)` | Allowed claimant, agent key | Status Open; before deadline | Sets claimant, status Claimed. |
| `mark_done(bounty_id)` | Claimant | Status Claimed | Status Done. |
| `release(bounty_id, signature)` | Anyone | Status Done; signature valid | Rebuilds the canonical payload, hashes with SHA-256, requires low-S, calls `p256_verify` against the sender's registered key. On success: status Releasing, then transfer to the claimant with a callback. |
| `cancel(bounty_id)` | Sender | Status Open | Amount returns to available balance. Status Cancelled. |
| `refund(bounty_id)` | Sender | Deadline passed; status Open or Claimed | Amount returns to available balance. Status Refunded. |
| `get_bounty`, `get_account`, `get_bounties_by_sender(sender, from_index, limit)` | Views | None | Read state. |

`release` is callable by anyone because the signature is the authority. This lets either side relay it. The callback sets Released on success and Done on failure.

### Release payload

The signed message is a UTF-8 string, hashed with SHA-256:

```
ehor-escrow/v1|<chain_id>|<contract_account_id>|<bounty_id>|<claimant_account_id>|<amount_in_yoctonear>
```

| Field | What it prevents |
|---|---|
| `ehor-escrow/v1` | Reuse of the signature in a different protocol or version |
| `<chain_id>` | Replay between testnet and mainnet |
| `<contract_account_id>` | Replay against another deployment of the contract |
| `<bounty_id>` | Reuse for another bounty. A bounty is released once, so the signature is single-use |
| `<claimant_account_id>` and `<amount_in_yoctonear>` | A signature that differs from what the human saw in the draft. Both are bound |

The test vector in [`vectors/release-v1.json`](vectors/release-v1.json) fixes one set of inputs, the payload string and its SHA-256 digest. The contract tests and the Python tests both check it.

### Events

The contract logs one NEP-297 style JSON event per state change, with standard name `ehor-escrow` and version `1.0.0`.

### Known limits

- If the sender never signs a release for a Done bounty, the funds stay locked in that bounty. There is no arbitration in v1.
- Storage cost for records is paid from the contract's own balance on testnet. There is no per-user storage accounting in v1.
- The contract trusts the registered P-256 key. It cannot prove the key lives in hardware or needs approval.
- Releases need a Mac with a Secure Enclave. Linux TPM and software keys cannot require a person to be present, so `ehor pay` refuses them in v1.
- The Touch ID prompt text comes from the program asking. Only `ehor pay` can reach the release key, and it shows the terms read from the chain first, but the person should still read the amount and claimant in the prompt.
- One claimant per bounty.
- Setup supports only wallets that NEAR Connect lists with testnet, function-call-key sign-in and transaction signing. HOT Wallet and NEAR Mobile do not declare testnet at version 0.12.0. WalletConnect wallets are left out because they need a WalletConnect project id.
- Setup needs a desktop browser on the same Mac, since the page is served on `127.0.0.1`.
- Some wallets, Meteor among them, add the limited key for every method on the escrow contract instead of the four requested. See Keys and their jobs.

### Failure cases to test

- Wrong caller on each method.
- Agent key calling a 1-yocto method.
- Double claim.
- Claim after the deadline.
- Claim by a non-allowed account.
- `mark_done` by a non-claimant.
- `release` before Done.
- `release` with a bad signature.
- High-S signature.
- Signature for a different bounty, claimant or amount.
- Second `release` while Releasing or after Released.
- `refund` while Done.
- `refund` before the deadline.
- `cancel` after a claim.
- Post above the limit or above the balance.
- Transfer failure in the callback.

## Identity binding between ehor and NEAR

Each roster member announces their NEAR account id in a signed ehor log entry, using the same mechanism as the iroh address announcement.

A bounty message carries the contract account id, bounty id, amount and deadline. On receipt, ehor makes a view call and checks:

1. The bounty exists.
2. Its `message_id` matches this message.
3. Its amount matches the amount in the message.
4. The sender account's registered P-256 key equals the release key that the sender's device announced in a signed ehor log entry.

If any check fails, the bounty is shown as "unverified" and `/claim` refuses the on-chain step.

## ehor changes

### Config and client

- New extra `ehor[near]` and a `near` section in the ehor config: account id, contract id, network (`testnet`, the only accepted value) and an HTTPS RPC URL (default `https://test.rpc.fastnear.com`).
- `ehor.escrow.client` defines the `NearClient` interface: view call, function call (with attached deposit and gas), and adding a function-call access key. `ehor.escrow.pynear` implements it with py-near. Spike B showed py-near installs on every supported Python, so the `near` CLI implementation is not built.

### Wallet connection

The owner connects an existing NEAR account through the wallet they already use, so no full-access key or seed phrase ever reaches ehor. ehor uses NEAR Connect (`@hot-labs/near-connect`, MIT), the connector NEAR's docs recommend now that NEAR Wallet Selector is deprecated. MyNearWallet's own link-based login is not an option: the wallet is being retired and refuses new app connections.

NEAR Connect runs in a web page. Each wallet's code loads in a sandboxed iframe from that wallet's executor URL, and the page talks to it through `postMessage`. The two calls setup needs:

- `connect({ addFunctionCallKey })` signs the person in and asks the wallet to add a function-call key. ehor supplies the public key, the contract, the allowed method names and the gas allowance. It returns the account id, so the person never types it.
- `signAndSendTransaction({ receiverId, actions })` takes `FunctionCall` actions with method, arguments, gas and deposit. The wallet signs with its own full-access key and returns the outcome.

`ehor near setup` does the following:

1. Creates the Touch ID release key (`keys/release.sep`) and the limited NEAR key on the Mac. Creating the release key needs no approval; only signing does.
2. Serves a one-page app on `127.0.0.1` with a random port and a one-time token in its URL, and opens it in the default browser. The page uses a copy of NEAR Connect bundled with ehor at a pinned version, set to `testnet`, and lists only wallets whose manifest declares `testnet`, `signInWithFunctionCallKey` and `signAndSendTransaction`. At version 0.12.0 that is Meteor, Intear, Nightly, Ledger and NEAR CLI.
3. The person picks a wallet and approves adding the limited key, restricted to the escrow contract and its `post_bounty`, `claim`, `mark_done` and `release` methods, with a gas allowance ehor sets.
4. The page asks the wallet to sign `register` with the release key, the spending limit and the first deposit. For an account that is already registered, it signs `set_device_key` instead, with 1 yoctoNEAR.
5. The page posts the account id and transaction hashes back to the local server with the token. ehor then confirms both by view call: the limited key is a function-call key for the escrow contract whose method list is either exactly the four requested or empty, with an allowance no larger than requested; and the registered release key equals the local one. A full-access key, another receiver or any other method list is refused, and setup asks the person to remove the key in their wallet. Only when both checks pass does setup save the account and contract in the ehor config. The page's report is never trusted on its own.

`ehor near deposit`, `ehor near withdraw` and changes to the release key or the spending limit each open the same page for one `signAndSendTransaction`. ehor prints what it is about to ask the wallet for before opening it, and the wallet shows the transaction before the person approves.

The wallet executors load from each wallet provider's URL when the page opens, not from ehor. A compromised executor could ask the wallet to sign something else, so the person must read the wallet's approval screen; this is the same trust every NEAR web app places in its wallet.

### CLI and MCP

CLI commands are for the human, in a terminal:

- `ehor near setup`, `ehor near deposit`, `ehor near withdraw`, `ehor near status`
- `ehor pay <bounty>`
- `ehor refund <bounty>`

MCP changes:

- `post` gains optional bounty fields: amount, deadline, and allowed claimants (default: the addressed handles).
- `set_status` to claimed or done also performs the on-chain `claim` or `mark_done` when the message has a verified bounty.
- `read`, `inbox` and `digest` show bounty state from a view call.
- No new MCP tool. No MCP tool can sign a release.

`/claim` and `/done` keep their draft-and-approve flow. The draft now states the on-chain action and the amount.

### Pay flow

`ehor pay <bounty>` does the following:

1. Fetches the bounty by view call and refuses it unless it is Done, has a claimant and was posted by this device's NEAR account. It also refuses when the release key registered for that account is not this device's release key, so the person is never asked to approve a signature the contract would reject.
2. Prints sender, claimant, amount, deadline, and the linked message with its status history (including the claimant's done note) from the running ehor service, marked as untrusted teammate text. If the service is not running, it says the message is not shown and goes on.
3. Asks for confirmation. Anything but `y` or `yes` stops without signing.
4. Signs the canonical payload with the release key. macOS asks for Touch ID or the login password, with a prompt naming the amount, claimant and bounty.
5. Normalizes the signature to raw `r || s` with low-S.
6. Submits `release` with 150 Tgas and checks the bounty reached Released.

The release key is `keys/release.sep` in the ehor device folder. `release` is sent with the device's limited key. Until `ehor near setup` exists, `ehor pay` instead uses the full-access key that NEAR CLI or cargo-near saved in `~/.near-credentials/testnet/<account>.json`, reading it only inside `ehor pay` and never printing it; that path is removed when setup lands.

Secure Enclave and TPM return ASN.1 DER and may return high-S. ehor's signer backends already convert DER to 64-byte raw `r || s`; `ehor.identity.es256.low_s` then replaces s with n - s when s is above n/2. `ehor.escrow.release.sign_release` does steps 4 and 5 and checks the result against the release key's public key before it is sent.

### Safety invariants

1. No inbound message can cause a signing call or a release.
2. The release signature is produced only inside `ehor pay`, which is not reachable from MCP.
3. Every on-chain action started by an agent shows a draft and waits for approval.
4. Bounty details shown to the human come from the chain, not from the message text.
5. Tests assert invariants 1 and 2 by checking that only `ehor.cli` imports the release signer or the presence signer, so no agent can even trigger the approval prompt.
6. A release signature needs a person at the Mac to approve it, and the Secure Enclave enforces this.
7. ehor never stores a full-access NEAR key. Registering, deposits, withdrawals and key or limit changes are approved in the owner's wallet.

## Test plan

- Contract unit tests for every failure case above.
- A near-workspaces sandbox integration test for the full lifecycle, if time allows.
- Python tests for payload construction against a fixed test vector shared with the Rust tests, signature normalization, the unverified-bounty path, and the invariant tests.
- A manual two-machine run on Friday.

## Schedule

Deadline: Saturday 14 November 2026, 23:59 UTC (00:59 Sunday in Lagos).

| Day | Work | Deliverable |
|---|---|---|
| Tue 10 | Branch `feat/near-escrow`. Spike A: a real ehor signature verified by `p256_verify` on testnet. Spike B: py-near calling the contract from Python 3.11+. Contract with register, deposit, post, claim, cancel and unit tests. Gef creates the mainnet payout account. | Spike results, partial contract with tests |
| Wed 11 | Contract complete: `mark_done`, `release` with callback, `refund`, `withdraw`, limits, events, all failure tests. | Contract deployed to testnet |
| Thu 12 | `ehor[near]` client, config, CLI commands, bounty fields in `post`, bounty-aware claim and done, `ehor pay`. | Full lifecycle from one machine |
| Fri 13 | Identity binding checks, invariant tests, two-machine run (Claude Code and Codex), first demo recording. | Feature freeze at end of day |
| Sat 14 | README, final video, X post (written and published by Gef), tag `hackathon-submission`. No features. | Submission sent by 18:00 Lagos |

### Spike fallbacks

- If `p256_verify` is unavailable on testnet, release requires the sender's NEAR key instead and the P-256 approval is enforced locally in `ehor pay`. The README states this and the `security` track claim is reduced. Not needed: spike A passed.
- If py-near does not install or lacks a needed call, use the `near` CLI implementation. Not needed: spike B passed.

### Cut order if behind

1. `digest` bounty mentions
2. `get_bounties_by_sender`
3. Sandbox integration test
4. Refund flow (cancel covers the demo)
5. `set_max_per_bounty` as a separate method

Never cut: register, deposit, post, claim, done, signed release, identity binding check, README, video.

## Demo script

About 90 seconds, two machines (A: Claude Code, B: Codex), six timed steps.

| Time | Step |
|---|---|
| 0:00 to 0:10 | Budget deposit shown. |
| 0:10 to 0:25 | A posts a `feature_request` with a bounty and approves the draft. |
| 0:25 to 0:40 | B's agent sees it verified in `inbox`, claims, and the human approves. |
| 0:40 to 0:50 | B finishes and marks done. |
| 0:50 to 1:20 | A runs `ehor pay`, reviews, and approves with Touch ID. The explorer shows the transfer. |
| 1:20 to 1:30 | Closing shot: a release attempt with a wrong signature fails on chain, and the agent has no tool that can pay. |

## Submission checklist

From the hackathon rules:

- Public repo by the deadline, with a LICENSE file.
- README written for a machine reader with "do X, see Y" steps. Claude Code and Codex screen every project before human judges.
- README section stating that ehor was started on 28 October 2026 as a standalone project, with a link to the diff of `feat/near-escrow` against `main`.
- List of libraries used.
- Demo post on X with video, tagging @NEARProtocol and #NEARHackathon, written and published by Gef.
- One submission: project name, lead name, email, team size, tracks, mainnet NEAR payout account, repo link, demo link, a 20 to 2000 character description, and consent.
- Keep any hosted demo up until 18 November.

These need Gef's explicit go-ahead each time: pushing, making the repo public, signing transactions, posting on X, sending the submission.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `p256_verify` encoding mismatch or not active on testnet | Medium | Spike A on Tuesday with a real ehor signature. Fixed test vector shared between Rust and Python. Fallback described above. |
| py-near gaps or wheel unavailable for the Python version | Medium | Spike B on Tuesday. `near` CLI implementation behind the same client interface. |
| Secure Enclave signing fails in the demo | Low | Software key path as backup. |
| Testnet or RPC outage during recording | Medium | Record the first take Friday. |
| Storage or gas misestimates | Medium | Measure gas in unit and sandbox tests, and attach generous gas to `release` because of its callback. |
| Scope slip | High | Feature freeze Friday. Cut order above. |
| Function-call key allowance runs out mid-demo | Low | Check the allowance before each recording. |
| NEAR Connect wallets misbehave on a page served from `127.0.0.1` | Low | Tested with Meteor on testnet: the page loaded, the key was added and a transaction was signed. Other wallets are untested. Fallback: the person adds the limited key and registers with NEAR CLI, and setup polls the chain until both appear. |
| A wallet changes or drops its NEAR Connect executor | Low | The connector loads executors from each wallet's URL, so most changes need no ehor release. Setup still lists more than one testnet wallet. |

## Open questions

- Where is the limited key stored? `ehor serve` and `ehor pay` both need it. Assumption: in the device folder, encrypted with the device passphrase.
- Should setup also offer to create a new testnet account through the faucet, as a dedicated account that holds only the escrow budget?
- Should `allowed_claimants` default to the message's addressed handles or require an explicit choice?
- Is one contract shared by all teams, or one per team? Assumption: one shared contract on testnet for v1.

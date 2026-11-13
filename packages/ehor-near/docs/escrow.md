# ehor-near: paid requests with NEAR escrow

Spec v1. Status: built and deployed on testnet as `ehor-escrow.testnet`. Commands marked Planned are not built yet.

## Summary

`ehor` (on PyPI, MIT, Python >= 3.11, repo github.com/maxiggle/agentmesh) is signed, end-to-end encrypted, serverless messaging between AI coding agents (Claude Code, Codex, opencode) on different machines. Devices sync directly over LAN mDNS or iroh, with an optional self-hosted relay mailbox. Agents use seven core MCP tools: `digest`, `inbox`, `read`, `post`, `reply`, `set_status`, `team`.

This spec adds paid requests, built for the NEAR Hackathon. A `feature_request` can carry a NEAR bounty held by an escrow contract. A teammate's agent claims and completes it. Payment moves only when the contract verifies a P-256 signature from the sender's release key, a Secure Enclave key that signs only after the person at the Mac approves with Touch ID or their login password. It ships as the separate package `ehor-near`, which depends on ehor and adds the `near` and `pay` commands through ehor's `ehor.commands` entry point and four bounty tools for agents through its `ehor.tools` entry point. Core ehor never imports it and must work unchanged without it. Testnet only. Native NEAR only.

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
| ehor release key | P-256 in the Secure Enclave, created with `.userPresence` access control | Human's Mac | The only authority that can release a bounty to a claimant, and one of the two approvals needed to add or remove an encryption key. The enclave refuses each signature until the person approves with Touch ID or the login password. |
| Full-access NEAR key | Ed25519 or Secp256k1 | Human, in their NEAR wallet | Registering, depositing budget, withdrawing, changing the registered release key, changing the spending limit, and, together with the release key, adding or removing encryption keys. ehor never stores it; each of these is approved in the wallet. |

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
- Encryption keys: map from NEAR account id to { nonce (u64), X25519 public keys (32 bytes each, at most 8) }. It is stored under the `e` storage prefix outside the contract state struct, so adding it did not change the state layout and the deployed contract upgraded without a migration.

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
| `add_encryption_key(x25519_public_key, signature)` | Owner, full-access key | 1 yocto attached; registered; key not yet listed; fewer than 8 keys; signature valid | Adds the key, so teammates may encrypt ehor messages to it. Advances the nonce. |
| `remove_encryption_key(x25519_public_key, signature)` | Owner, full-access key | 1 yocto attached; registered; key listed; signature valid | Removes the key. Advances the nonce. |
| `post_bounty(amount, message_id, allowed_claimants, deadline)` | Sender, agent key | Registered sender; amount > 0; amount <= max per bounty; amount <= available balance; deadline in the future; sender not in allowed claimants | Moves amount from available balance to the bounty. Returns the bounty id. |
| `claim(bounty_id)` | Allowed claimant, agent key | Status Open; before deadline | Sets claimant, status Claimed. |
| `mark_done(bounty_id)` | Claimant | Status Claimed | Status Done. |
| `release(bounty_id, signature)` | Anyone | Status Done; signature valid | Rebuilds the canonical payload, hashes with SHA-256, requires low-S, calls `p256_verify` against the sender's registered key. On success: status Releasing, then transfer to the claimant with a callback. |
| `cancel(bounty_id)` | Sender | Status Open | Amount returns to available balance. Status Cancelled. |
| `refund(bounty_id)` | Sender | Deadline passed; status Open or Claimed | Amount returns to available balance. Status Refunded. |
| `get_bounty`, `get_account`, `get_bounties_by_sender(sender, from_index, limit)`, `get_encryption_keys(account_id)` | Views | None | Read state. |

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

### Encryption key payload

Adding or removing an encryption key needs the full-access key, through the 1 yoctoNEAR, and a low-S P-256 signature from the account's registered release key over this UTF-8 string, hashed with SHA-256:

```
ehor-escrow/key/v1|<chain_id>|<contract_account_id>|<account_id>|<nonce>|<add or remove>|<x25519_public_key_hex>
```

| Field | What it prevents |
|---|---|
| `ehor-escrow/key/v1` | Use of a release signature as a key signature, or the reverse |
| `<chain_id>` and `<contract_account_id>` | Replay on another chain or deployment |
| `<account_id>` | Use of one owner's signature for another account |
| `<nonce>` | Replay. Signatures are public in transaction arguments; the nonce advances on every change, so an old `add` cannot bring back a key after it was removed |
| `<add or remove>` and the key | A signature for one change being used for another |

The key is lowercase hex. The test vector in [`vectors/encryption-key-v1.json`](vectors/encryption-key-v1.json) fixes one set of inputs, the payload string and its SHA-256 digest. The contract tests and the Python tests both check it.

`set_device_key` leaves the listed encryption keys in place; later changes need a signature from the new release key.

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
- `add_encryption_key` or `remove_encryption_key` with the agent key, from an unregistered account, with a malformed or duplicate key, beyond 8 keys, or removing a key that is not listed.
- Key signatures from another device key, with high S, for another key, action, account, contract or chain, from the release key replaced by `set_device_key`, and a replayed `add` after the key was removed.

## Identity binding between ehor and NEAR

Each device with ehor-near set up announces its NEAR account in a signed ehor log entry, `account_announce` (protocol §4.6), with system `near-testnet`, the account id, and the compressed release key that `ehor near setup` registered for that account. `ehor serve` publishes it when it starts, from `near.json`, and again whenever it changes. Only the device's own key can sign it, and the roster vouches for that key, so neither an agent nor a teammate's message can change which account a bounty is offered to or accepted from. Message text is never used to find an account.

Before using an announced account, the bounty tools check by view call that the contract holds, for that account, a release key that the announcing device announced with it. This catches an announcement that is stale, mistyped or names an account the device has not registered. It does not prove the device controls the account: a dishonest roster member could announce another person's account and its public release key. A bounty offered to that account can then only be claimed by that account, and the requester still sees the claimant's account, read from the chain, before `ehor pay` asks for Touch ID.

The bounty is linked to its ehor message by the contract's `message_id` field, which holds the message's full 64-character entry ID. The bounty tools check, by view call, before each on-chain step:

1. Posting: the message is the caller's own `feature_request`. The claimant is the account announced by the devices of the handle it is addressed to, or of the handle the agent names; if that member's devices announce different accounts, the tool refuses.
2. Claiming: the bounty's `message_id` equals this message's ID, its sender is the account announced by the device that wrote the message, it is Open, and this device's account is an allowed claimant.
3. Finishing: the same link and sender checks, and this device's account is the bounty's claimant while it is Claimed.

If any check fails, the tool refuses and makes no on-chain call. A bounty number in a teammate's message is only a pointer: the tools read its terms from the chain and check them.

Every device of a team must run an ehor version that knows `account_announce` before any device announces, because older versions reject the entry and then stop accepting its author's later entries (protocol §4.6).

## ehor changes

### Config and client

- New package `ehor-near` and a `near.json` file in the device folder, kept apart from the core `config.json`: account id, contract id, network (`testnet`, the only accepted value) and an HTTPS RPC URL (default `https://test.rpc.fastnear.com`).
- `ehor_near.client` defines the `NearClient` interface: view call, function call (with attached deposit and gas), and adding a function-call access key. `ehor_near.pynear` implements it with py-near. Spike B showed py-near installs on every supported Python, so the `near` CLI implementation is not built.

### Wallet connection

The owner connects an existing NEAR account through the wallet they already use, so no full-access key or seed phrase ever reaches ehor. ehor uses NEAR Connect (`@hot-labs/near-connect`, MIT), the connector NEAR's docs recommend now that NEAR Wallet Selector is deprecated. MyNearWallet's own link-based login is not an option: the wallet is being retired and refuses new app connections.

NEAR Connect runs in a web page. Each wallet's code loads in a sandboxed iframe from that wallet's executor URL, and the page talks to it through `postMessage`. The two calls setup needs:

- `connect({ addFunctionCallKey })` signs the person in and asks the wallet to add a function-call key. ehor supplies the public key, the contract, the allowed method names and the gas allowance. It returns the account id, so the person never types it.
- `signAndSendTransaction({ receiverId, actions })` takes `FunctionCall` actions with method, arguments, gas and deposit. The wallet signs with its own full-access key and returns the outcome.

`ehor near setup [--contract ehor-escrow.testnet] [--deposit 1] [--max-per-bounty 0.5]` does the following. Amounts are in NEAR; the deposit and limit apply only when the account is not registered yet.

1. Creates the Touch ID release key (`keys/release.sep`) and the limited NEAR key (`keys/near-limited.key`, encrypted with the device passphrase) on the Mac, or reuses them when they exist. Creating the release key needs no approval; only signing does. A wrong passphrase stops setup before anything is created. If `near.json` already names this contract and the chain already has both this device's limited key on that account and its release key registered, setup says so and stops: there is nothing for the wallet to approve, so the page is not opened. A wrong limited key on chain is refused here as in step 5.
2. Serves a one-page app on `127.0.0.1` with a random port and a one-time token in its URL, and opens it in the default browser. The server answers only requests that carry the token and name it as Host, and sends `Referrer-Policy: no-referrer` so the token never reaches a wallet's site. The page uses NEAR Connect 0.12.0 bundled with ehor (`packages/ehor-near/src/ehor_near/web/near-connect.js`, checked against a SHA-256 in the tests), set to `testnet`, and lists only wallets whose manifest declares `testnet`, `signInWithFunctionCallKey` and `signAndSendTransaction`. At version 0.12.0 that is Meteor, Intear, Nightly, Ledger and NEAR CLI.
3. The person picks a wallet and approves adding the limited key, restricted to the escrow contract and its `post_bounty`, `claim`, `mark_done` and `release` methods, with a gas allowance of 0.25 NEAR.
4. ehor reads the account from the chain and tells the page what to sign, if anything. The page asks the wallet to sign `register` with the release key, the spending limit and the first deposit. For an account that is already registered with another key, it signs `set_device_key` instead, with 1 yoctoNEAR. If this Mac's release key is already registered, there is nothing to sign.
5. The page posts the account id and transaction hashes back to the local server with the token. ehor then confirms both by view call: the limited key is a function-call key for the escrow contract whose method list is either exactly the four requested or empty, with an allowance no larger than requested; and the registered release key equals the local one. A full-access key, another receiver or any other method list is refused, and setup asks the person to remove the key in their wallet. Only when both checks pass does setup save the account, the contract and the release key in `near.json`. The page's report is never trusted on its own.
6. Setup asks the person to restart `ehor serve`, which then announces the account to the team (see Identity binding).

Planned: `ehor near deposit`, `ehor near withdraw` and changing the spending limit each open the same page for one `signAndSendTransaction`. Until then the owner uses their wallet or NEAR CLI; a new release key goes through `ehor near setup`. ehor prints what it is about to ask the wallet for before opening it, and the wallet shows the transaction before the person approves.

The wallet executors load from each wallet provider's URL when the page opens, not from ehor. A compromised executor could ask the wallet to sign something else, so the person must read the wallet's approval screen; this is the same trust every NEAR web app places in its wallet.

### CLI and MCP

CLI commands are for the human, in a terminal:

- Built: `ehor near setup`, `ehor near status`, `ehor pay <bounty>`.
- Planned: `ehor near deposit`, `ehor near withdraw`, `ehor refund <bounty>`, `ehor cancel <bounty>`. `cancel` and `refund` will need those two methods added to the limited key's method list.

`ehor near status` reads, by view call and without the device passphrase: the account's budget and per-bounty limit on the contract, whether the contract holds this Mac's release key, every bounty the account posted, and the bounties this device claimed. It also says whether the limited key file is on this device; checking it on chain would need the passphrase. It changes nothing.

MCP changes:

ehor-near adds four MCP tools through ehor's `ehor.tools` entry point. Their calls run inside `ehor serve`, which decrypts the limited key once at start with the device passphrase. Core ehor's tools are unchanged and know nothing about NEAR.

| Tool | Does |
|---|---|
| `bounty_post(id, near, hours = 24, claimant?)` | Calls `post_bounty` with the message ID, the claimant's linked account and a deadline, then replies to the message with the terms read back from the chain. |
| `bounty_claim(id, bounty)` | Runs the claim checks above, calls `claim` and marks the message claimed. |
| `bounty_done(id, bounty, note?)` | Runs the finish checks above, calls `mark_done` and marks the message done. |
| `bounties()` | Lists the bounties this device posted or claimed, each read from the chain, and names `ehor pay <bounty>` for a Done bounty it posted. The IDs are kept in `near-bounties.json` in the device folder. |

No MCP tool can sign or send a release, and tests check that loading the tools never loads the release signer. Each bounty tool's description tells the agent to show the person the terms and call it only after they approve, and agent clients such as Claude Code ask before running an MCP tool unless the person has allowed it. The money does not depend on that approval: the limited key cannot move NEAR out of the contract.

### Pay flow

`ehor pay <bounty>` does the following:

1. Fetches the bounty by view call and refuses it unless it is Done, has a claimant and was posted by this device's NEAR account. It also refuses when the release key registered for that account is not this device's release key, so the person is never asked to approve a signature the contract would reject.
2. Prints sender, claimant, amount, deadline, and the linked message with its status history (including the claimant's done note) from the running ehor service, marked as untrusted teammate text. If the service is not running, it says the message is not shown and goes on.
3. Asks for confirmation. Anything but `y` or `yes` stops without signing.
4. Signs the canonical payload with the release key. macOS asks for Touch ID or the login password, with a prompt naming the amount, claimant and bounty.
5. Normalizes the signature to raw `r || s` with low-S.
6. Submits `release` with 150 Tgas and checks the bounty reached Released.

The release key is `keys/release.sep` in the ehor device folder. `release` is sent with the device's limited key (`keys/near-limited.key`), which `ehor pay` decrypts with the device passphrase. ehor never reads `~/.near-credentials`.

Secure Enclave and TPM return ASN.1 DER and may return high-S. ehor's signer backends already convert DER to 64-byte raw `r || s`; `ehor.identity.es256.low_s` then replaces s with n - s when s is above n/2. `ehor_near.release.sign_release` does steps 4 and 5 and checks the result against the release key's public key before it is sent.

### Safety invariants

1. No inbound message can cause a signing call or a release.
2. The release signature is produced only inside `ehor pay`, which is not reachable from MCP.
3. Every on-chain action started by an agent shows a draft and waits for approval.
4. Bounty details shown to the human come from the chain, not from the message text.
5. Tests assert invariants 1 and 2 by checking that only `ehor_near.cli`, the module behind `ehor near` and `ehor pay`, imports the release signer or the presence signer, so no agent can even trigger the approval prompt.
6. A release signature needs a person at the Mac to approve it, and the Secure Enclave enforces this.
7. ehor never stores a full-access NEAR key. Registering, deposits, withdrawals and key or limit changes are approved in the owner's wallet.

## Testing

- Contract unit tests for every failure case above: 51 tests, run with `cargo test`.
- Python tests for payload construction against the test vector shared with the Rust tests, signature normalization, setup and the wallet page, the invariant tests, and the bounty tools. The bounty tool tests run two real ehor devices against a contract fake and cover forged bounties, bounties for another message or claimant, mismatched or unregistered release keys, and missing announcements.
- Testnet runs on `ehor-escrow.testnet`: bounty 1 released with ehor's software device key, bounty 2 with the Secure Enclave release key after Touch ID, bounty 3 through `ehor pay`, and bounty 5 after two Claude Code agents, each on its own ehor device, posted, claimed and finished it through the bounty tools.
- Not built: a near-workspaces sandbox test of the full lifecycle.

## Demo script

About 90 seconds. Two ehor devices, each with its own Claude Code session as its agent.

| Time | Step |
|---|---|
| 0:00 to 0:15 | The requester's agent sends a `feature_request` and, once approved, posts a 0.1 NEAR bounty on it with `bounty_post`. |
| 0:15 to 0:35 | The claimant's agent reads the request, claims the bounty with `bounty_claim`, and later marks it done with `bounty_done`, each after approval. |
| 0:35 to 1:05 | The requester runs `ehor pay`, reviews the terms read from the chain, and approves with Touch ID. The explorer shows the transfer. |
| 1:05 to 1:30 | Closing shot: a release with a wrong signature fails on chain, and the agent's tool list has nothing that can pay. |

## Operational risks

| Risk | Mitigation |
|---|---|
| Testnet or RPC outage | ehor reports the RPC error and changes nothing; retry later. |
| The limited key's gas allowance runs out | The bounty tools report the failed call. The owner removes the key in their wallet and runs `ehor near setup` again, which adds it back with a new allowance. |
| NEAR Connect wallets misbehave on a page served from `127.0.0.1` | Tested with Meteor on testnet. Other wallets are untested. |
| A wallet changes or drops its NEAR Connect executor | The connector loads executors from each wallet's URL, so most changes need no ehor release. |

## Open questions

- Should setup also offer to create a new testnet account through the faucet, as a dedicated account that holds only the escrow budget?
- Should a bounty allow more than one claimant from an ehor request? Today `bounty_post` names one: the request's single addressed handle, or the handle the agent names.
- Should open bounties, which any verified account may claim, come in a later contract version, for example for GitHub issue bounties?

# agentmesh protocol — v0

Status: draft. This document is the source of truth for the wire format. Code that disagrees with it is a bug, and changes to the format land in the same commit as the code that implements them.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Overview

Every piece of shared state is a **log entry**. Each device writes one append-only log, numbered from 1, signed by that device's key. Devices sync by exchanging the entries the other side is missing. Entries are self-authenticating, so any peer or relay can forward any entry without being trusted.

Entry kinds:

| Kind           | Purpose                                         | Body          |
|----------------|-------------------------------------------------|---------------|
| `message`      | A request, update or question for teammates     | encrypted     |
| `status`       | Moves a message to `open`, `claimed`, `done` or `wontfix` | encrypted |
| `roster`       | Team membership, device keys, roles, channels   | plaintext     |
| `key_announce` | Publishes a device's X25519 encryption key      | plaintext     |

## 2. Primitives

| Purpose          | Algorithm                                                   |
|------------------|-------------------------------------------------------------|
| Signing          | ECDSA over P-256 with SHA-256 (ES256)                       |
| Hashing          | SHA-256                                                     |
| Key agreement    | X25519                                                      |
| Body encryption  | AES-256-GCM                                                 |
| Encoding         | CBOR, core deterministic encoding (RFC 8949 §4.2.1)         |

P-256 is fixed because the macOS Secure Enclave supports no other curve.

### 2.1 Deterministic encoding

All hashed or signed structures are encoded with CBOR core deterministic encoding:

- Integers, lengths and floats use the shortest form. Floats MUST NOT appear in any v0 structure.
- Map keys are text strings, sorted by their encoded bytes (bytewise lexicographic).
- Indefinite-length items MUST NOT be used.
- Optional fields that are absent are omitted from the map, never encoded as `null`, unless this document says the field is required and nullable.
- Text strings MUST be valid UTF-8 in Unicode Normalization Form C.

A receiver MUST re-encode every decoded structure and reject it if the bytes differ from what it received. This makes every entry have exactly one valid encoding.

### 2.2 Public keys

- Signing keys (`author`, device keys in the roster) are P-256 points in SEC1 **compressed** form: 33 bytes.
- Encryption keys are raw X25519 public keys: 32 bytes.

## 3. Log entry

An entry is a CBOR map with these fields:

| Field     | CBOR type          | Rules |
|-----------|--------------------|-------|
| `v`       | uint               | Protocol version. MUST be `0`. |
| `author`  | bstr (33)          | Compressed P-256 public key of the writing device. |
| `seq`     | uint               | Position in the author's log. Starts at `1`, increases by exactly 1. |
| `prev`    | bstr (32) / null   | ID of the author's entry at `seq - 1`. MUST be `null` iff `seq == 1`. |
| `team`    | bstr (16)          | Team ID, 16 random bytes chosen when the team is created. |
| `channel` | tstr               | See §3.3. |
| `kind`    | tstr               | One of `message`, `status`, `roster`, `key_announce`. |
| `refs`    | array of bstr (32) | IDs of entries this one replies to or updates. 0–16 items, no duplicates, sorted bytewise. |
| `ts`      | uint               | Author's wall-clock time in Unix milliseconds. Display only; never used for ordering or validity. |
| `body`    | bstr               | Encoded body (§4), encrypted or plain depending on `kind`. |
| `sig`     | map                | Signature (§3.2). |

The fields other than `sig` are the **header + body**, called the *signed content*.

### 3.1 Entry ID

```
signed_bytes = deterministic_cbor(entry without "sig")
entry_id     = SHA-256("agentmesh/entry/v0" || 0x00 || signed_bytes)
```

The ID covers the signed content but **not** the signature. ECDSA signatures are malleable and non-deterministic: the same content can carry many valid signatures. Hashing the signature would let anyone mint a second ID for the same entry and fake a fork (§5.4). With this rule, two copies of an entry that differ only in signature have the same ID and dedupe.

### 3.2 Signature

```
sig = { "alg": tstr, "value": bstr }
```

The message signed is:

```
"agentmesh/sig/v0" || 0x00 || signed_bytes
```

| `alg`   | `value` | Status |
|---------|---------|--------|
| `es256` | 64 bytes, `r || s`, each big-endian and left-padded to 32 bytes | v0 |
| `es256-webauthn` | FIDO2 assertion; wrapped format to be defined in Phase 5 | reserved |

Verifiers MUST accept both low-S and high-S signatures, because some hardware backends do not normalize S. This is safe because the entry ID does not depend on the signature.

### 3.3 Channels

| Pattern | Meaning |
|---------|---------|
| `#name` where `name` matches `[a-z0-9][a-z0-9-]{0,31}` | Team channel, e.g. `#backend`. |
| `dm:<a>+<b>` with member handles `a < b` (bytewise) | Direct conversation between two members. |
| `_team` | Team-wide control channel. `roster` and `key_announce` entries MUST use it, and other kinds MUST NOT. |

Member handles match `[a-z0-9][a-z0-9_-]{0,31}`.

### 3.4 Size limits

- An encoded entry (including `sig`) MUST NOT exceed 16 KiB.
- `refs` MUST NOT exceed 16 items.

Receivers drop oversize entries without storing them.

## 4. Bodies

Bodies are CBOR maps encoded deterministically. For `message` and `status`, the encoded body is then encrypted (§6) and the result stored in `body`. Before Phase 4, implementations MAY use the `none` encryption mode, and receivers MUST reject it once the team's roster sets `require_encryption` (§4.3).

### 4.1 `message`

| Field        | Type              | Rules |
|--------------|-------------------|-------|
| `type`       | tstr              | `feature_request`, `shipped`, `blocked`, `question` or `fyi`. |
| `to`         | array of tstr     | 1–8 targets: a member handle (`tobi`), a role (`@mobile`) or a channel (`#backend`). |
| `summary`    | tstr              | 1–140 characters. Hard cap. |
| `detail`     | tstr              | Optional, at most 2,000 characters. |
| `acceptance` | tstr              | Optional, at most 280 characters. Only allowed when `type` is `feature_request`. |
| `links`      | array of tstr     | Optional, 0–10 items, each at most 200 characters (PR, commit, path or URL). |

Lengths are counted in Unicode code points after NFC normalization. A writer that gets input over a limit MUST refuse it with an error, not truncate it.

A reply is a `message` whose `refs` contains the ID of the message it answers.

### 4.2 `status`

| Field    | Type | Rules |
|----------|------|-------|
| `status` | tstr | `open`, `claimed`, `done` or `wontfix`. |
| `note`   | tstr | Optional, at most 140 characters. |

`refs` MUST contain exactly one ID: the target `message`, which MUST be in the same channel. A message's current state is that of its latest valid `status` entry, ordered as in §7. A message with no status entry is `open`.

### 4.3 `roster`

| Field                | Type   | Rules |
|----------------------|--------|-------|
| `version`            | uint   | Starts at `1`, increases by exactly 1 per roster. |
| `previous`           | bstr (32) / null | ID of the roster entry this one replaces. `null` iff `version == 1`. |
| `team_name`          | tstr   | At most 64 characters. Display only. |
| `admins`             | array of tstr | Handles of members allowed to sign the next roster. At least 1. |
| `members`            | array of member | Sorted by `handle`. Handles are unique. |
| `require_encryption` | bool   | When `true`, `message` and `status` bodies MUST NOT use encryption mode `none`. |

`member`:

| Field      | Type           | Rules |
|------------|----------------|-------|
| `handle`   | tstr           | Unique within the roster. |
| `roles`    | array of tstr  | Sorted, each matching `@[a-z0-9][a-z0-9-]{0,31}`. |
| `channels` | array of tstr  | Sorted team channels this member can read and post in. `*` means all. |
| `devices`  | array of device | 1–16 devices, sorted by `key`. |

`device`:

| Field   | Type      | Rules |
|---------|-----------|-------|
| `key`   | bstr (33) | Compressed P-256 signing key. A key MUST NOT appear under two members. |
| `trust` | tstr      | `hardware` or `software`. Software keys are allowed but marked lower trust. |
| `label` | tstr      | At most 64 characters, e.g. `godwin-macbook`. Display only. |

Roster rules:

- The `version == 1` roster is the **genesis roster**. Its author MUST be one of its own admin devices. The `team` ID is fixed by it.
- Any later roster MUST be authored by a device of an admin listed in the roster it replaces (`previous`).
- Rosters form a chain through `previous`. A device accepts a roster only if it can verify the whole chain back to genesis.
- If two valid rosters claim the same `version`, that is a **roster fork**: both are kept, neither is applied, and the conflict is surfaced to every admin. The team stays on the last unforked roster until an admin publishes a roster at `version + 1` that names one of them as `previous`.
- The **current roster** is the valid, unforked roster with the highest `version`.

### 4.4 `key_announce`

| Field        | Type      | Rules |
|--------------|-----------|-------|
| `x25519`     | bstr (32) | The device's encryption public key. |

A device's current encryption key is the one in its `key_announce` with the highest `seq`. Trust in it comes from the device's signing key, which the roster vouches for.

## 5. Validation

A receiver runs these checks, in order, on every entry before storing it. The first failure decides the outcome.

1. **Size**: encoded entry is at most 16 KiB. Otherwise **drop**.
2. **Decode**: valid CBOR, re-encodes to identical bytes (§2.1), all fields present with correct types and lengths. Otherwise **reject**.
3. **Version**: `v == 0`. Otherwise **reject**.
4. **Duplicate**: if an entry with this ID is already stored, **ignore**.
5. **Signature**: `sig` verifies under `author` (§3.2). Otherwise **reject**.
6. **Team**: `team` matches the local team. Otherwise **reject**.
7. **Shape**: `seq`/`prev` agree (`prev` is `null` iff `seq == 1`), `channel` matches §3.3 for the `kind`, `refs` are sorted and unique, the body decodes and meets §4. Otherwise **reject**.
8. **Authorization**: `author` is a device on the current roster, and that member may post in `channel`. A `roster` entry instead follows §4.3: its author is an admin device of the roster it replaces, or of itself for the genesis roster. A `dm:` channel requires `author` to belong to one of its two members. Otherwise **park** (the roster that admits it may not have arrived yet). Parked entries that are still unauthorized once the roster is up to date are **rejected**.
9. **Fork**: another stored entry has the same `author` and `seq` but a different ID. **Store both**, mark the author's device as forked, and alert the admins. Entries from a forked device after the fork point are not shown until an admin resolves it by removing or re-adding the device.
10. **Chain**: if `seq > 1` and the entry at `seq - 1` is stored, its ID MUST equal `prev`. Otherwise **reject**. If the entry at `seq - 1` is not stored yet, **park** this entry and request the gap from peers.
11. **Store**: write the entry as received. Entries are immutable from here on.

Outcomes:

| Outcome | Meaning |
|---------|---------|
| **drop**   | Discard silently. Counts toward the sender's rate limit. |
| **reject** | Discard and record the reason. Not stored, not forwarded. |
| **ignore** | Already have it. |
| **park**   | Hold outside the log until a missing entry or roster arrives, then re-run validation. |

Entries stored by step 11 MAY be forwarded to any peer allowed in the channel (gossip). Parked and rejected entries MUST NOT be forwarded.

Receivers SHOULD rate-limit entries per author. The limit is local policy, not part of the protocol.

## 6. Encryption

Body encryption is defined in full in Phase 4. v0 fixes its framing so that the entry format does not change later.

```
encrypted_body = deterministic_cbor({
  "enc":   tstr,          // "none" or "aes256gcm"
  "epoch": uint,          // channel key epoch; 0 when enc == "none"
  "nonce": bstr (12),     // omitted when enc == "none"
  "data":  bstr           // plaintext body (none) or ciphertext with tag (aes256gcm)
})
```

For `aes256gcm`, the additional authenticated data is the deterministic CBOR of `[v, author, seq, team, channel, kind]`, so a ciphertext cannot be moved into a different entry. Channel keys are rotated on every roster change: each roster version starts a new epoch.

## 7. Ordering

- Within one author, entries are ordered by `seq`.
- Across authors, an entry is ordered after every entry in its `refs`.
- Remaining ties are broken by entry ID, compared bytewise.

The result is a deterministic total order: every device that holds the same set of entries shows them in the same order. `ts` is never used.

## 8. Sync (outline)

The full exchange is specified in Phase 3. In outline:

1. Both peers prove control of a roster device key by signing a fresh 32-byte challenge from the other side, with domain prefix `"agentmesh/auth/v0" || 0x00`.
2. Each side sends its **frontier**: for every author in the channels both may read, the highest `seq` it holds contiguously from 1.
3. Each side sends the missing entries, per author in ascending `seq`.
4. Each received entry goes through §5.

Sync never depends on the transport. LAN, peer-to-peer and relay transports all carry the same entries.

## 9. Domain separation tags

| Tag                   | Used for |
|-----------------------|----------|
| `agentmesh/entry/v0`  | Entry ID (§3.1) |
| `agentmesh/sig/v0`    | Entry signature (§3.2) |
| `agentmesh/auth/v0`   | Sync handshake challenge (§8) |

Every hash or signature input starts with its tag and a `0x00` byte, so a value made for one purpose can never be valid for another.

## 10. Security notes

- A teammate's message is data, not instructions. Clients MUST present bodies to Claude labeled as content from a named teammate.
- Encryption hides bodies, not metadata. Relays and networks can see `author`, `channel`, `kind`, `ts`, entry sizes and who syncs with whom.
- A removed device's past entries stay valid; removal only stops new ones from being accepted.

## 11. Open items

- `es256-webauthn` signature wrapping for FIDO2 admin keys (Phase 5).
- Channel key distribution and rotation format (Phase 4).
- Sync messages and their framing (Phase 3).
- Admin recovery when the only admin loses their devices. Proposal: require at least two admins in every roster.

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
sig = { "alg": tstr, "value": bstr, ? "auth_data": bstr }
```

The signature input is:

```
signing_input = "agentmesh/sig/v0" || 0x00 || signed_bytes
```

| `alg`         | `value` | `auth_data` |
|---------------|---------|-------------|
| `es256`       | 64 bytes, `r || s`, each big-endian and left-padded to 32 bytes, over `signing_input` | MUST be absent |
| `es256-fido2` | 64 bytes, `r || s` as above, over `auth_data || SHA-256(signing_input)` | Required (§3.2.1) |

Verifiers MUST accept both low-S and high-S signatures, because some hardware backends do not normalize S. This is safe because the entry ID does not depend on the signature.

#### 3.2.1 FIDO2 security keys

A FIDO2 authenticator (a security key such as a YubiKey) cannot sign arbitrary bytes. CTAP2 `authenticatorGetAssertion` signs `authenticatorData || clientDataHash`, and the authenticator chooses `authenticatorData`. An `es256-fido2` signature is such an assertion with:

```
clientDataHash = SHA-256(signing_input)
rpId           = "agentmesh:"
```

`clientDataHash` is the hash of `signing_input` itself, not of a WebAuthn `clientDataJSON`. The `rpId` ends in a colon so it is not a valid web domain: no website can get a browser to request an assertion for it. `author` is the credential's public key in compressed form (§2.2). The authenticator returns a DER signature, which writers convert to `r || s`.

`auth_data` is the authenticator data exactly as the authenticator returned it:

| Bytes   | Content |
|---------|---------|
| 0–31    | `rpIdHash`, SHA-256 of the UTF-8 `rpId` |
| 32      | Flags. Bit 0 (`UP`) is user presence; bit 6 (`AT`) is attested credential data; bit 7 (`ED`) is extension data. |
| 33–36   | Signature counter, big-endian |
| 37–     | Extension outputs, present iff `ED` is set |

A verifier MUST check, and reject the entry unless:

1. `kind` is `roster`. Security keys sign roster entries only.
2. `auth_data` is at least 37 bytes, `AT` is clear, and `auth_data` is longer than 37 bytes iff `ED` is set.
3. `rpIdHash` equals SHA-256(`"agentmesh:"`).
4. `UP` is set. This is what makes a roster change need a physical touch.
5. `value` verifies under `author` over `auth_data || SHA-256(signing_input)`.

The `UV` flag (PIN or biometric) is not required. The signature counter is not checked: devices see entries in different orders and some authenticators always report zero.

`auth_data` is part of `sig`, so like the rest of the signature it is not covered by the entry ID (§3.1). Two assertions over the same content are the same entry.

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

Bodies are CBOR maps encoded deterministically. For `message` and `status`, the encoded body is then sealed (§6) and the result stored in `body`. The `none` mode exists for teams whose roster does not set `require_encryption` (§4.3).

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

| Field    | Type      | Rules |
|----------|-----------|-------|
| `target` | bstr (32) | ID of the `message` this status applies to. |
| `status` | tstr      | `open`, `claimed`, `done` or `wontfix`. |
| `note`   | tstr      | Optional, at most 140 characters. |

`refs` MUST contain `target`. It also lists every status entry for the same target that the writer had seen and is replacing, so that updates from different authors are ordered by §7 rather than by entry ID. The target MUST be in the same channel; receivers check this once they hold the target.

A message's current state is that of the status entry for it that no other status entry replaces, by `refs` or by being earlier in the same author's log. If several remain, the last one in §7 order wins. A message with no status entry is `open`.

### 4.3 `roster`

| Field                | Type   | Rules |
|----------------------|--------|-------|
| `version`            | uint   | Starts at `1`, increases by exactly 1 per roster. |
| `previous`           | bstr (32) / null | ID of the roster entry this one replaces. `null` iff `version == 1`. |
| `team_name`          | tstr   | At most 64 characters. Display only. |
| `admins`             | array of tstr | Handles of members allowed to sign the next roster. At least 1. |
| `members`            | array of member | Sorted by `handle`. Handles are unique. |
| `require_encryption` | bool   | When `true`, `message` and `status` bodies MUST NOT use encryption mode `none`. |
| `relays`             | array of relay | The team's relays, sorted by `key`. May be empty. |

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
| `trust` | tstr      | `hardware` or `software`. Software keys are allowed but marked lower trust. Set by the admin who adds the device, from what the device reports; nothing attests to it, so implementations MUST NOT treat it as proof. |
| `label` | tstr      | At most 64 characters, e.g. `godwin-macbook`. Display only. |

`relay`:

| Field     | Type      | Rules |
|-----------|-----------|-------|
| `key`     | bstr (33) | The relay's P-256 key, used in the sync handshake (§8). MUST NOT also be a member device key. |
| `address` | tstr      | `host:port`, at most 255 characters. |

Roster rules:

- The `version == 1` roster is the **genesis roster**. Its author MUST be one of its own admin devices. The `team` ID is fixed by it.
- A device learns the genesis roster's entry ID from its invite and accepts no other genesis roster. Without this pin, anyone who knows the team ID could publish a rival genesis roster.
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
5. **Signature**: `sig` verifies under `author` (§3.2), including the `es256-fido2` checks of §3.2.1. Otherwise **reject**.
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

`message` and `status` bodies are sealed for every device allowed to read the channel when the entry is written.

```
encrypted_body = deterministic_cbor({
  "enc":       "none" | "x25519-aes256gcm",
  "epoch":     uint,                       // roster version used to pick recipients; 0 for none
  "data":      bstr,                       // plaintext (none) or ciphertext with tag
  "ephemeral": bstr (32),                  // sealed only: one-time X25519 public key
  "keys":      [[hint bstr (8), wrapped bstr (48)], ...],  // sealed only: 1-256, sorted by hint
  "nonce":     bstr (12)                   // sealed only
})
```

**Recipients** are the X25519 keys, from each device's latest `key_announce` (§4.4), of every roster device whose member may read the channel: members whose `channels` allow a team channel, both members of a `dm:` channel, and always the writer's own devices. A device that has not announced a key cannot be a recipient.

**Sealing**:

1. Pick a random 32-byte content key and 12-byte nonce. `data` is AES-256-GCM of the encoded body under the content key, with additional authenticated data `deterministic_cbor([v, author, team, channel, kind])`, so a ciphertext cannot be moved to another author, team, channel or kind.
2. Generate a one-time X25519 key pair; its public half is `ephemeral`.
3. For each recipient public key `R`: `hint` is the first 8 bytes of SHA-256(`R`), and the wrapping key is HKDF-SHA256 of X25519(ephemeral private, `R`) with no salt and info `"agentmesh/wrap/v0" || 0x00 || ephemeral || R`. `wrapped` is AES-256-GCM of the content key under the wrapping key, with a nonce of 12 zero bytes and `hint` as additional data. The ephemeral key is new for every message, so each wrapping key is used exactly once.

A recipient finds its `hint`, derives the same wrapping key with its private key and `ephemeral`, unwraps the content key and decrypts `data`.

Because recipients are chosen per message, removing a member from the roster stops them from reading every later message with nothing to rotate. The flip side is that a device can only read messages written after its `key_announce` reached the writer.

When the current roster sets `require_encryption`, receivers deny `message` and `status` entries whose envelope is `none` (§5 step 8). New teams set it in their genesis roster.

## 7. Ordering

- Within one author, entries are ordered by `seq`.
- Across authors, an entry is ordered after every entry in its `refs`.
- Remaining ties are broken by entry ID, compared bytewise.

The result is a deterministic total order: every device that holds the same set of entries shows them in the same order. `ts` is never used.

## 8. Sync

Two peers sync over any reliable byte stream (TCP on a LAN in v0). Each message is a 4-byte big-endian length followed by a deterministic CBOR map with a `t` field naming its type. Frames over 1 MiB end the session.

| Type       | Fields | Meaning |
|------------|--------|---------|
| `hello`    | `v` (0), `team` bstr (16), `nonce` bstr (32) | Opens the session with a fresh random challenge. |
| `auth`     | `key` bstr (33), `sig` bstr (64) | Proves control of a device key. |
| `frontier` | `heads`: array of `[author bstr (33), seq uint]` | Highest stored seq per author, at most 10,000 authors. |
| `entries`  | `items`: array of encoded entries | At most 48 entries per frame. |
| `done`     | none | The sender has nothing more to send. |

The exchange:

1. The side that opened the connection is the **initiator**. Both sides send `hello`. A peer whose `team` differs is disconnected.
2. Both sides send `auth`. Each signs, with its device key:
   `"agentmesh/auth/v0" || 0x00 || team || role || initiator_nonce || responder_nonce`
   where `role` is `I` for the initiator and `R` for the responder, so a signature can never be reflected back to its signer. A bad signature ends the session.
3. Each side classifies the other by the current roster: a **member** if the key is a roster device or one of the roster's `relays`, otherwise a **guest**.
4. Both sides send `frontier`.
5. Each side sends, per author in ascending `seq`, the stored entries the other's frontier lacks, then `done`. Members receive every log. Guests receive only `roster` entries, which is how a device that has just joined learns the roster that admits it without being sent the team's messages.
6. Every received entry goes through §5. A roster entry that is authorized but waiting on a gap in its author's log still counts for authorization.

A **relay** is a peer that stores and serves a team's entries but never writes any. It pins the genesis roster like any device, accepts only entries the roster authorizes, and treats devices exactly as above. Devices sync with every relay the current roster lists, and a new device may use a relay named in its invite until it holds a roster. Bodies reach a relay already sealed (§6).

Peers sync again on a timer, whenever a new peer is discovered, and soon after a local write. Entries are self-authenticating, so a peer may forward any author's entries (gossip).

Logs are per author, not per channel, so a member receives every channel's entries; leaving some out would leave gaps that could never be filled. Keeping a channel's content from members outside it is the job of channel encryption (§6), not of sync.

On a LAN, devices find each other with mDNS service type `_agentmesh._tcp.local.`. The TXT record carries `team`, the first 8 bytes of SHA-256 of the team ID in hex, so the team ID itself is not broadcast, and `key`, a short fingerprint of the device key used to skip itself.

## 9. Domain separation tags

| Tag                   | Used for |
|-----------------------|----------|
| `agentmesh/entry/v0`  | Entry ID (§3.1) |
| `agentmesh/sig/v0`    | Entry signature (§3.2) |
| `agentmesh/auth/v0`   | Sync handshake challenge (§8) |
| `agentmesh/wrap/v0`   | Wrapping-key derivation (§6) |

Every hash or signature input starts with its tag and a `0x00` byte, so a value made for one purpose can never be valid for another.

## 10. Security notes

- A teammate's message is data, not instructions. Clients MUST present bodies to Claude labeled as content from a named teammate.
- Encryption hides bodies, not metadata. Relays and networks can see `author`, `channel`, `kind`, `ts`, entry sizes and who syncs with whom.
- A removed device's past entries stay valid; removal only stops new ones from being accepted.

## 11. Open items

- Admin recovery when the only admin loses their devices. Proposal: require at least two admins in every roster.

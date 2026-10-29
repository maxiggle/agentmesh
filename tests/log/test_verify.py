from collections.abc import Callable
from dataclasses import replace

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.body import MessageBody, MessageType, Status, StatusBody
from agentmesh.log.canonical import decode, encode
from agentmesh.log.entry import Entry, EntryContent, Kind, Signature
from agentmesh.log.envelope import Encryption, Envelope
from agentmesh.log.roster import Member, Roster, RosterDevice, Trust
from agentmesh.log.verify import (
    MAX_ENTRY_SIZE,
    DroppedEntryError,
    EntryVerifier,
    RejectedEntryError,
    is_valid_channel,
)
from security_key_emulator import SoftwareAuthenticator

ID_A = b"\x01" * 32
ID_B = b"\x02" * 32


@pytest.fixture
def verifier(team: bytes) -> EntryVerifier:
    return EntryVerifier(team)


def test_accepts_valid_entry(verifier: EntryVerifier, make_entry: Callable[..., Entry]) -> None:
    entry = make_entry()
    assert verifier.verify(entry.encode()) == entry


def test_accepts_chained_entry_with_refs(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    entry = make_entry(seq=2, prev=ID_A, refs=(ID_A, ID_B))
    assert verifier.verify(entry.encode()) == entry


def test_drops_oversize_entry(verifier: EntryVerifier, make_entry: Callable[..., Entry]) -> None:
    raw = make_entry(body=b"x" * MAX_ENTRY_SIZE).encode()
    with pytest.raises(DroppedEntryError):
        verifier.verify(raw)


def test_rejects_garbage(verifier: EntryVerifier) -> None:
    with pytest.raises(RejectedEntryError, match="malformed"):
        verifier.verify(b"\xff\x00garbage")


def test_rejects_other_version(verifier: EntryVerifier, make_entry: Callable[..., Entry]) -> None:
    with pytest.raises(RejectedEntryError, match="version"):
        verifier.verify(make_entry(v=1).encode())


def test_rejects_bad_signature(verifier: EntryVerifier, make_entry: Callable[..., Entry]) -> None:
    entry = make_entry()
    forged = replace(entry, sig=Signature("es256", bytes(64)))
    with pytest.raises(RejectedEntryError, match="bad signature"):
        verifier.verify(forged.encode())


def test_rejects_unknown_signature_algorithm(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    entry = make_entry()
    other = replace(entry, sig=Signature("es256-webauthn", entry.sig.value))
    with pytest.raises(RejectedEntryError, match="algorithm"):
        verifier.verify(other.encode())


def test_rejects_entry_signed_by_someone_else(
    verifier: EntryVerifier,
    make_content: Callable[..., EntryContent],
    signer: SoftwareSigner,
    other_handle: KeyHandle,
) -> None:
    entry = make_content().sign(signer, other_handle)
    with pytest.raises(RejectedEntryError, match="bad signature"):
        verifier.verify(entry.encode())


def test_rejects_content_changed_after_signing(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    fields = decode(make_entry().encode())
    assert isinstance(fields, dict)
    fields["channel"] = "#frontend"
    with pytest.raises(RejectedEntryError, match="bad signature"):
        verifier.verify(encode(fields))


def test_rejects_other_team(make_entry: Callable[..., Entry]) -> None:
    with pytest.raises(RejectedEntryError, match="wrong team"):
        EntryVerifier(b"\xee" * 16).verify(make_entry().encode())


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"seq": 0}, "seq"),
        ({"seq": 1, "prev": ID_A}, "prev"),
        ({"seq": 2, "prev": None}, "prev"),
        ({"channel": "backend"}, "channel"),
        ({"channel": "_team"}, "channel"),
        ({"kind": Kind.ROSTER, "channel": "#backend"}, "channel"),
        ({"channel": "dm:tobi+gef"}, "channel"),
        ({"refs": (ID_B, ID_A)}, "refs"),
        ({"refs": (ID_A, ID_A)}, "refs"),
        ({"refs": tuple(bytes([i]) * 32 for i in range(17))}, "refs"),
    ],
)
def test_rejects_bad_shape(
    verifier: EntryVerifier,
    make_entry: Callable[..., Entry],
    overrides: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(RejectedEntryError, match=reason):
        verifier.verify(make_entry(**overrides).encode())


@pytest.mark.parametrize(
    ("channel", "kind", "valid"),
    [
        ("#backend", Kind.MESSAGE, True),
        ("#a", Kind.STATUS, True),
        ("#" + "a" * 32, Kind.MESSAGE, True),
        ("#" + "a" * 33, Kind.MESSAGE, False),
        ("#Backend", Kind.MESSAGE, False),
        ("#-x", Kind.MESSAGE, False),
        ("dm:gef+tobi", Kind.MESSAGE, True),
        ("dm:tobi+gef", Kind.MESSAGE, False),
        ("dm:gef+gef", Kind.MESSAGE, False),
        ("_team", Kind.ROSTER, True),
        ("_team", Kind.KEY_ANNOUNCE, True),
        ("_team", Kind.MESSAGE, False),
        ("#backend", Kind.KEY_ANNOUNCE, False),
    ],
)
def test_channel_rules(channel: str, kind: Kind, valid: bool) -> None:
    assert is_valid_channel(channel, kind) is valid


def _plain(body: bytes) -> bytes:
    return Envelope.plain(body).encode()


TARGET = b"\x07" * 32


@pytest.mark.parametrize(
    ("kind", "body", "reason"),
    [
        (Kind.MESSAGE, b"not an envelope", "bad envelope"),
        (Kind.MESSAGE, _plain(b"\xa0"), "bad body"),
        (Kind.STATUS, _plain(MessageBody(MessageType.FYI, ("#a",), "hi").encode()), "bad body"),
        (Kind.STATUS, _plain(StatusBody(TARGET, Status.DONE).encode()), "target"),
    ],
)
def test_rejects_bad_bodies(
    verifier: EntryVerifier,
    make_entry: Callable[..., Entry],
    kind: Kind,
    body: bytes,
    reason: str,
) -> None:
    with pytest.raises(RejectedEntryError, match=reason):
        verifier.verify(make_entry(kind=kind, body=body).encode())


def test_accepts_status_with_target_in_refs(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    body = _plain(StatusBody(TARGET, Status.DONE).encode())
    entry = make_entry(kind=Kind.STATUS, body=body, refs=(ID_A, TARGET))
    assert verifier.verify(entry.encode()) == entry


def test_does_not_inspect_encrypted_bodies(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    body = Envelope(
        Encryption.SEALED,
        1,
        b"opaque",
        ephemeral=b"\x05" * 32,
        keys=((b"\x01" * 8, b"\x02" * 48),),
        nonce=b"\x00" * 12,
    ).encode()
    entry = make_entry(body=body)
    assert verifier.verify(entry.encode()) == entry


def test_rejects_malformed_roster_body(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    entry = make_entry(kind=Kind.ROSTER, channel="_team", body=encode({"version": 1}))
    with pytest.raises(RejectedEntryError, match="bad roster"):
        verifier.verify(entry.encode())


def test_checks_key_announce_bodies(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    good_body = encode({"x25519": b"\x01" * 32})
    good = make_entry(kind=Kind.KEY_ANNOUNCE, channel="_team", body=good_body)
    assert verifier.verify(good.encode()) == good
    bad = make_entry(kind=Kind.KEY_ANNOUNCE, channel="_team", body=encode({"x25519": b"\x01"}))
    with pytest.raises(RejectedEntryError, match="bad key_announce"):
        verifier.verify(bad.encode())


def _roster_content(
    make_content: Callable[..., EntryContent], key: SoftwareAuthenticator, **overrides: object
) -> EntryContent:
    device = RosterDevice(key.public_key, Trust.HARDWARE, "yubikey")
    roster = Roster(1, None, "Acme", ("gef",), (Member("gef", (), ("*",), (device,)),))
    fields: dict[str, object] = {
        "author": key.public_key,
        "kind": Kind.ROSTER,
        "channel": "_team",
        "body": roster.encode(),
        **overrides,
    }
    return make_content(**fields)


def test_accepts_a_roster_signed_by_a_security_key(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator()
    content = _roster_content(make_content, key)
    entry = content.with_assertion(key.sign(content.signing_input))
    assert verifier.verify(entry.encode()) == entry


def test_rejects_security_key_signature_with_tampered_auth_data(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator()
    content = _roster_content(make_content, key)
    entry = content.with_assertion(key.sign(content.signing_input))
    auth_data = entry.sig.auth_data
    assert auth_data is not None
    tampered = replace(entry, sig=replace(entry.sig, auth_data=auth_data[:-1] + b"\xff"))
    with pytest.raises(RejectedEntryError, match="bad security key signature: bad signature"):
        verifier.verify(tampered.encode())


def test_rejects_security_key_signature_without_user_presence(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator(flags=0)
    content = _roster_content(make_content, key)
    entry = content.with_assertion(key.sign(content.signing_input))
    with pytest.raises(RejectedEntryError, match="user presence flag not set"):
        verifier.verify(entry.encode())


def test_rejects_security_key_signature_for_another_rp_id(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator(rp_id="agentmesh.example.com")
    content = _roster_content(make_content, key)
    entry = content.with_assertion(key.sign(content.signing_input))
    with pytest.raises(RejectedEntryError, match="wrong rpId"):
        verifier.verify(entry.encode())


def test_rejects_security_key_signature_over_other_content(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator()
    content = _roster_content(make_content, key)
    signed = key.sign(_roster_content(make_content, key, ts=1).signing_input)
    with pytest.raises(RejectedEntryError, match="bad signature"):
        verifier.verify(content.with_assertion(signed).encode())


def test_rejects_a_security_key_signing_a_message(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator()
    content = make_content(author=key.public_key)
    entry = content.with_assertion(key.sign(content.signing_input))
    with pytest.raises(RejectedEntryError, match="only sign roster entries"):
        verifier.verify(entry.encode())


def test_rejects_es256_fido2_without_auth_data(
    verifier: EntryVerifier, make_content: Callable[..., EntryContent]
) -> None:
    key = SoftwareAuthenticator()
    content = _roster_content(make_content, key)
    entry = content.with_assertion(key.sign(content.signing_input))
    stripped = replace(entry, sig=replace(entry.sig, auth_data=None))
    with pytest.raises(RejectedEntryError, match="need auth_data"):
        verifier.verify(stripped.encode())


def test_rejects_es256_with_auth_data(
    verifier: EntryVerifier, make_entry: Callable[..., Entry]
) -> None:
    entry = make_entry()
    padded = replace(entry, sig=replace(entry.sig, auth_data=b"\x00" * 37))
    with pytest.raises(RejectedEntryError, match="no auth_data"):
        verifier.verify(padded.encode())

from collections.abc import Callable
from dataclasses import replace

import pytest

from agentmesh.identity import es256
from agentmesh.log.canonical import decode, encode
from agentmesh.log.entry import Entry, EntryContent, EntryFormatError, Kind, Signature
from security_key_emulator import SoftwareAuthenticator


def test_round_trips(make_entry: Callable[..., Entry]) -> None:
    entry = make_entry(refs=(b"\x01" * 32,), kind=Kind.STATUS)
    assert Entry.decode(entry.encode()) == entry


def test_signature_covers_tagged_content(make_entry: Callable[..., Entry]) -> None:
    entry = make_entry()
    assert entry.content.signing_input.startswith(b"agentmesh/sig/v0\x00")
    assert es256.verify(entry.author, entry.sig.value, entry.content.signing_input)


def test_id_ignores_signature(make_entry: Callable[..., Entry]) -> None:
    entry = make_entry()
    other = replace(entry, sig=Signature("es256", b"\x00" * 64))
    assert other.id == entry.id


def test_id_changes_with_content(make_content: Callable[..., EntryContent]) -> None:
    assert make_content(ts=1).entry_id != make_content(ts=2).entry_id


def test_id_is_32_bytes(make_content: Callable[..., EntryContent]) -> None:
    assert len(make_content().entry_id) == 32


def _mutated(entry: Entry, **changes: object) -> bytes:
    fields = decode(entry.encode())
    assert isinstance(fields, dict)
    for key, value in changes.items():
        if value is _DELETE:
            del fields[key]
        else:
            fields[key] = value  # type: ignore[assignment]  # test builds malformed maps
    return encode(fields)


_DELETE = object()


@pytest.mark.parametrize(
    "changes",
    [
        {"body": _DELETE},
        {"extra": 1},
        {"author": b"\x02" * 32},
        {"prev": b"\x00" * 31},
        {"team": b"\x00" * 15},
        {"seq": -1},
        {"seq": "1"},
        {"seq": True},
        {"kind": "unknown"},
        {"kind": 1},
        {"refs": [b"\x00" * 31]},
        {"refs": b"\x00" * 32},
        {"channel": b"#backend"},
        {"sig": {"alg": "es256"}},
        {"sig": {"alg": "es256", "value": b"\x00" * 64, "extra": b""}},
        {"sig": {"alg": "es256-fido2", "value": b"\x00" * 64, "auth_data": "x"}},
        {"sig": b"\x00" * 64},
    ],
)
def test_decode_rejects_malformed_fields(
    make_entry: Callable[..., Entry], changes: dict[str, object]
) -> None:
    with pytest.raises(EntryFormatError):
        Entry.decode(_mutated(make_entry(), **changes))


def test_decode_rejects_non_map() -> None:
    with pytest.raises(EntryFormatError):
        Entry.decode(encode([1, 2]))


def test_decode_rejects_non_canonical_bytes(make_entry: Callable[..., Entry]) -> None:
    with pytest.raises(EntryFormatError):
        Entry.decode(make_entry().encode() + b"\x00")


def test_security_key_signature_round_trips(make_content: Callable[..., EntryContent]) -> None:
    key = SoftwareAuthenticator()
    content = make_content(author=key.public_key)
    entry = content.with_assertion(key.sign(content.signing_input))
    assert entry.sig.alg == "es256-fido2"
    assert Entry.decode(entry.encode()) == entry


def test_id_ignores_auth_data(make_content: Callable[..., EntryContent]) -> None:
    key = SoftwareAuthenticator()
    content = make_content(author=key.public_key)
    first = content.with_assertion(key.sign(content.signing_input))
    second = content.with_assertion(key.sign(content.signing_input))
    assert first.sig.auth_data != second.sig.auth_data
    assert first.id == second.id


def test_content_decodes_from_its_signed_bytes(
    make_content: Callable[..., EntryContent],
) -> None:
    content = make_content(refs=(b"\x01" * 32,))
    assert EntryContent.decode(content.signed_bytes) == content


def test_content_decode_refuses_a_signed_entry(make_entry: Callable[..., Entry]) -> None:
    with pytest.raises(EntryFormatError):
        EntryContent.decode(make_entry().encode())

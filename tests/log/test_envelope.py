import pytest

from agentmesh.log.canonical import encode
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError, body_aad

KEYS = ((b"\x01" * 8, b"\x02" * 48), (b"\x03" * 8, b"\x04" * 48))


def sealed(**changes: object) -> Envelope:
    fields: dict[str, object] = {
        "enc": Encryption.SEALED,
        "epoch": 2,
        "data": b"cipher",
        "ephemeral": b"\x05" * 32,
        "keys": KEYS,
        "nonce": b"\x06" * 12,
    }
    fields.update(changes)
    return Envelope(**fields)  # type: ignore[arg-type]  # built from a dict on purpose


def test_plain_round_trip() -> None:
    envelope = Envelope.plain(b"data")
    assert Envelope.decode(envelope.encode()) == envelope


def test_sealed_round_trip() -> None:
    envelope = sealed()
    assert Envelope.decode(envelope.encode()) == envelope


@pytest.mark.parametrize(
    "changes",
    [
        {"epoch": -1},
        {"ephemeral": None},
        {"ephemeral": b"\x05" * 31},
        {"nonce": None},
        {"nonce": b"\x06" * 11},
        {"keys": ()},
        {"keys": (KEYS[1], KEYS[0])},
        {"keys": (KEYS[0], KEYS[0])},
        {"keys": ((b"\x01" * 7, b"\x02" * 48),)},
        {"keys": ((b"\x01" * 8, b"\x02" * 47),)},
    ],
)
def test_sealed_rules(changes: dict[str, object]) -> None:
    with pytest.raises(EnvelopeError):
        sealed(**changes)


@pytest.mark.parametrize(
    "changes",
    [{"epoch": 1}, {"nonce": b"\x00" * 12}, {"ephemeral": b"\x00" * 32}, {"keys": KEYS}],
)
def test_plain_carries_only_data(changes: dict[str, object]) -> None:
    fields: dict[str, object] = {"enc": Encryption.NONE, "epoch": 0, "data": b"x"}
    fields.update(changes)
    with pytest.raises(EnvelopeError):
        Envelope(**fields)  # type: ignore[arg-type]  # built from a dict on purpose


@pytest.mark.parametrize(
    "fields",
    [
        {"enc": "rot13", "epoch": 0, "data": b""},
        {"enc": "none", "epoch": 0},
        {"enc": "none", "epoch": True, "data": b""},
        {"enc": "none", "epoch": 0, "data": "text"},
        {"enc": "none", "epoch": 0, "data": b"", "nonce": b"\x00" * 12},
        {"enc": "aes256gcm", "epoch": 1, "data": b"", "nonce": b"\x00" * 12},
        {"enc": "x25519-aes256gcm", "epoch": 1, "data": b""},
        {
            "enc": "x25519-aes256gcm",
            "epoch": 1,
            "data": b"",
            "ephemeral": b"\x05" * 32,
            "keys": [[b"\x01" * 8]],
            "nonce": b"\x06" * 12,
        },
        {
            "enc": "x25519-aes256gcm",
            "epoch": 1,
            "data": b"",
            "ephemeral": b"\x05" * 32,
            "keys": b"",
            "nonce": b"\x06" * 12,
        },
    ],
)
def test_decode_rejects_malformed(fields: dict[str, object]) -> None:
    with pytest.raises(EnvelopeError):
        Envelope.decode(encode(fields))  # type: ignore[arg-type]  # malformed on purpose


def test_decode_rejects_non_canonical() -> None:
    with pytest.raises(EnvelopeError):
        Envelope.decode(b"\xff")


def test_aad_separates_channels_and_authors() -> None:
    base = body_aad(0, b"\x02" * 33, b"\x01" * 16, "#a", "message")
    assert base != body_aad(0, b"\x02" * 33, b"\x01" * 16, "#b", "message")
    assert base != body_aad(0, b"\x03" * 33, b"\x01" * 16, "#a", "message")
    assert base != body_aad(0, b"\x02" * 33, b"\x01" * 16, "#a", "status")

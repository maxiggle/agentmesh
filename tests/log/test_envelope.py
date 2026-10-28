import pytest

from agentmesh.log.canonical import encode
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError


def test_plain_round_trip() -> None:
    envelope = Envelope.plain(b"data")
    assert Envelope.decode(envelope.encode()) == envelope


def test_encrypted_round_trip() -> None:
    envelope = Envelope(Encryption.AES256GCM, 3, b"cipher", b"\x00" * 12)
    assert Envelope.decode(envelope.encode()) == envelope


@pytest.mark.parametrize(
    ("enc", "epoch", "nonce"),
    [
        (Encryption.NONE, 1, None),
        (Encryption.NONE, 0, b"\x00" * 12),
        (Encryption.AES256GCM, 1, None),
        (Encryption.AES256GCM, 1, b"\x00" * 11),
        (Encryption.AES256GCM, -1, b"\x00" * 12),
    ],
)
def test_rejects_inconsistent_fields(enc: Encryption, epoch: int, nonce: bytes | None) -> None:
    with pytest.raises(EnvelopeError):
        Envelope(enc, epoch, b"data", nonce)


@pytest.mark.parametrize(
    "fields",
    [
        {"enc": "none", "epoch": 0},
        {"enc": "rot13", "epoch": 0, "data": b""},
        {"enc": "none", "epoch": True, "data": b""},
        {"enc": "none", "epoch": 0, "data": "text"},
        {"enc": "none", "epoch": 0, "data": b"", "nonce": None},
        {"enc": "none", "epoch": 0, "data": b"", "extra": 1},
    ],
)
def test_decode_rejects_malformed(fields: dict[str, object]) -> None:
    with pytest.raises(EnvelopeError):
        Envelope.decode(encode(fields))  # type: ignore[arg-type]  # malformed on purpose


def test_decode_rejects_non_canonical() -> None:
    with pytest.raises(EnvelopeError):
        Envelope.decode(b"\xff")

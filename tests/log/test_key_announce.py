import pytest

from agentmesh.log.canonical import encode
from agentmesh.log.key_announce import KeyAnnounce, KeyAnnounceError


def test_round_trip() -> None:
    announce = KeyAnnounce(b"\x05" * 32)
    assert KeyAnnounce.decode(announce.encode()) == announce


@pytest.mark.parametrize(
    "raw",
    [
        encode({"x25519": b"\x05" * 31}),
        encode({"x25519": "key"}),
        encode({"x25519": b"\x05" * 32, "extra": 1}),
        encode([b"\x05" * 32]),
        b"\xff",
    ],
)
def test_rejects_malformed(raw: bytes) -> None:
    with pytest.raises(KeyAnnounceError):
        KeyAnnounce.decode(raw)

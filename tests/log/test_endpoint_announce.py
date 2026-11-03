import pytest

from agentmesh.log.canonical import encode
from agentmesh.log.endpoint_announce import EndpointAnnounce, EndpointAnnounceError


def test_round_trip() -> None:
    announce = EndpointAnnounce(b"\x07" * 32)
    assert EndpointAnnounce.decode(announce.encode()) == announce


@pytest.mark.parametrize(
    "raw",
    [
        encode({"iroh": b"\x07" * 31}),
        encode({"iroh": b"\x07" * 33}),
        encode({"iroh": "node"}),
        encode({"iroh": b"\x07" * 32, "extra": 1}),
        encode({}),
        encode({"x25519": b"\x07" * 32}),
        encode([b"\x07" * 32]),
        b"\xff",
    ],
)
def test_rejects_malformed(raw: bytes) -> None:
    with pytest.raises(EndpointAnnounceError):
        EndpointAnnounce.decode(raw)

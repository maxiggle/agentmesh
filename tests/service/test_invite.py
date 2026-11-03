import base64

import pytest

from agentmesh.log.canonical import CborValue, encode
from agentmesh.service.invite import Invite, InviteError

TEAM = b"\x01" * 16
GENESIS = b"\x02" * 32
IROH = b"\x03" * 32
INVITE = Invite(TEAM, GENESIS)


def _am2(fields: dict[str, CborValue]) -> str:
    return "am2-" + base64.urlsafe_b64encode(encode(fields)).decode().rstrip("=")


def _am1(relay: bytes = b"") -> str:
    return "am1-" + base64.urlsafe_b64encode(TEAM + GENESIS + relay).decode().rstrip("=")


def test_round_trip() -> None:
    code = INVITE.encode()
    assert code.startswith("am2-")
    assert "=" not in code
    assert Invite.decode(f"  {code}\n") == INVITE


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("xx1-AAAA", "start with"),
        ("am1-!!!!", "base64"),
        ("am1-AAAA", "too short"),
        ("am2-!!!!", "base64"),
        ("am2-AAAA", "malformed"),
    ],
)
def test_rejects_bad_codes(code: str, error: str) -> None:
    with pytest.raises(InviteError, match=error):
        Invite.decode(code)


def test_relay_round_trip() -> None:
    invite = Invite(TEAM, GENESIS, "relay.example.com:7447")
    assert Invite.decode(invite.encode()) == invite
    assert len(INVITE.encode()) < len(invite.encode())


def test_iroh_endpoint_round_trip() -> None:
    for relay in (None, "relay.example.com:7447"):
        invite = Invite(TEAM, GENESIS, relay, IROH)
        assert Invite.decode(invite.encode()) == invite
    assert Invite.decode(INVITE.encode()).iroh is None


def test_rejects_bad_relays() -> None:
    with pytest.raises(InviteError, match="host:port"):
        Invite(TEAM, GENESIS, "no-port")
    with pytest.raises(InviteError, match="host:port"):
        Invite.decode(_am2({"team": TEAM, "genesis": GENESIS, "relay": "nope"}))
    with pytest.raises(InviteError, match="not text"):
        Invite.decode(_am2({"team": TEAM, "genesis": GENESIS, "relay": b"x:1"}))
    raw = base64.urlsafe_b64encode(b"\x01" * 48 + b"\xff\xfe").decode().rstrip("=")
    with pytest.raises(InviteError, match="not text"):
        Invite.decode("am1-" + raw)
    raw = base64.urlsafe_b64encode(b"\x01" * 48 + b"nope").decode().rstrip("=")
    with pytest.raises(InviteError, match="host:port"):
        Invite.decode("am1-" + raw)


def test_legacy_codes_still_decode() -> None:
    assert Invite.decode(_am1()) == INVITE
    assert Invite.decode(_am1(b"relay.example.com:7447")) == Invite(
        TEAM, GENESIS, "relay.example.com:7447"
    )
    assert Invite.decode(_am1()).encode().startswith("am2-")


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"team": TEAM, "genesis": GENESIS, "extra": b""}, "unknown field 'extra'"),
        ({"team": TEAM[:-1], "genesis": GENESIS}, "team id"),
        ({"team": TEAM, "genesis": GENESIS + b"\x00"}, "genesis roster id"),
        ({"team": TEAM, "genesis": GENESIS, "iroh": IROH[:-1]}, "iroh endpoint must be"),
        ({"team": TEAM, "genesis": GENESIS, "iroh": "text"}, "not bytes"),
        ({"team": TEAM}, "needs a team"),
        ({"team": "text", "genesis": GENESIS}, "needs a team"),
    ],
)
def test_rejects_malformed_fields(fields: dict[str, CborValue], error: str) -> None:
    with pytest.raises(InviteError, match=error):
        Invite.decode(_am2(fields))


def test_rejects_a_non_map_and_non_canonical_cbor() -> None:
    body = base64.urlsafe_b64encode(encode([TEAM])).decode().rstrip("=")
    with pytest.raises(InviteError, match="expected a map"):
        Invite.decode("am2-" + body)
    loose = b"\xb8\x02\x64team\x50" + TEAM + b"\x67genesis\x58\x20" + GENESIS
    with pytest.raises(InviteError, match="malformed"):
        Invite.decode("am2-" + base64.urlsafe_b64encode(loose).decode().rstrip("="))


def test_rejects_wrong_sizes_when_constructed() -> None:
    with pytest.raises(InviteError, match="iroh endpoint"):
        Invite(TEAM, GENESIS, iroh=b"short")

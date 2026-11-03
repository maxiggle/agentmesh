import base64

import pytest

from ehor.log.canonical import CborValue, encode
from ehor.service.invite import Invite, InviteError

TEAM = b"\x01" * 16
GENESIS = b"\x02" * 32
IROH = b"\x03" * 32
INVITE = Invite(TEAM, GENESIS)


def _code(fields: dict[str, CborValue]) -> str:
    return "eh1-" + base64.urlsafe_b64encode(encode(fields)).decode().rstrip("=")


def test_round_trip() -> None:
    code = INVITE.encode()
    assert code.startswith("eh1-")
    assert "=" not in code
    assert Invite.decode(f"  {code}\n") == INVITE


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("xx1-AAAA", "start with"),
        ("am2-AAAA", "start with"),
        ("eh1-!!!!", "base64"),
        ("eh1-AAAA", "malformed"),
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
        Invite.decode(_code({"team": TEAM, "genesis": GENESIS, "relay": "nope"}))
    with pytest.raises(InviteError, match="not text"):
        Invite.decode(_code({"team": TEAM, "genesis": GENESIS, "relay": b"x:1"}))


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
        Invite.decode(_code(fields))


def test_rejects_a_non_map_and_non_canonical_cbor() -> None:
    body = base64.urlsafe_b64encode(encode([TEAM])).decode().rstrip("=")
    with pytest.raises(InviteError, match="expected a map"):
        Invite.decode("eh1-" + body)
    loose = b"\xb8\x02\x64team\x50" + TEAM + b"\x67genesis\x58\x20" + GENESIS
    with pytest.raises(InviteError, match="malformed"):
        Invite.decode("eh1-" + base64.urlsafe_b64encode(loose).decode().rstrip("="))


def test_rejects_wrong_sizes_when_constructed() -> None:
    with pytest.raises(InviteError, match="iroh endpoint"):
        Invite(TEAM, GENESIS, iroh=b"short")

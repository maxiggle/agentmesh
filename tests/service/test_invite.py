import base64

import pytest

from agentmesh.service.invite import Invite, InviteError

INVITE = Invite(b"\x01" * 16, b"\x02" * 32)


def test_round_trip() -> None:
    code = INVITE.encode()
    assert code.startswith("am1-")
    assert "=" not in code
    assert Invite.decode(f"  {code}\n") == INVITE


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("xx1-AAAA", "start with"),
        ("am1-!!!!", "base64"),
        ("am1-AAAA", "too short"),
    ],
)
def test_rejects_bad_codes(code: str, error: str) -> None:
    with pytest.raises(InviteError, match=error):
        Invite.decode(code)


def test_relay_round_trip() -> None:
    invite = Invite(b"\x01" * 16, b"\x02" * 32, "relay.example.com:7447")
    assert Invite.decode(invite.encode()) == invite
    assert len(Invite(b"\x01" * 16, b"\x02" * 32).encode()) < len(invite.encode())


def test_rejects_bad_relays() -> None:
    with pytest.raises(InviteError, match="host:port"):
        Invite(b"\x01" * 16, b"\x02" * 32, "no-port")
    raw = base64.urlsafe_b64encode(b"\x01" * 48 + b"\xff\xfe").decode().rstrip("=")
    with pytest.raises(InviteError, match="not text"):
        Invite.decode("am1-" + raw)
    raw = base64.urlsafe_b64encode(b"\x01" * 48 + b"nope").decode().rstrip("=")
    with pytest.raises(InviteError, match="host:port"):
        Invite.decode("am1-" + raw)

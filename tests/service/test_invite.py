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
        ("am1-AAAA", "length"),
    ],
)
def test_rejects_bad_codes(code: str, error: str) -> None:
    with pytest.raises(InviteError, match=error):
        Invite.decode(code)

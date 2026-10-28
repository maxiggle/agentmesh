import base64
from dataclasses import dataclass

from agentmesh.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE

PREFIX = "am1-"


class InviteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Invite:
    """What a new device needs to trust: the team id and the genesis roster it must pin."""

    team: bytes
    genesis: bytes

    def encode(self) -> str:
        raw = self.team + self.genesis
        return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, code: str) -> "Invite":
        text = code.strip()
        if not text.startswith(PREFIX):
            raise InviteError(f"invite codes start with {PREFIX}")
        body = text[len(PREFIX) :]
        try:
            raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
        except ValueError as exc:
            raise InviteError("invite code is not valid base64") from exc
        if len(raw) != TEAM_ID_SIZE + ENTRY_ID_SIZE:
            raise InviteError("invite code has the wrong length")
        return cls(raw[:TEAM_ID_SIZE], raw[TEAM_ID_SIZE:])

import base64
from dataclasses import dataclass

from agentmesh.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE
from agentmesh.log.names import parse_address

PREFIX = "am1-"


class InviteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Invite:
    """What a new device needs: the team id, the genesis roster it must pin, and
    optionally a relay to reach the team through before it shares a network with anyone."""

    team: bytes
    genesis: bytes
    relay: str | None = None

    def __post_init__(self) -> None:
        if self.relay is not None and parse_address(self.relay) is None:
            raise InviteError(f"relay must be host:port, got {self.relay!r}")

    def encode(self) -> str:
        raw = self.team + self.genesis + (self.relay or "").encode()
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
        fixed = TEAM_ID_SIZE + ENTRY_ID_SIZE
        if len(raw) < fixed:
            raise InviteError("invite code is too short")
        try:
            relay = raw[fixed:].decode() or None
        except UnicodeDecodeError as exc:
            raise InviteError("invite relay address is not text") from exc
        return cls(raw[:TEAM_ID_SIZE], raw[TEAM_ID_SIZE:fixed], relay)

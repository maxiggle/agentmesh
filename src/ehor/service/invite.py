import base64
import binascii
from dataclasses import dataclass

from ehor.log.canonical import CanonicalError, CborValue
from ehor.log.canonical import decode as cbor_decode
from ehor.log.canonical import encode as cbor_encode
from ehor.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE
from ehor.log.names import parse_address

PREFIX = "am2-"
LEGACY_PREFIX = "am1-"
IROH_ENDPOINT_SIZE = 32
_KEYS = frozenset({"team", "genesis", "relay", "iroh"})


class InviteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Invite:
    """What a new device needs: the team id, the genesis roster it must pin, and
    optionally where to knock before it shares a network with anyone: a relay, or
    the iroh endpoint of the device that issued the invite."""

    team: bytes
    genesis: bytes
    relay: str | None = None
    iroh: bytes | None = None

    def __post_init__(self) -> None:
        if len(self.team) != TEAM_ID_SIZE:
            raise InviteError(f"team id must be {TEAM_ID_SIZE} bytes")
        if len(self.genesis) != ENTRY_ID_SIZE:
            raise InviteError(f"genesis roster id must be {ENTRY_ID_SIZE} bytes")
        if self.relay is not None and parse_address(self.relay) is None:
            raise InviteError(f"relay must be host:port, got {self.relay!r}")
        if self.iroh is not None and len(self.iroh) != IROH_ENDPOINT_SIZE:
            raise InviteError(f"iroh endpoint must be {IROH_ENDPOINT_SIZE} bytes")

    def encode(self) -> str:
        fields: dict[str, CborValue] = {"team": self.team, "genesis": self.genesis}
        if self.relay is not None:
            fields["relay"] = self.relay
        if self.iroh is not None:
            fields["iroh"] = self.iroh
        return PREFIX + base64.urlsafe_b64encode(cbor_encode(fields)).decode().rstrip("=")

    @classmethod
    def decode(cls, code: str) -> "Invite":
        text = code.strip()
        if text.startswith(PREFIX):
            return cls._decode_current(_unbase64(text[len(PREFIX) :]))
        if text.startswith(LEGACY_PREFIX):
            return cls._decode_legacy(_unbase64(text[len(LEGACY_PREFIX) :]))
        raise InviteError(f"invite codes start with {PREFIX}")

    @classmethod
    def _decode_current(cls, raw: bytes) -> "Invite":
        try:
            fields = cbor_decode(raw)
        except CanonicalError as exc:
            raise InviteError(f"invite code is malformed: {exc}") from exc
        if not isinstance(fields, dict):
            raise InviteError("invite code is malformed: expected a map")
        unknown = fields.keys() - _KEYS
        if unknown:
            raise InviteError(f"invite code has unknown field {sorted(unknown)[0]!r}")
        team, genesis = fields.get("team"), fields.get("genesis")
        relay, iroh = fields.get("relay"), fields.get("iroh")
        if not isinstance(team, bytes) or not isinstance(genesis, bytes):
            raise InviteError("invite code needs a team and a genesis roster")
        if relay is not None and not isinstance(relay, str):
            raise InviteError("invite relay address is not text")
        if iroh is not None and not isinstance(iroh, bytes):
            raise InviteError("invite iroh endpoint is not bytes")
        return cls(team, genesis, relay, iroh)

    @classmethod
    def _decode_legacy(cls, raw: bytes) -> "Invite":
        fixed = TEAM_ID_SIZE + ENTRY_ID_SIZE
        if len(raw) < fixed:
            raise InviteError("invite code is too short")
        try:
            relay = raw[fixed:].decode() or None
        except UnicodeDecodeError as exc:
            raise InviteError("invite relay address is not text") from exc
        return cls(raw[:TEAM_ID_SIZE], raw[TEAM_ID_SIZE:fixed], relay)


def _unbase64(body: str) -> bytes:
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InviteError("invite code is not valid base64") from exc

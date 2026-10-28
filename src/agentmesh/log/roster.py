from dataclasses import dataclass, replace
from enum import StrEnum

from agentmesh.identity.es256 import PUBLIC_KEY_SIZE
from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode
from agentmesh.log.entry import ENTRY_ID_SIZE
from agentmesh.log.names import HANDLE, ROLE, TEAM_CHANNEL

ALL_CHANNELS = "*"
TEAM_NAME_MAX = 64
LABEL_MAX = 64
DEVICES_MAX = 16


class RosterError(ValueError):
    pass


class Trust(StrEnum):
    HARDWARE = "hardware"
    SOFTWARE = "software"


@dataclass(frozen=True, slots=True)
class RosterDevice:
    key: bytes
    trust: Trust
    label: str

    def __post_init__(self) -> None:
        if len(self.key) != PUBLIC_KEY_SIZE:
            raise RosterError(f"device key must be {PUBLIC_KEY_SIZE} bytes")
        if len(self.label) > LABEL_MAX:
            raise RosterError(f"device label is longer than {LABEL_MAX} chars")

    def to_cbor(self) -> CborValue:
        return {"key": self.key, "trust": self.trust.value, "label": self.label}

    @classmethod
    def from_cbor(cls, value: CborValue) -> "RosterDevice":
        fields = _fields(value, "device", {"key", "trust", "label"})
        try:
            trust = Trust(_text(fields["trust"], "trust"))
        except ValueError as exc:
            raise RosterError(f"unknown trust {fields['trust']!r}") from exc
        return cls(_bytes(fields["key"], "key"), trust, _text(fields["label"], "label"))


@dataclass(frozen=True, slots=True)
class Member:
    handle: str
    roles: tuple[str, ...]
    channels: tuple[str, ...]
    devices: tuple[RosterDevice, ...]

    def __post_init__(self) -> None:
        if not HANDLE.fullmatch(self.handle):
            raise RosterError(f"invalid handle {self.handle!r}")
        if list(self.roles) != sorted(set(self.roles)):
            raise RosterError(f"roles of {self.handle} must be sorted and unique")
        for role in self.roles:
            if not ROLE.fullmatch(role):
                raise RosterError(f"invalid role {role!r}")
        if list(self.channels) != sorted(set(self.channels)):
            raise RosterError(f"channels of {self.handle} must be sorted and unique")
        for channel in self.channels:
            if channel != ALL_CHANNELS and not TEAM_CHANNEL.fullmatch(channel):
                raise RosterError(f"invalid channel {channel!r}")
        if not 1 <= len(self.devices) <= DEVICES_MAX:
            raise RosterError(f"{self.handle} needs 1-{DEVICES_MAX} devices")
        keys = [device.key for device in self.devices]
        if keys != sorted(set(keys)):
            raise RosterError(f"devices of {self.handle} must be sorted by key and unique")

    def can_use(self, channel: str) -> bool:
        return ALL_CHANNELS in self.channels or channel in self.channels

    def to_cbor(self) -> CborValue:
        return {
            "handle": self.handle,
            "roles": list(self.roles),
            "channels": list(self.channels),
            "devices": [device.to_cbor() for device in self.devices],
        }

    @classmethod
    def from_cbor(cls, value: CborValue) -> "Member":
        fields = _fields(value, "member", {"handle", "roles", "channels", "devices"})
        return cls(
            handle=_text(fields["handle"], "handle"),
            roles=_texts(fields["roles"], "roles"),
            channels=_texts(fields["channels"], "channels"),
            devices=tuple(RosterDevice.from_cbor(d) for d in _list(fields["devices"], "devices")),
        )


@dataclass(frozen=True, slots=True)
class Roster:
    version: int
    previous: bytes | None
    team_name: str
    admins: tuple[str, ...]
    members: tuple[Member, ...]
    require_encryption: bool = False

    def __post_init__(self) -> None:
        if self.version < 1:
            raise RosterError("version starts at 1")
        if (self.previous is None) != (self.version == 1):
            raise RosterError("previous must be null exactly when version is 1")
        if self.previous is not None and len(self.previous) != ENTRY_ID_SIZE:
            raise RosterError(f"previous must be a {ENTRY_ID_SIZE}-byte entry id")
        if len(self.team_name) > TEAM_NAME_MAX:
            raise RosterError(f"team name is longer than {TEAM_NAME_MAX} chars")
        handles = [member.handle for member in self.members]
        if handles != sorted(set(handles)):
            raise RosterError("members must be sorted by handle and unique")
        keys = [device.key for member in self.members for device in member.devices]
        if len(keys) != len(set(keys)):
            raise RosterError("a device key appears under two members")
        if not self.admins or list(self.admins) != sorted(set(self.admins)):
            raise RosterError("admins must be a non-empty sorted list without repeats")
        if not set(self.admins) <= set(handles):
            raise RosterError("every admin must be a member")

    def member(self, handle: str) -> Member | None:
        return next((m for m in self.members if m.handle == handle), None)

    def member_for_key(self, key: bytes) -> Member | None:
        return next(
            (m for m in self.members if any(device.key == key for device in m.devices)), None
        )

    def is_admin_device(self, key: bytes) -> bool:
        member = self.member_for_key(key)
        return member is not None and member.handle in self.admins

    def successor(
        self,
        previous_id: bytes,
        *,
        members: tuple[Member, ...] | None = None,
        admins: tuple[str, ...] | None = None,
    ) -> "Roster":
        return replace(
            self,
            version=self.version + 1,
            previous=previous_id,
            members=tuple(sorted(members, key=lambda m: m.handle))
            if members is not None
            else self.members,
            admins=tuple(sorted(admins)) if admins is not None else self.admins,
        )

    def encode(self) -> bytes:
        return encode(
            {
                "version": self.version,
                "previous": self.previous,
                "team_name": self.team_name,
                "admins": list(self.admins),
                "members": [member.to_cbor() for member in self.members],
                "require_encryption": self.require_encryption,
            }
        )

    @classmethod
    def decode(cls, raw: bytes) -> "Roster":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise RosterError(str(exc)) from exc
        fields = _fields(
            value,
            "roster",
            {"version", "previous", "team_name", "admins", "members", "require_encryption"},
        )
        version = fields["version"]
        previous = fields["previous"]
        require_encryption = fields["require_encryption"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise RosterError("version must be an integer")
        if previous is not None and not isinstance(previous, bytes):
            raise RosterError("previous must be bytes or null")
        if not isinstance(require_encryption, bool):
            raise RosterError("require_encryption must be a boolean")
        return cls(
            version=version,
            previous=previous,
            team_name=_text(fields["team_name"], "team_name"),
            admins=_texts(fields["admins"], "admins"),
            members=tuple(Member.from_cbor(m) for m in _list(fields["members"], "members")),
            require_encryption=require_encryption,
        )


def _fields(value: CborValue, name: str, expected: set[str]) -> dict[str, CborValue]:
    if not isinstance(value, dict) or value.keys() != expected:
        raise RosterError(f"{name} fields must be exactly {sorted(expected)}")
    return value


def _list(value: CborValue, name: str) -> list[CborValue]:
    if not isinstance(value, list):
        raise RosterError(f"{name} must be an array")
    return value


def _text(value: CborValue, name: str) -> str:
    if not isinstance(value, str):
        raise RosterError(f"{name} must be text")
    return value


def _texts(value: CborValue, name: str) -> tuple[str, ...]:
    return tuple(_text(item, f"{name}[]") for item in _list(value, name))


def _bytes(value: CborValue, name: str) -> bytes:
    if not isinstance(value, bytes):
        raise RosterError(f"{name} must be bytes")
    return value

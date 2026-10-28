import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agentmesh.log.entry import TEAM_ID_SIZE
from agentmesh.log.names import HANDLE, ROLE, TEAM_CHANNEL
from agentmesh.service.messenger import ALL_CHANNELS, Identity

CONFIG_FILE = "config.json"
HOME_ENV = "AGENTMESH_HOME"


class ConfigError(Exception):
    pass


def default_home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override) if override else Path.home() / ".agentmesh"


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    handle: str
    team: bytes
    key_label: str = "device"
    roles: tuple[str, ...] = ()
    channels: tuple[str, ...] = (ALL_CHANNELS,)
    exchange: Path | None = field(default=None)

    def __post_init__(self) -> None:
        if not HANDLE.fullmatch(self.handle):
            raise ConfigError(f"invalid handle {self.handle!r}")
        if len(self.team) != TEAM_ID_SIZE:
            raise ConfigError(f"team id must be {TEAM_ID_SIZE} bytes")
        for role in self.roles:
            if not ROLE.fullmatch(role):
                raise ConfigError(f"invalid role {role!r}, expected @name")
        for channel in self.channels:
            if channel != ALL_CHANNELS and not TEAM_CHANNEL.fullmatch(channel):
                raise ConfigError(f"invalid channel {channel!r}, expected #name")

    @property
    def identity(self) -> Identity:
        return Identity(self.handle, self.roles, self.channels)

    def save(self, home: Path) -> None:
        data = {
            "handle": self.handle,
            "team": self.team.hex(),
            "key_label": self.key_label,
            "roles": list(self.roles),
            "channels": list(self.channels),
            "exchange": None if self.exchange is None else str(self.exchange),
        }
        (home / CONFIG_FILE).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "DeviceConfig":
        path = home / CONFIG_FILE
        try:
            data = json.loads(path.read_text())
            exchange = data.get("exchange")
            return cls(
                handle=data["handle"],
                team=bytes.fromhex(data["team"]),
                key_label=data["key_label"],
                roles=tuple(data.get("roles", ())),
                channels=tuple(data.get("channels", (ALL_CHANNELS,))),
                exchange=None if exchange is None else Path(exchange),
            )
        except FileNotFoundError as exc:
            raise ConfigError(f"no device at {home}; run `agentmesh init` first") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigError(f"invalid {path}: {exc}") from exc

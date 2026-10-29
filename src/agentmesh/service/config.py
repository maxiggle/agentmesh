import json
import os
from dataclasses import dataclass
from pathlib import Path

from agentmesh.identity.backends import Backend
from agentmesh.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE
from agentmesh.log.names import HANDLE, parse_address

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
    genesis: bytes
    key_label: str = "device"
    key_backend: Backend = Backend.SOFTWARE
    exchange: Path | None = None
    relay: str | None = None

    def __post_init__(self) -> None:
        if not HANDLE.fullmatch(self.handle):
            raise ConfigError(f"invalid handle {self.handle!r}")
        if len(self.team) != TEAM_ID_SIZE:
            raise ConfigError(f"team id must be {TEAM_ID_SIZE} bytes")
        if len(self.genesis) != ENTRY_ID_SIZE:
            raise ConfigError(f"genesis roster id must be {ENTRY_ID_SIZE} bytes")
        if self.relay is not None and parse_address(self.relay) is None:
            raise ConfigError(f"relay must be host:port, got {self.relay!r}")

    def save(self, home: Path) -> None:
        data = {
            "handle": self.handle,
            "team": self.team.hex(),
            "genesis": self.genesis.hex(),
            "key_label": self.key_label,
            "key_backend": self.key_backend.value,
            "exchange": None if self.exchange is None else str(self.exchange),
            "relay": self.relay,
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
                genesis=bytes.fromhex(data["genesis"]),
                key_label=data["key_label"],
                key_backend=Backend(data.get("key_backend", Backend.SOFTWARE)),
                exchange=None if exchange is None else Path(exchange),
                relay=data.get("relay"),
            )
        except FileNotFoundError as exc:
            raise ConfigError(f"no device at {home}; run `agentmesh init` first") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigError(f"invalid {path}: {exc}") from exc

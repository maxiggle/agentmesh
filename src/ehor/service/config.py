import json
import os
from dataclasses import dataclass
from pathlib import Path

from ehor.identity.backends import Backend
from ehor.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE
from ehor.log.names import HANDLE, HANDLE_RULE, parse_address
from ehor.service.notify import NotifyError, PhoneTarget

CONFIG_FILE = "config.json"
ENDPOINT_SIZE = 32
HOME_ENV = "EHOR_HOME"


class ConfigError(Exception):
    pass


def default_home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override) if override else Path.home() / ".ehor"


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    handle: str
    team: bytes
    genesis: bytes
    key_label: str = "device"
    key_backend: Backend = Backend.SOFTWARE
    exchange: Path | None = None
    relay: str | None = None
    bootstrap_endpoint: bytes | None = None
    notifications: bool = True
    phone: str | None = None

    def __post_init__(self) -> None:
        if not HANDLE.fullmatch(self.handle):
            raise ConfigError(f"invalid handle {self.handle!r}: {HANDLE_RULE}")
        if len(self.team) != TEAM_ID_SIZE:
            raise ConfigError(f"team id must be {TEAM_ID_SIZE} bytes")
        if len(self.genesis) != ENTRY_ID_SIZE:
            raise ConfigError(f"genesis roster id must be {ENTRY_ID_SIZE} bytes")
        if self.relay is not None and parse_address(self.relay) is None:
            raise ConfigError(f"relay must be host:port, got {self.relay!r}")
        if self.bootstrap_endpoint is not None and len(self.bootstrap_endpoint) != ENDPOINT_SIZE:
            raise ConfigError(f"bootstrap endpoint must be {ENDPOINT_SIZE} bytes")
        if not isinstance(self.notifications, bool):
            raise ConfigError("notifications must be true or false")
        if self.phone is not None:
            try:
                PhoneTarget(self.phone)
            except NotifyError as exc:
                raise ConfigError(str(exc)) from exc

    def save(self, home: Path) -> None:
        data = {
            "handle": self.handle,
            "team": self.team.hex(),
            "genesis": self.genesis.hex(),
            "key_label": self.key_label,
            "key_backend": self.key_backend.value,
            "exchange": None if self.exchange is None else str(self.exchange),
            "relay": self.relay,
            "bootstrap_endpoint": (
                None if self.bootstrap_endpoint is None else self.bootstrap_endpoint.hex()
            ),
            "notifications": self.notifications,
            "phone": self.phone,
        }
        (home / CONFIG_FILE).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "DeviceConfig":
        path = home / CONFIG_FILE
        try:
            data = json.loads(path.read_text())
            exchange = data.get("exchange")
            bootstrap = data.get("bootstrap_endpoint")
            return cls(
                handle=data["handle"],
                team=bytes.fromhex(data["team"]),
                genesis=bytes.fromhex(data["genesis"]),
                key_label=data["key_label"],
                key_backend=Backend(data.get("key_backend", Backend.SOFTWARE)),
                exchange=None if exchange is None else Path(exchange),
                relay=data.get("relay"),
                bootstrap_endpoint=None if bootstrap is None else bytes.fromhex(bootstrap),
                notifications=data.get("notifications", True),
                phone=data.get("phone"),
            )
        except FileNotFoundError as exc:
            raise ConfigError(f"no device at {home}; run `ehor init` first") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigError(f"invalid {path}: {exc}") from exc

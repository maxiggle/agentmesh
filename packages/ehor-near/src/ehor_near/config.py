import json
import re
from dataclasses import dataclass
from pathlib import Path

from ehor_near.client import TESTNET, TESTNET_RPC, is_account_id

NEAR_CONFIG_FILE = "near.json"
_COMPRESSED_P256 = re.compile(r"0[23][0-9a-f]{64}")


class NearConfigError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class NearConfig:
    account_id: str
    contract_id: str
    network: str = TESTNET
    rpc_url: str = TESTNET_RPC
    release_key: str | None = None

    def __post_init__(self) -> None:
        if self.network != TESTNET:
            raise NearConfigError(f"near network must be {TESTNET}, got {self.network!r}")
        for name, account in (("account", self.account_id), ("contract", self.contract_id)):
            if not is_account_id(account):
                raise NearConfigError(f"invalid NEAR {name} id {account!r}")
        if not self.rpc_url.startswith("https://"):
            raise NearConfigError(f"near rpc must be an https URL, got {self.rpc_url!r}")
        if self.release_key is not None and _COMPRESSED_P256.fullmatch(self.release_key) is None:
            raise NearConfigError("near release key must be a compressed P-256 key in hex")

    def to_json(self) -> dict[str, str]:
        data = {
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "network": self.network,
            "rpc_url": self.rpc_url,
        }
        if self.release_key is not None:
            data["release_key"] = self.release_key
        return data

    @classmethod
    def from_json(cls, data: dict[str, str]) -> "NearConfig":
        return cls(
            account_id=data["account_id"],
            contract_id=data["contract_id"],
            network=data.get("network", TESTNET),
            rpc_url=data.get("rpc_url", TESTNET_RPC),
            release_key=data.get("release_key"),
        )

    def save(self, home: Path) -> None:
        (home / NEAR_CONFIG_FILE).write_text(json.dumps(self.to_json(), indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "NearConfig | None":
        """Return this device's NEAR settings, or None before `ehor near setup` has run."""
        path = home / NEAR_CONFIG_FILE
        try:
            return cls.from_json(json.loads(path.read_text()))
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError) as exc:
            raise NearConfigError(f"invalid {path}: {exc}") from exc

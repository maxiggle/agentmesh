import json
from pathlib import Path

import pytest

from ehor.escrow.client import TESTNET_RPC
from ehor.service.config import ConfigError, DeviceConfig, NearConfig

TEAM = b"\x09" * 16
GENESIS = b"\x07" * 32
NEAR = NearConfig("gef.testnet", "escrow.gef.testnet")


def _rewrite(home: Path, **changes: object) -> None:
    path = home / "config.json"
    data = json.loads(path.read_text())
    for key, value in changes.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    path.write_text(json.dumps(data))


def test_near_defaults_to_testnet() -> None:
    assert NEAR.network == "testnet"
    assert NEAR.rpc_url == TESTNET_RPC


def test_config_keeps_the_near_section(tmp_path: Path) -> None:
    config = DeviceConfig("gef", TEAM, GENESIS, near=NEAR)
    config.save(tmp_path)
    saved = json.loads((tmp_path / "config.json").read_text())["near"]
    assert saved == {
        "account_id": "gef.testnet",
        "contract_id": "escrow.gef.testnet",
        "network": "testnet",
        "rpc_url": TESTNET_RPC,
    }
    assert DeviceConfig.load(tmp_path) == config


def test_config_without_near_has_none(tmp_path: Path) -> None:
    DeviceConfig("gef", TEAM, GENESIS).save(tmp_path)
    assert DeviceConfig.load(tmp_path).near is None
    _rewrite(tmp_path, near=None)
    assert DeviceConfig.load(tmp_path).near is None


def test_near_section_without_network_or_rpc_uses_testnet(tmp_path: Path) -> None:
    DeviceConfig("gef", TEAM, GENESIS).save(tmp_path)
    _rewrite(tmp_path, near={"account_id": "gef.testnet", "contract_id": "escrow.gef.testnet"})
    assert DeviceConfig.load(tmp_path).near == NEAR


@pytest.mark.parametrize(
    ("near", "error"),
    [
        ({"account_id": "gef.testnet"}, "contract_id"),
        ({**NEAR.to_json(), "network": "mainnet"}, "must be testnet"),
        ({**NEAR.to_json(), "account_id": "Gef"}, "invalid NEAR account"),
        ("gef.testnet", "invalid"),
    ],
)
def test_load_rejects_a_broken_near_section(tmp_path: Path, near: object, error: str) -> None:
    DeviceConfig("gef", TEAM, GENESIS).save(tmp_path)
    _rewrite(tmp_path, near=near)
    with pytest.raises(ConfigError, match=error):
        DeviceConfig.load(tmp_path)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"network": "mainnet"}, "must be testnet"),
        ({"account_id": "gef..testnet"}, "invalid NEAR account id"),
        ({"contract_id": "Escrow"}, "invalid NEAR contract id"),
        ({"rpc_url": "http://rpc.testnet.near.org"}, "https"),
    ],
)
def test_near_rejects_bad_values(kwargs: dict[str, str], error: str) -> None:
    fields = {"account_id": "gef.testnet", "contract_id": "escrow.gef.testnet", **kwargs}
    with pytest.raises(ConfigError, match=error):
        NearConfig(**fields)

import json
from pathlib import Path

import pytest

from ehor.escrow.client import TESTNET_RPC
from ehor.escrow.config import NEAR_CONFIG_FILE, NearConfig, NearConfigError

NEAR = NearConfig("gef.testnet", "escrow.gef.testnet")


def test_near_defaults_to_testnet() -> None:
    assert NEAR.network == "testnet"
    assert NEAR.rpc_url == TESTNET_RPC


def test_saves_and_loads_its_own_file(tmp_path: Path) -> None:
    NEAR.save(tmp_path)
    assert json.loads((tmp_path / NEAR_CONFIG_FILE).read_text()) == {
        "account_id": "gef.testnet",
        "contract_id": "escrow.gef.testnet",
        "network": "testnet",
        "rpc_url": TESTNET_RPC,
    }
    assert NearConfig.load(tmp_path) == NEAR


def test_a_device_without_near_has_none(tmp_path: Path) -> None:
    assert NearConfig.load(tmp_path) is None


def test_a_file_without_network_or_rpc_uses_testnet(tmp_path: Path) -> None:
    (tmp_path / NEAR_CONFIG_FILE).write_text(
        json.dumps({"account_id": "gef.testnet", "contract_id": "escrow.gef.testnet"})
    )
    assert NearConfig.load(tmp_path) == NEAR


@pytest.mark.parametrize(
    ("near", "error"),
    [
        ({"account_id": "gef.testnet"}, "contract_id"),
        ({**NEAR.to_json(), "network": "mainnet"}, "must be testnet"),
        ({**NEAR.to_json(), "account_id": "Gef"}, "invalid NEAR account"),
        ("gef.testnet", "invalid"),
    ],
)
def test_load_rejects_a_broken_file(tmp_path: Path, near: object, error: str) -> None:
    (tmp_path / NEAR_CONFIG_FILE).write_text(json.dumps(near))
    with pytest.raises(NearConfigError, match=error):
        NearConfig.load(tmp_path)


def test_load_rejects_a_file_that_is_not_json(tmp_path: Path) -> None:
    (tmp_path / NEAR_CONFIG_FILE).write_text("{")
    with pytest.raises(NearConfigError, match="invalid"):
        NearConfig.load(tmp_path)


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
    with pytest.raises(NearConfigError, match=error):
        NearConfig(**fields)

import json
from pathlib import Path

import pytest

from ehor.escrow.credentials import CredentialsError, load_account_key

KEY = "ed25519:" + "4" * 60


def _save(root: Path, account: str, data: object) -> Path:
    path = root / "testnet" / f"{account}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_a_cargo_near_file_without_account_id(tmp_path: Path) -> None:
    _save(tmp_path, "alice.testnet", {"public_key": "ed25519:x", "private_key": KEY})
    assert load_account_key("alice.testnet", tmp_path) == KEY


def test_loads_a_near_cli_file_with_account_id(tmp_path: Path) -> None:
    _save(tmp_path, "alice.testnet", {"account_id": "alice.testnet", "private_key": KEY})
    assert load_account_key("alice.testnet", tmp_path) == KEY


def test_a_file_for_another_account_is_refused(tmp_path: Path) -> None:
    _save(tmp_path, "alice.testnet", {"account_id": "mallory.testnet", "private_key": KEY})
    with pytest.raises(CredentialsError, match="another account"):
        load_account_key("alice.testnet", tmp_path)


def test_a_missing_file_says_how_to_fix_it(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="log in with NEAR CLI"):
        load_account_key("alice.testnet", tmp_path)


@pytest.mark.parametrize("data", [[], {"private_key": ""}, {"public_key": "x"}])
def test_a_file_without_a_key_is_refused(tmp_path: Path, data: object) -> None:
    _save(tmp_path, "alice.testnet", data)
    with pytest.raises(CredentialsError):
        load_account_key("alice.testnet", tmp_path)


def test_errors_never_include_the_key(tmp_path: Path) -> None:
    path = _save(tmp_path, "alice.testnet", {"private_key": KEY})
    path.write_text('{"private_key": "' + KEY + '"', encoding="utf-8")
    with pytest.raises(CredentialsError) as caught:
        load_account_key("alice.testnet", tmp_path)
    assert KEY not in str(caught.value)
    assert caught.value.__cause__ is not None
    assert KEY not in str(caught.value.__cause__)


@pytest.mark.parametrize("account", ["../etc/passwd", "Alice.testnet", ""])
def test_bad_account_ids_are_refused_before_any_read(tmp_path: Path, account: str) -> None:
    with pytest.raises(CredentialsError, match="not a NEAR account id"):
        load_account_key(account, tmp_path)


def test_mainnet_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CredentialsError, match="only testnet"):
        load_account_key("alice.near", tmp_path, network="mainnet")

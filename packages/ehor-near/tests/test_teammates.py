from pathlib import Path

import pytest

from ehor_near.teammates import TEAMMATES_FILE, TeammateAccounts, TeammateError


def test_links_survive_a_save_and_load(tmp_path: Path) -> None:
    links = TeammateAccounts().linked("tobi", "tobi.testnet").linked("ada", "ada.testnet")
    links.save(tmp_path)
    loaded = TeammateAccounts.load(tmp_path)
    assert dict(loaded.accounts) == {"ada": "ada.testnet", "tobi": "tobi.testnet"}
    assert loaded.account_for("tobi") == "tobi.testnet"
    assert loaded.account_for("eve") is None


def test_no_file_means_no_links(tmp_path: Path) -> None:
    assert dict(TeammateAccounts.load(tmp_path).accounts) == {}


def test_relinking_an_account_moves_it_to_the_new_handle() -> None:
    links = TeammateAccounts().linked("tobi", "shared.testnet").linked("ada", "shared.testnet")
    assert dict(links.accounts) == {"ada": "shared.testnet"}


def test_unlinking(tmp_path: Path) -> None:
    links = TeammateAccounts().linked("tobi", "tobi.testnet").unlinked("tobi")
    assert dict(links.accounts) == {}
    with pytest.raises(TeammateError, match="no linked"):
        links.unlinked("tobi")


@pytest.mark.parametrize(
    ("handle", "account"),
    [("Tobi", "tobi.testnet"), ("@mobile", "tobi.testnet"), ("tobi", "Tobi.testnet"), ("tobi", "")],
)
def test_bad_handles_and_accounts_are_refused(handle: str, account: str) -> None:
    with pytest.raises(TeammateError):
        TeammateAccounts().linked(handle, account)


@pytest.mark.parametrize(
    "content", ["not json", '["tobi"]', '{"tobi": 3}', '{"Tobi": "t.testnet"}']
)
def test_a_tampered_file_is_refused(tmp_path: Path, content: str) -> None:
    (tmp_path / TEAMMATES_FILE).write_text(content)
    with pytest.raises(TeammateError):
        TeammateAccounts.load(tmp_path)

import pytest

from ehor_near.client import is_account_id


@pytest.mark.parametrize(
    "value",
    [
        "ab",
        "alice.testnet",
        "ehor-escrow.alice.testnet",
        "a_b-c.testnet",
        "98793cd91a3f870fb126f66285808c7e094afcfc4eda8a970f6648cdf0dbd6de",
        "a" * 64,
    ],
)
def test_accepts_valid_account_ids(value: str) -> None:
    assert is_account_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a",
        "a" * 65,
        "Alice.testnet",
        "alice..testnet",
        ".alice",
        "alice.",
        "alice-.testnet",
        "alice|testnet",
        "alice testnet",
    ],
)
def test_rejects_invalid_account_ids(value: str) -> None:
    assert not is_account_id(value)

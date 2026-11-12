import pytest

from ehor.log.account_announce import AccountAnnounce, AccountAnnounceError
from ehor.log.canonical import encode


@pytest.mark.parametrize(
    "announced",
    [
        AccountAnnounce("near-testnet", "tobi.testnet", b"\x02" * 33),
        AccountAnnounce("near-testnet", "tobi.testnet"),
        AccountAnnounce("x", "a" * 64, b"\x01"),
    ],
)
def test_round_trip(announced: AccountAnnounce) -> None:
    assert AccountAnnounce.decode(announced.encode()) == announced


def test_an_absent_key_is_left_out_of_the_body() -> None:
    assert AccountAnnounce("near-testnet", "t.testnet").encode() == encode(
        {"system": "near-testnet", "account": "t.testnet"}
    )


@pytest.mark.parametrize(
    ("system", "account", "key"),
    [
        ("Near", "t.testnet", None),
        ("-near", "t.testnet", None),
        ("n" * 33, "t.testnet", None),
        ("near", "", None),
        ("near", "a" * 65, None),
        ("near", "tobi\n.testnet", None),
        ("near", "t.testnet", b""),
        ("near", "t.testnet", b"\x01" * 66),
    ],
)
def test_bad_fields_are_refused(system: str, account: str, key: bytes | None) -> None:
    with pytest.raises(AccountAnnounceError):
        AccountAnnounce(system, account, key)


@pytest.mark.parametrize(
    "body",
    [
        encode({"system": "near"}),
        encode({"account": "t.testnet"}),
        encode({"system": "near", "account": "t.testnet", "extra": 1}),
        encode({"system": 1, "account": "t.testnet"}),
        encode({"system": "near", "account": "t.testnet", "key": "02aa"}),
        encode(["near", "t.testnet"]),
        b"\xff",
    ],
)
def test_bad_bodies_are_refused(body: bytes) -> None:
    with pytest.raises(AccountAnnounceError):
        AccountAnnounce.decode(body)

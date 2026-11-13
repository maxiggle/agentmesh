import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import pytest

from ehor_near.client import DEFAULT_GAS
from ehor_near.config import NearConfig
from ehor_near.pay import Bounty, PayError
from ehor_near.status import (
    PAGE,
    EscrowAccount,
    Status,
    bounties_posted_by,
    describe,
    fetch_account,
)

CONTRACT = "ehor-escrow.testnet"
ME = "ada.testnet"
KEY = b"\x02" + b"\x11" * 32
OTHER = b"\x03" + b"\x22" * 32
CONFIG = NearConfig(ME, CONTRACT)


def _bounty(bounty_id: int, sender: str = ME, status: str = "Open") -> dict[str, object]:
    return {
        "id": bounty_id,
        "sender": sender,
        "amount": str(10**23),
        "message_id": f"{bounty_id:064x}",
        "allowed_claimants": ["tobi.testnet"],
        "claimant": None,
        "status": status,
        "deadline": "1794604337000000000",
    }


@dataclass
class Views:
    bounties: list[dict[str, object]] = field(default_factory=list)
    accounts: dict[str, object] = field(default_factory=dict)
    pages: list[int] = field(default_factory=list)

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert contract == CONTRACT
        if method == "get_account":
            return self.accounts.get(str(args["account_id"]))
        if method == "get_bounty":
            index = int(str(args["bounty_id"])) - 1
            return self.bounties[index] if index < len(self.bounties) else None
        assert method == "get_bounties_by_sender"
        start, limit = int(str(args["from_index"])), int(str(args["limit"]))
        self.pages.append(start)
        window = self.bounties[start - 1 : start - 1 + limit]
        return [item for item in window if item["sender"] == args["sender"]]

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        raise AssertionError("status only reads")

    async def access_key(self, account: str, public_key: str) -> object | None:
        raise AssertionError("status only reads")

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("status only reads")


def test_posted_bounties_are_read_across_pages() -> None:
    views = Views([_bounty(n, ME if n % 3 else "eve.testnet") for n in range(1, PAGE + 8)])
    posted = asyncio.run(bounties_posted_by(views, CONTRACT, ME))
    assert [bounty.id for bounty in posted] == [n for n in range(1, PAGE + 8) if n % 3]
    assert views.pages == [1, 1 + PAGE]


def test_no_bounties_means_no_page_reads() -> None:
    views = Views()
    assert asyncio.run(bounties_posted_by(views, CONTRACT, ME)) == []
    assert views.pages == []


def test_an_account_is_read_from_the_contract() -> None:
    views = Views(
        accounts={
            ME: {
                "balance": str(6 * 10**23),
                "device_key": KEY.hex(),
                "max_per_bounty": str(5 * 10**23),
            }
        }
    )
    assert asyncio.run(fetch_account(views, CONTRACT, ME)) == EscrowAccount(
        6 * 10**23, KEY, 5 * 10**23
    )
    assert asyncio.run(fetch_account(views, CONTRACT, "nobody.testnet")) is None


@pytest.mark.parametrize(
    "data", [[], {"balance": "1"}, {"balance": "x", "device_key": "02", "max_per_bounty": "1"}]
)
def test_a_malformed_account_is_refused(data: object) -> None:
    with pytest.raises(PayError, match="malformed account"):
        EscrowAccount.from_view(data)


BASE = Status(
    CONFIG,
    EscrowAccount(6 * 10**23, KEY, 5 * 10**23),
    KEY,
    True,
    (Bounty.from_view(_bounty(5, status="Done")), Bounty.from_view(_bounty(6))),
    (),
)


def test_status_shows_budget_keys_and_bounties() -> None:
    assert describe(BASE).splitlines() == [
        f"account      {ME} on {CONTRACT} (testnet)",
        "budget       0.6 NEAR available, at most 0.5 NEAR per bounty",
        f"release key  this Mac's ({KEY.hex()[:12]}…), so `ehor pay` can release here",
        "limited key  on this Mac",
        "posted       5 · 0.1 NEAR · Done · for tobi.testnet · pay with `ehor pay 5`"
        " · message 00000000",
        "             6 · 0.1 NEAR · Open · for tobi.testnet · message 00000000",
        "claimed      none",
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (replace(BASE, local_release_key=OTHER), "not this Mac's"),
        (replace(BASE, local_release_key=None), "this Mac has none, so it cannot pay"),
        (
            replace(BASE, account=None),
            "not registered on ehor-escrow.testnet; run `ehor near setup`",
        ),
        (replace(BASE, has_limited_key=False), "limited key  missing; run `ehor near setup`"),
    ],
)
def test_status_says_what_needs_fixing(status: Status, expected: str) -> None:
    assert expected in describe(status)

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from ehor_near.client import DEFAULT_GAS, ContractError
from ehor_near.pay import (
    RELEASE_GAS,
    Bounty,
    PayError,
    check_payable,
    fetch_bounty,
    registered_release_key,
    submit_release,
)

CONTRACT = "ehor-escrow.testnet"
VIEW = {
    "id": 2,
    "sender": "alice.testnet",
    "amount": "100000000000000000000000",
    "message_id": "abc123",
    "allowed_claimants": ["bob.testnet"],
    "claimant": "bob.testnet",
    "status": "Done",
    "deadline": "1794386614444784000",
    "created": "1794383016023537332",
}


@dataclass
class FakeNear:
    bounties: dict[int, object] = field(default_factory=dict)
    accounts: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, object], int]] = field(default_factory=list)
    release_to: str = "Released"
    fail_with: str | None = None

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert contract == CONTRACT
        if method == "get_bounty":
            return self.bounties.get(int(str(args["bounty_id"])))
        if method == "get_account":
            return self.accounts.get(str(args["account_id"]))
        raise AssertionError(method)

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        assert deposit_yocto == 0
        self.calls.append((method, dict(args), gas))
        if self.fail_with:
            raise ContractError(self.fail_with)
        bounty = self.bounties[int(str(args["bounty_id"]))]
        assert isinstance(bounty, dict)
        bounty["status"] = self.release_to
        return None

    async def access_key(self, account: str, public_key: str) -> object | None:
        raise AssertionError("pay never looks up access keys")

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("pay never adds keys")


def _bounty(**changes: object) -> Bounty:
    return Bounty.from_view({**VIEW, **changes})


def test_view_is_parsed() -> None:
    bounty = _bounty()
    assert bounty == Bounty(
        2, "alice.testnet", 10**23, "abc123", "bob.testnet", "Done", 1794386614444784000
    )


def test_description_shows_the_chain_terms() -> None:
    text = _bounty().describe()
    assert "amount    0.1 NEAR" in text
    assert "claimant  bob.testnet" in text
    assert "deadline  2026-11-11 08:43 UTC" in text
    assert "read from the chain" in text


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "0"},
        {"amount": "-5"},
        {"amount": 1.5},
        {"amount": True},
        {"id": "2x"},
        {"sender": "Not An Account"},
        {"claimant": 7},
        {"status": None},
    ],
)
def test_malformed_views_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(PayError, match="malformed bounty"):
        _bounty(**changes)


@pytest.mark.parametrize("data", [None, [], "x"])
def test_non_object_views_are_refused(data: object) -> None:
    with pytest.raises(PayError, match="malformed bounty"):
        Bounty.from_view(data)


def test_missing_field_is_refused() -> None:
    view = dict(VIEW)
    del view["claimant"]
    with pytest.raises(PayError, match="malformed bounty"):
        Bounty.from_view(view)


def test_fetch_refuses_a_missing_bounty() -> None:
    with pytest.raises(PayError, match="does not exist"):
        asyncio.run(fetch_bounty(FakeNear(), CONTRACT, 9))


def test_fetch_refuses_a_different_bounty() -> None:
    near = FakeNear(bounties={3: dict(VIEW)})
    with pytest.raises(PayError, match="different bounty"):
        asyncio.run(fetch_bounty(near, CONTRACT, 3))


def test_only_the_sender_can_pay() -> None:
    with pytest.raises(PayError, match=r"posted by alice\.testnet, not mallory\.testnet"):
        check_payable(_bounty(), "mallory.testnet")


@pytest.mark.parametrize("status", ["Open", "Claimed", "Releasing", "Released", "Refunded"])
def test_only_a_done_bounty_can_be_paid(status: str) -> None:
    with pytest.raises(PayError, match="only a Done bounty"):
        check_payable(_bounty(status=status), "alice.testnet")


def test_a_done_bounty_without_claimant_is_refused() -> None:
    with pytest.raises(PayError, match="no claimant"):
        check_payable(_bounty(claimant=None), "alice.testnet")


def test_payable_bounty_returns_the_chain_claimant() -> None:
    assert check_payable(_bounty(), "alice.testnet") == "bob.testnet"


def test_registered_release_key_is_read_from_the_chain() -> None:
    near = FakeNear(accounts={"alice.testnet": {"device_key": "02" + "ab" * 32}})
    key = asyncio.run(registered_release_key(near, CONTRACT, "alice.testnet"))
    assert key == bytes.fromhex("02" + "ab" * 32)


@pytest.mark.parametrize(
    ("accounts", "message"),
    [
        ({}, "not registered"),
        ({"alice.testnet": {"device_key": 5}}, "malformed account"),
        ({"alice.testnet": {"device_key": "zz"}}, "malformed account"),
        ({"alice.testnet": []}, "malformed account"),
    ],
)
def test_bad_registered_accounts_are_refused(accounts: dict[str, object], message: str) -> None:
    with pytest.raises(PayError, match=message):
        asyncio.run(registered_release_key(FakeNear(accounts=accounts), CONTRACT, "alice.testnet"))


def test_submit_sends_release_with_enough_gas_and_confirms() -> None:
    near = FakeNear(bounties={2: dict(VIEW)})
    bounty = asyncio.run(submit_release(near, CONTRACT, 2, "ab" * 64))
    assert bounty.status == "Released"
    assert near.calls == [("release", {"bounty_id": 2, "signature": "ab" * 64}, RELEASE_GAS)]


def test_submit_reports_a_bounty_that_did_not_release() -> None:
    near = FakeNear(bounties={2: dict(VIEW)}, release_to="Done")
    with pytest.raises(PayError, match="is Done"):
        asyncio.run(submit_release(near, CONTRACT, 2, "ab" * 64))


def test_submit_passes_contract_errors_through() -> None:
    near = FakeNear(bounties={2: dict(VIEW)}, fail_with="ERR_BAD_SIGNATURE")
    with pytest.raises(ContractError, match="ERR_BAD_SIGNATURE"):
        asyncio.run(submit_release(near, CONTRACT, 2, "ab" * 64))

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest

from ehor_near.client import DEFAULT_GAS
from ehor_near.limited_key import ALLOWED_METHODS, LimitedKeyError
from ehor_near.registration import (
    RegistrationError,
    confirm_registration,
    registration_call,
)
from ehor_near.wallet_page import WalletCall

CONTRACT = "ehor-escrow.testnet"
ME = "geff.testnet"
KEY = bytes.fromhex("03" + "ab" * 32)
LIMITED = "ed25519:" + "4" * 44
ALLOWANCE = 25 * 10**22


def _permission(**changes: object) -> dict[str, object]:
    call: dict[str, object] = {
        "receiver_id": CONTRACT,
        "method_names": list(ALLOWED_METHODS),
        "allowance": str(ALLOWANCE),
    }
    call.update(changes)
    return {"FunctionCall": call}


@dataclass
class Chain:
    accounts: dict[str, object] = field(default_factory=dict)
    keys: dict[tuple[str, str], object] = field(default_factory=dict)
    views: int = 0
    appear_after: int = 0

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert (contract, method) == (CONTRACT, "get_account")
        self.views += 1
        if self.views <= self.appear_after:
            return None
        return self.accounts.get(str(args["account_id"]))

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        raise AssertionError("setup never signs on the Mac")

    async def access_key(self, account: str, public_key: str) -> object | None:
        if self.views <= self.appear_after:
            return None
        return self.keys.get((account, public_key))

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("setup never signs on the Mac")


def _call(chain: Chain) -> WalletCall | None:
    return asyncio.run(
        registration_call(
            chain, CONTRACT, ME, KEY, max_per_bounty_yocto=5 * 10**23, deposit_yocto=10**24
        )
    )


def test_a_new_account_registers_with_deposit_and_limit() -> None:
    call = _call(Chain())
    assert call is not None
    assert call.to_json() == {
        "receiverId": CONTRACT,
        "method": "register",
        "args": {"p256_public_key": KEY.hex(), "max_per_bounty": str(5 * 10**23)},
        "deposit": str(10**24),
        "gas": str(DEFAULT_GAS),
    }
    assert "deposit of 1 NEAR" in call.summary
    assert "0.5 NEAR per bounty" in call.summary


def test_another_registered_key_is_replaced_with_one_yocto() -> None:
    call = _call(Chain(accounts={ME: {"device_key": "02" + "11" * 32}}))
    assert call is not None
    assert call.to_json()["method"] == "set_device_key"
    assert call.to_json()["deposit"] == "1"
    assert call.to_json()["args"] == {"p256_public_key": KEY.hex()}


def test_the_same_key_needs_nothing() -> None:
    assert _call(Chain(accounts={ME: {"device_key": KEY.hex()}})) is None


@pytest.mark.parametrize("account", [{"device_key": 5}, {"device_key": "zz"}, []])
def test_malformed_accounts_are_refused(account: object) -> None:
    with pytest.raises(RegistrationError, match="malformed"):
        _call(Chain(accounts={ME: account}))


def _confirm(chain: Chain, attempts: int = 3) -> None:
    asyncio.run(
        confirm_registration(
            chain,
            CONTRACT,
            ME,
            limited_public_key=LIMITED,
            release_key=KEY,
            max_allowance_yocto=ALLOWANCE,
            attempts=attempts,
            delay=0,
        )
    )


def _ready(**permission: object) -> Chain:
    return Chain(
        accounts={ME: {"device_key": KEY.hex()}}, keys={(ME, LIMITED): _permission(**permission)}
    )


def test_confirms_when_both_are_on_chain() -> None:
    _confirm(_ready())
    _confirm(_ready(method_names=[]))


def test_waits_for_the_chain_to_catch_up() -> None:
    chain = _ready()
    chain.appear_after = 2
    _confirm(chain, attempts=4)


def test_a_missing_limited_key_is_reported() -> None:
    chain = Chain(accounts={ME: {"device_key": KEY.hex()}})
    with pytest.raises(LimitedKeyError, match=r"is not on geff\.testnet"):
        _confirm(chain)


def test_a_wider_limited_key_is_refused_even_when_registered() -> None:
    with pytest.raises(LimitedKeyError, match="full access"):
        _confirm(
            Chain(accounts={ME: {"device_key": KEY.hex()}}, keys={(ME, LIMITED): "FullAccess"})
        )


def test_a_missing_release_key_is_reported() -> None:
    chain = Chain(
        accounts={ME: {"device_key": "02" + "11" * 32}}, keys={(ME, LIMITED): _permission()}
    )
    with pytest.raises(RegistrationError, match="does not have this Mac's release key"):
        _confirm(chain)

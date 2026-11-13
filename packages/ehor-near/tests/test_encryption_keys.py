import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from ehor.log.account_announce import AccountAnnounce
from ehor.service.accounts import AnnouncedAccount
from ehor.service.device import Device
from ehor_near.bounties import NEAR_SYSTEM
from ehor_near.client import DEFAULT_GAS, RpcError
from ehor_near.config import KeyPolicy
from ehor_near.encryption_keys import ApprovedKeys, ChainKeyCheck, EncryptionKeyError

CONTRACT = "ehor-escrow.testnet"
GEF_DEVICE, TOBI_DEVICE, EVE_DEVICE = b"\x01" * 33, b"\x02" * 33, b"\x03" * 33
GEF_KEY, TOBI_KEY, ROGUE_KEY = b"\x11" * 32, b"\x22" * 32, b"\x66" * 32


@dataclass
class Accounts:
    announced: list[AnnouncedAccount] = field(default_factory=list)

    def announced_accounts(self, system: str) -> tuple[AnnouncedAccount, ...]:
        assert system == NEAR_SYSTEM
        return tuple(self.announced)


@dataclass
class Chain:
    keys: dict[str, object] = field(default_factory=dict)
    down: bool = False
    views: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)

    async def __aenter__(self) -> Self:
        if self.down:
            raise RpcError("cannot reach the NEAR RPC")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert contract == CONTRACT
        self.views.append((method, args))
        return self.keys.get(str(args["account_id"]), {"nonce": 0, "keys": []})

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        raise AssertionError("the key check only reads the chain")

    async def access_key(self, account: str, public_key: str) -> object | None:
        raise AssertionError("the key check only reads approved keys")

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("the key check never adds keys")


def approved(*keys: bytes, nonce: int = 1) -> dict[str, object]:
    return {"nonce": nonce, "keys": [key.hex() for key in keys]}


def announce(device: bytes, account: str) -> AnnouncedAccount:
    return AnnouncedAccount(account.split(".", maxsplit=1)[0], device, account, b"\x02" * 33)


def checked(
    chain: Chain, policy: KeyPolicy = KeyPolicy.VERIFY, *announced: AnnouncedAccount
) -> ChainKeyCheck:
    accounts = Accounts(list(announced))
    check = ChainKeyCheck(CONTRACT, lambda: chain, accounts, policy)
    asyncio.run(check.refresh())
    return check


def test_an_approved_key_is_allowed() -> None:
    chain = Chain({"tobi.testnet": approved(TOBI_KEY)})
    check = checked(chain, KeyPolicy.REQUIRE, announce(TOBI_DEVICE, "tobi.testnet"))
    assert check.allows(TOBI_DEVICE, TOBI_KEY)
    assert chain.views == [("get_encryption_keys", {"account_id": "tobi.testnet"})]


@pytest.mark.parametrize("policy", list(KeyPolicy))
def test_a_key_the_account_did_not_approve_is_refused(policy: KeyPolicy) -> None:
    chain = Chain({"tobi.testnet": approved(TOBI_KEY)})
    check = checked(chain, policy, announce(TOBI_DEVICE, "tobi.testnet"))
    assert not check.allows(TOBI_DEVICE, ROGUE_KEY)


def test_a_rogue_device_claiming_an_account_cannot_add_its_own_key() -> None:
    chain = Chain({"tobi.testnet": approved(TOBI_KEY)})
    check = checked(
        chain,
        KeyPolicy.VERIFY,
        announce(TOBI_DEVICE, "tobi.testnet"),
        announce(EVE_DEVICE, "tobi.testnet"),
    )
    assert not check.allows(EVE_DEVICE, ROGUE_KEY)
    assert check.allows(EVE_DEVICE, TOBI_KEY)


def test_a_removed_key_is_refused_after_the_next_refresh() -> None:
    chain = Chain({"tobi.testnet": approved(TOBI_KEY, ROGUE_KEY)})
    check = checked(chain, KeyPolicy.VERIFY, announce(TOBI_DEVICE, "tobi.testnet"))
    assert check.allows(TOBI_DEVICE, ROGUE_KEY)
    chain.keys["tobi.testnet"] = approved(TOBI_KEY, nonce=3)
    asyncio.run(check.refresh())
    assert not check.allows(TOBI_DEVICE, ROGUE_KEY)


@pytest.mark.parametrize(
    ("policy", "allowed"), [(KeyPolicy.VERIFY, True), (KeyPolicy.REQUIRE, False)]
)
def test_policy_decides_for_accounts_without_approved_keys(
    policy: KeyPolicy, allowed: bool
) -> None:
    check = checked(Chain(), policy, announce(GEF_DEVICE, "gef.testnet"))
    assert check.allows(GEF_DEVICE, GEF_KEY) is allowed


@pytest.mark.parametrize(
    ("policy", "allowed"), [(KeyPolicy.VERIFY, True), (KeyPolicy.REQUIRE, False)]
)
def test_policy_decides_for_devices_without_a_near_account(
    policy: KeyPolicy, allowed: bool
) -> None:
    check = checked(Chain({"tobi.testnet": approved(TOBI_KEY)}), policy)
    assert check.allows(GEF_DEVICE, GEF_KEY) is allowed


def test_policy_decides_before_the_chain_was_read() -> None:
    accounts = Accounts([announce(TOBI_DEVICE, "tobi.testnet")])
    chain = Chain({"tobi.testnet": approved(TOBI_KEY)})
    strict = ChainKeyCheck(CONTRACT, lambda: chain, accounts, KeyPolicy.REQUIRE)
    assert not strict.allows(TOBI_DEVICE, TOBI_KEY)
    assert strict.approved(TOBI_DEVICE) is None


def test_the_last_keys_read_stay_while_the_chain_is_down() -> None:
    chain = Chain({"tobi.testnet": approved(TOBI_KEY)})
    accounts = Accounts([announce(TOBI_DEVICE, "tobi.testnet")])
    check = ChainKeyCheck(CONTRACT, lambda: chain, accounts, interval=0.01)
    asyncio.run(check.refresh())
    chain.down = True

    async def run_briefly() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(check.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(run_briefly())
    assert not check.allows(TOBI_DEVICE, ROGUE_KEY)
    assert check.allows(TOBI_DEVICE, TOBI_KEY)


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"nonce": -1, "keys": []},
        {"nonce": True, "keys": []},
        {"nonce": 1, "keys": "ab"},
        {"nonce": 1, "keys": ["AB" * 32]},
        {"nonce": 1, "keys": ["ab" * 31]},
    ],
)
def test_a_malformed_view_is_rejected(data: object) -> None:
    with pytest.raises(EncryptionKeyError):
        ApprovedKeys.from_view(data)


def _settle(*devices: Device) -> None:
    for _ in range(3):
        for device in devices:
            device.sync()


def test_teammates_seal_only_to_keys_the_account_approved(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    passphrase = b"test passphrase"
    gef = Device.create_team(tmp_path / "gef", "gef", passphrase, exchange=shared)
    tobi = Device.join(tmp_path / "tobi", gef.invite, "tobi", passphrase, exchange=shared)
    gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
    _settle(gef, tobi)
    tobi.announce_account(AccountAnnounce(NEAR_SYSTEM, "tobi.testnet", b"\x03" * 33))
    _settle(gef, tobi)

    chain = Chain({"tobi.testnet": approved(ROGUE_KEY)})
    check = ChainKeyCheck(CONTRACT, lambda: chain, gef)
    gef.check_recipients(check)
    asyncio.run(check.refresh())
    refused = gef.call("post", {"to": ["tobi"], "type": "fyi", "summary": "first note"})
    _settle(gef, tobi)
    assert refused.endswith("not sealed for tobi: encryption key failed a check")
    assert "first note" not in tobi.call("inbox", {})

    chain.keys["tobi.testnet"] = approved(tobi.encryption_key, nonce=2)
    asyncio.run(check.refresh())
    sealed = gef.call("post", {"to": ["tobi"], "type": "fyi", "summary": "second note"})
    _settle(gef, tobi)
    assert "not sealed" not in sealed
    assert "second note" in tobi.call("inbox", {})
    gef.close()
    tobi.close()

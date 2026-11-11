import asyncio
import base64
import json
import sys
from dataclasses import dataclass, field

import pytest

from ehor_near.client import (
    DEFAULT_GAS,
    ContractError,
    NearError,
    NearUnavailableError,
    RpcError,
    WrongChainError,
)
from ehor_near.pynear import PyNearClient

CONTRACT = "escrow.ehor.testnet"
PRIVATE_KEY = "ed25519:" + "3" * 88


class FakeRpcError(Exception):
    pass


class FakeViewError(Exception):
    pass


@dataclass
class FakeResult:
    status: object = None
    result: object = None


def success(value: object) -> FakeResult:
    encoded = "" if value is None else base64.b64encode(json.dumps(value).encode()).decode()
    return FakeResult(status={"SuccessValue": encoded})


def panic(message: str) -> FakeResult:
    error = {"FunctionCallError": {"ExecutionError": f"Smart contract panicked: {message}"}}
    return FakeResult(status={"Failure": {"ActionError": {"index": 0, "kind": error}}})


@dataclass
class FakeProvider:
    reply: object = None
    raises: Exception | None = None
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def get_access_key(
        self, account_id: str, public_key: str, finality: str = "optimistic"
    ) -> object:
        self.calls.append((account_id, public_key, finality))
        if self.raises is not None:
            raise self.raises
        return self.reply


@dataclass
class FakeAccount:
    chain_id: str = "testnet"
    provider: FakeProvider = field(default_factory=FakeProvider)
    reply: FakeResult = field(default_factory=lambda: success(None))
    raises: Exception | None = None
    calls: list[tuple[object, ...]] = field(default_factory=list)
    started: bool = False
    shut_down: bool = False

    async def startup(self) -> None:
        if self.raises is not None:
            raise self.raises
        self.started = True

    async def shutdown(self) -> None:
        self.shut_down = True

    def _answer(self, *call: object) -> FakeResult:
        self.calls.append(call)
        if self.raises is not None:
            raise self.raises
        return self.reply

    async def view_function(
        self, contract_id: str, method_name: str, args: dict[str, object]
    ) -> object:
        return self._answer("view", contract_id, method_name, args)

    async def function_call(
        self,
        contract_id: str,
        method_name: str,
        args: dict[str, object],
        gas: int,
        amount: int,
    ) -> object:
        return self._answer("call", contract_id, method_name, args, gas, amount)

    async def add_public_key(
        self, public_key: str, receiver_id: str, method_names: list[str], allowance: int
    ) -> object:
        return self._answer("add_key", public_key, receiver_id, method_names, allowance)


def client(account: FakeAccount) -> PyNearClient:
    return PyNearClient(account, rpc_errors=(FakeRpcError,), view_errors=(FakeViewError,))


async def _view(account: FakeAccount) -> object:
    async with client(account) as near:
        return await near.view(CONTRACT, "get_bounty", {"bounty_id": 1})


async def _call(account: FakeAccount, deposit: int = 0) -> object:
    async with client(account) as near:
        return await near.call(CONTRACT, "claim", {"bounty_id": 1}, deposit_yocto=deposit)


def test_view_returns_decoded_result() -> None:
    account = FakeAccount(reply=FakeResult(result={"amount": "5"}))
    assert asyncio.run(_view(account)) == {"amount": "5"}
    assert account.calls == [("view", CONTRACT, "get_bounty", {"bounty_id": 1})]
    assert account.shut_down


def test_view_panic_is_a_contract_error() -> None:
    account = FakeAccount()

    async def run() -> None:
        async with client(account) as near:
            account.raises = FakeViewError("Smart contract panicked: ERR_NO_BOUNTY")
            await near.view(CONTRACT, "get_bounty", {"bounty_id": 9})

    with pytest.raises(ContractError, match=r"^ERR_NO_BOUNTY$"):
        asyncio.run(run())


def test_call_passes_gas_and_deposit_and_decodes_value() -> None:
    account = FakeAccount(reply=success(7))
    assert asyncio.run(_call(account, deposit=1)) == 7
    assert account.calls == [("call", CONTRACT, "claim", {"bounty_id": 1}, DEFAULT_GAS, 1)]


def test_call_with_empty_return_is_none() -> None:
    assert asyncio.run(_call(FakeAccount(reply=success(None)))) is None


def test_call_panic_is_a_contract_error_with_the_err_code() -> None:
    with pytest.raises(ContractError, match=r"^ERR_NOT_CLAIMANT$"):
        asyncio.run(_call(FakeAccount(reply=panic("ERR_NOT_CLAIMANT"))))


def test_call_failure_without_panic_keeps_the_raw_failure() -> None:
    failure = {"Failure": {"ActionError": {"kind": {"AccountDoesNotExist": {"account_id": "x"}}}}}
    with pytest.raises(ContractError, match="AccountDoesNotExist"):
        asyncio.run(_call(FakeAccount(reply=FakeResult(status=failure))))


@pytest.mark.parametrize(
    "status",
    [None, {"SuccessReceiptId": "abc"}, {"SuccessValue": "not base64!"}, {"SuccessValue": "eyI="}],
)
def test_call_rejects_malformed_results(status: object) -> None:
    with pytest.raises(RpcError):
        asyncio.run(_call(FakeAccount(reply=FakeResult(status=status))))


def test_call_rpc_failure_is_an_rpc_error() -> None:
    account = FakeAccount()

    async def run() -> None:
        async with client(account) as near:
            account.raises = FakeRpcError("timeout")
            await near.call(CONTRACT, "claim", {"bounty_id": 1})

    with pytest.raises(RpcError, match="call claim failed"):
        asyncio.run(run())


def test_unreachable_rpc_on_startup_is_an_rpc_error() -> None:
    with pytest.raises(RpcError, match="cannot reach"):
        asyncio.run(_view(FakeAccount(raises=FakeRpcError("refused"))))


def test_mainnet_rpc_is_refused_and_closed() -> None:
    account = FakeAccount(chain_id="mainnet")
    with pytest.raises(WrongChainError, match="testnet only"):
        asyncio.run(_view(account))
    assert account.shut_down
    assert account.calls == []


def test_add_function_call_key_scopes_the_key() -> None:
    account = FakeAccount()

    async def run() -> None:
        async with client(account) as near:
            await near.add_function_call_key("ed25519:abc", CONTRACT, ("claim", "mark_done"), 10)

    asyncio.run(run())
    assert account.calls == [("add_key", "ed25519:abc", CONTRACT, ["claim", "mark_done"], 10)]


def test_add_function_call_key_failure_raises() -> None:
    async def run() -> None:
        async with client(FakeAccount(reply=panic("ERR_X"))) as near:
            await near.add_function_call_key("ed25519:abc", CONTRACT, ["claim"], 10)

    with pytest.raises(ContractError):
        asyncio.run(run())


@pytest.mark.parametrize(
    "private_key", ["", "3" * 88, "ed25519:", "ed25519:0OIl" * 12, "secp256k1:" + "3" * 88]
)
def test_connect_rejects_malformed_private_keys_without_echoing_them(private_key: str) -> None:
    with pytest.raises(NearError) as caught:
        PyNearClient.connect("alice.testnet", private_key)
    assert str(caught.value) == "private key must be ed25519:<base58>"


def test_connect_rejects_invalid_account_id() -> None:
    with pytest.raises(NearError, match="invalid NEAR account id"):
        PyNearClient.connect("Alice", PRIVATE_KEY)


def test_connect_without_py_near_names_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "py_near.account", None)
    with pytest.raises(NearUnavailableError, match="pip install ehor-near"):
        PyNearClient.connect("alice.testnet", PRIVATE_KEY)


async def _access_key(account: FakeAccount) -> object:
    async with client(account) as near:
        return await near.access_key("alice.testnet", "ed25519:abc")


def test_access_key_returns_the_permission_at_final() -> None:
    permission = {"FunctionCall": {"receiver_id": CONTRACT}}
    account = FakeAccount(provider=FakeProvider(reply={"nonce": 1, "permission": permission}))
    assert asyncio.run(_access_key(account)) == permission
    assert account.provider.calls == [("alice.testnet", "ed25519:abc", "final")]


def test_missing_access_key_is_none() -> None:
    reply = {"error": "access key ed25519:abc does not exist while viewing", "logs": []}
    assert asyncio.run(_access_key(FakeAccount(provider=FakeProvider(reply=reply)))) is None


def test_unknown_account_has_no_access_key() -> None:
    reply = {"error": "account alice.testnet does not exist while viewing"}
    assert asyncio.run(_access_key(FakeAccount(provider=FakeProvider(reply=reply)))) is None


@pytest.mark.parametrize(
    "provider",
    [
        FakeProvider(reply={"error": "Server error"}),
        FakeProvider(reply="nope"),
        FakeProvider(raises=FakeRpcError("down")),
    ],
)
def test_access_key_errors_are_rpc_errors(provider: FakeProvider) -> None:
    with pytest.raises(RpcError):
        asyncio.run(_access_key(FakeAccount(provider=provider)))

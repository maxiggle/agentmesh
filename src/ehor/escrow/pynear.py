import base64
import binascii
import importlib
import json
import re
from collections.abc import Mapping, Sequence
from types import ModuleType, TracebackType
from typing import Protocol, Self

from ehor.escrow.client import (
    DEFAULT_GAS,
    TESTNET,
    TESTNET_RPC,
    ContractError,
    NearError,
    NearUnavailableError,
    RpcError,
    WrongChainError,
    is_account_id,
)

_PRIVATE_KEY = re.compile(r"ed25519:[1-9A-HJ-NP-Za-km-z]{43,88}")
_PANIC_PREFIX = "Smart contract panicked: "
_NO_KEY = "does not exist"


class _Provider(Protocol):
    async def get_access_key(
        self, account_id: str, public_key: str, finality: str = ...
    ) -> object: ...


class _Account(Protocol):
    chain_id: str

    @property
    def provider(self) -> _Provider: ...

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def view_function(
        self, contract_id: str, method_name: str, args: dict[str, object]
    ) -> object: ...

    async def function_call(
        self,
        contract_id: str,
        method_name: str,
        args: dict[str, object],
        gas: int,
        amount: int,
    ) -> object: ...

    async def add_public_key(
        self, public_key: str, receiver_id: str, method_names: list[str], allowance: int
    ) -> object: ...


def _import(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise NearUnavailableError(
            "paid requests need the py-near package: pip install 'ehor[near]'"
        ) from exc


def _check_private_key(private_key: str) -> None:
    # py-near logs the first characters of a key it cannot decode, so reject bad keys first.
    if not _PRIVATE_KEY.fullmatch(private_key):
        raise NearError("private key must be ed25519:<base58>")


def _failure_message(failure: object) -> str:
    if isinstance(failure, dict):
        execution = failure.get("ExecutionError")
        if isinstance(execution, str):
            return execution.removeprefix(_PANIC_PREFIX)
        for value in failure.values():
            found = _failure_message(value)
            if found:
                return found
    return ""


def _outcome(result: object) -> object:
    status = getattr(result, "status", None)
    if not isinstance(status, dict):
        raise RpcError("transaction result has no status")
    if "Failure" in status:
        failure = status["Failure"]
        message = _failure_message(failure) or json.dumps(failure, separators=(",", ":"))
        raise ContractError(message)
    encoded = status.get("SuccessValue")
    if not isinstance(encoded, str):
        raise RpcError(f"unexpected transaction status {sorted(status)}")
    if not encoded:
        return None
    try:
        decoded: object = json.loads(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise RpcError("transaction returned a value that is not JSON") from exc
    return decoded


class PyNearClient:
    """A `NearClient` backed by py-near. Use it as an async context manager."""

    def __init__(
        self,
        account: _Account,
        *,
        rpc_errors: tuple[type[Exception], ...] = (),
        view_errors: tuple[type[Exception], ...] = (),
    ) -> None:
        self._account = account
        self._rpc_errors = rpc_errors
        self._view_errors = view_errors

    @classmethod
    def connect(
        cls,
        account_id: str | None = None,
        private_key: str | None = None,
        rpc_url: str = TESTNET_RPC,
    ) -> "PyNearClient":
        if account_id is not None and not is_account_id(account_id):
            raise NearError(f"invalid NEAR account id {account_id!r}")
        if private_key is not None:
            _check_private_key(private_key)
        account_module = _import("py_near.account")
        provider_errors = _import("py_near.exceptions.provider")
        errors = _import("py_near.exceptions.exceptions")
        aiohttp = _import("aiohttp")
        account: _Account = account_module.Account(account_id, private_key, rpc_url)
        return cls(
            account,
            rpc_errors=(
                provider_errors.JsonProviderError,
                errors.RpcNotAvailableError,
                errors.RpcEmptyResponse,
                aiohttp.ClientError,
                TimeoutError,
            ),
            view_errors=(account_module.ViewFunctionError,),
        )

    async def __aenter__(self) -> Self:
        try:
            await self._account.startup()
        except self._rpc_errors as exc:
            raise RpcError(f"cannot reach the NEAR RPC: {exc}") from exc
        if self._account.chain_id != TESTNET:
            await self._account.shutdown()
            raise WrongChainError(f"RPC is on {self._account.chain_id!r}; ehor uses {TESTNET} only")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._account.shutdown()

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        try:
            result = await self._account.view_function(contract, method, dict(args))
        except self._view_errors as exc:
            raise ContractError(str(exc).removeprefix(_PANIC_PREFIX)) from exc
        except self._rpc_errors as exc:
            raise RpcError(f"view {method} failed: {exc}") from exc
        value: object = getattr(result, "result", None)
        return value

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        try:
            result = await self._account.function_call(
                contract, method, dict(args), gas=gas, amount=deposit_yocto
            )
        except self._rpc_errors as exc:
            raise RpcError(f"call {method} failed: {exc}") from exc
        return _outcome(result)

    async def access_key(self, account: str, public_key: str) -> object | None:
        try:
            result = await self._account.provider.get_access_key(
                account, public_key, finality="final"
            )
        except self._rpc_errors as exc:
            raise RpcError(f"view access key failed: {exc}") from exc
        if not isinstance(result, dict):
            raise RpcError("access key view returned no object")
        error = result.get("error")
        if error is not None:
            if _NO_KEY in str(error):
                return None
            raise RpcError(f"view access key failed: {error}")
        permission: object = result.get("permission")
        return permission

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        try:
            result = await self._account.add_public_key(
                public_key, contract, list(methods), allowance=allowance_yocto
            )
        except self._rpc_errors as exc:
            raise RpcError(f"adding the access key failed: {exc}") from exc
        _outcome(result)

import re
from collections.abc import Mapping, Sequence
from typing import Protocol

TESTNET = "testnet"
TESTNET_RPC = "https://test.rpc.fastnear.com"
TGAS = 10**12
DEFAULT_GAS = 100 * TGAS
ONE_YOCTO = 1

_ACCOUNT_ID = re.compile(r"(([a-z\d]+[-_])*[a-z\d]+\.)*([a-z\d]+[-_])*[a-z\d]+")
_ACCOUNT_ID_LENGTH = range(2, 65)


class NearError(Exception):
    pass


class NearUnavailableError(NearError):
    pass


class WrongChainError(NearError):
    pass


class RpcError(NearError):
    pass


class ContractError(NearError):
    """The contract rejected a call; the message carries its `ERR_*` panic when there is one."""


def is_account_id(value: str) -> bool:
    return len(value) in _ACCOUNT_ID_LENGTH and _ACCOUNT_ID.fullmatch(value) is not None


class NearClient(Protocol):
    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object: ...

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        """Sign and send a function call, returning the decoded JSON result.

        Raises `ContractError` when the transaction lands but the contract panics.
        """
        ...

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        """Add a key that can call only `methods` on `contract`, paying at most the allowance."""
        ...

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass

from ehor_near.bounties import NEAR_SYSTEM, AccountBook, ConnectNear
from ehor_near.client import NearError
from ehor_near.config import KeyPolicy

REFRESH_SECONDS = 300.0
_KEY_HEX = re.compile(r"[0-9a-f]{64}")

_log = logging.getLogger(__name__)


class EncryptionKeyError(NearError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovedKeys:
    """The X25519 keys a NEAR account approved in the escrow contract, and its change counter."""

    nonce: int
    keys: frozenset[bytes]

    @classmethod
    def from_view(cls, data: object) -> "ApprovedKeys":
        if not isinstance(data, dict):
            raise EncryptionKeyError("get_encryption_keys returned no object")
        nonce, keys = data.get("nonce"), data.get("keys")
        if isinstance(nonce, bool) or not isinstance(nonce, int) or nonce < 0:
            raise EncryptionKeyError("get_encryption_keys returned a bad nonce")
        if not isinstance(keys, list) or not all(
            isinstance(key, str) and _KEY_HEX.fullmatch(key) for key in keys
        ):
            raise EncryptionKeyError("get_encryption_keys returned a bad key list")
        return cls(nonce, frozenset(bytes.fromhex(key) for key in keys))


class ChainKeyCheck:
    """Seals to a teammate only with an encryption key their NEAR account approved.

    The account comes from the device's own signed account announcement, never from message
    text. Approved keys are read by view call in the background, so sealing never waits on
    the network; until an account is read, and when it approved none, `policy` decides.
    """

    def __init__(
        self,
        contract: str,
        connect: ConnectNear,
        accounts: AccountBook,
        policy: KeyPolicy = KeyPolicy.VERIFY,
        *,
        interval: float = REFRESH_SECONDS,
    ) -> None:
        self._contract = contract
        self._connect = connect
        self._accounts = accounts
        self._policy = policy
        self._interval = interval
        self._approved: dict[str, ApprovedKeys] = {}

    def allows(self, device: bytes, encryption_key: bytes) -> bool:
        approved = self.approved(device)
        if approved is None or not approved.keys:
            return self._policy is KeyPolicy.VERIFY
        return encryption_key in approved.keys

    def approved(self, device: bytes) -> ApprovedKeys | None:
        """What the chain last said about the account `device` announced, if it was read."""
        account = self._account_of(device)
        return None if account is None else self._approved.get(account)

    async def refresh(self) -> None:
        accounts = sorted({a.account for a in self._accounts.announced_accounts(NEAR_SYSTEM)})
        fresh: dict[str, ApprovedKeys] = {}
        async with self._connect() as near:
            for account in accounts:
                data = await near.view(
                    self._contract, "get_encryption_keys", {"account_id": account}
                )
                fresh[account] = ApprovedKeys.from_view(data)
        self._approved = fresh

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
            except NearError as exc:
                # The last keys read stay in force until the chain can be read again.
                _log.warning("could not read approved encryption keys: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._interval)

    def _account_of(self, device: bytes) -> str | None:
        for announced in self._accounts.announced_accounts(NEAR_SYSTEM):
            if announced.device == device:
                return announced.account
        return None

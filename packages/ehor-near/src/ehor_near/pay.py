import time
from dataclasses import dataclass

from ehor_near.client import TGAS, NearClient, is_account_id
from ehor_near.payload import format_near

RELEASE_GAS = 150 * TGAS
DONE = "Done"
RELEASED = "Released"
_NS_PER_SECOND = 10**9


class PayError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Bounty:
    """A bounty as the contract reports it; the only source `ehor pay` trusts for terms."""

    id: int
    sender: str
    amount_yocto: int
    message_id: str
    claimant: str | None
    status: str
    deadline_ns: int
    allowed_claimants: tuple[str, ...] = ()

    @classmethod
    def from_view(cls, data: object) -> "Bounty":
        if not isinstance(data, dict):
            raise PayError("the contract returned a malformed bounty")
        try:
            bounty = cls(
                id=_int(data["id"]),
                sender=_account(data["sender"]),
                amount_yocto=_int(data["amount"]),
                message_id=_text(data["message_id"]),
                claimant=None if data["claimant"] is None else _account(data["claimant"]),
                status=_text(data["status"]),
                deadline_ns=_int(data["deadline"]),
                allowed_claimants=_accounts(data.get("allowed_claimants", [])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PayError("the contract returned a malformed bounty") from exc
        if bounty.amount_yocto <= 0:
            raise PayError("the contract returned a malformed bounty")
        return bounty

    @property
    def deadline(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(self.deadline_ns // _NS_PER_SECOND))

    def describe(self) -> str:
        deadline = self.deadline
        return "\n".join(
            [
                f"bounty    {self.id} (read from the chain)",
                f"sender    {self.sender}",
                f"claimant  {self.claimant or 'none'}",
                f"amount    {format_near(self.amount_yocto)} NEAR",
                f"status    {self.status}",
                f"deadline  {deadline}",
                f"message   {self.message_id}",
            ]
        )


def _int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise TypeError("expected an integer")


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def _accounts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("expected a list of accounts")
    return tuple(_account(item) for item in value)


def _account(value: object) -> str:
    account = _text(value)
    if not is_account_id(account):
        raise ValueError("expected a NEAR account id")
    return account


async def fetch_bounty(near: NearClient, contract: str, bounty_id: int) -> Bounty:
    data = await near.view(contract, "get_bounty", {"bounty_id": bounty_id})
    if data is None:
        raise PayError(f"bounty {bounty_id} does not exist on {contract}")
    bounty = Bounty.from_view(data)
    if bounty.id != bounty_id:
        raise PayError("the contract returned a different bounty than the one asked for")
    return bounty


def check_payable(bounty: Bounty, payer: str) -> str:
    """Return the claimant to pay, or explain why this payer cannot release `bounty`."""
    if bounty.sender != payer:
        raise PayError(f"bounty {bounty.id} was posted by {bounty.sender}, not {payer}")
    if bounty.status != DONE:
        raise PayError(f"bounty {bounty.id} is {bounty.status}; only a Done bounty can be paid")
    if bounty.claimant is None:
        raise PayError(f"bounty {bounty.id} has no claimant")
    return bounty.claimant


async def registered_release_key(near: NearClient, contract: str, account: str) -> bytes:
    data = await near.view(contract, "get_account", {"account_id": account})
    if data is None:
        raise PayError(f"{account} is not registered on {contract}; run `ehor near setup`")
    if not isinstance(data, dict) or not isinstance(data.get("device_key"), str):
        raise PayError("the contract returned a malformed account")
    try:
        return bytes.fromhex(data["device_key"])
    except ValueError as exc:
        raise PayError("the contract returned a malformed account") from exc


async def submit_release(near: NearClient, contract: str, bounty_id: int, signature: str) -> Bounty:
    await near.call(
        contract, "release", {"bounty_id": bounty_id, "signature": signature}, gas=RELEASE_GAS
    )
    bounty = await fetch_bounty(near, contract, bounty_id)
    if bounty.status != RELEASED:
        raise PayError(f"release was sent but bounty {bounty_id} is {bounty.status}")
    return bounty

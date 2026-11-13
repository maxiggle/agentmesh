from collections.abc import Sequence
from dataclasses import dataclass

from ehor_near.bounties import SENT, TAKEN, bounty_line
from ehor_near.client import NearClient
from ehor_near.config import NearConfig
from ehor_near.pay import Bounty, PayError, fetch_bounty
from ehor_near.payload import format_near

PAGE = 50
MAX_PAGES = 40
_KEY_PREFIX = 12


@dataclass(frozen=True, slots=True)
class EscrowAccount:
    """An owner's record on the escrow contract, as a view call reports it."""

    balance_yocto: int
    release_key: bytes
    max_per_bounty_yocto: int

    @classmethod
    def from_view(cls, data: object) -> "EscrowAccount":
        if not isinstance(data, dict):
            raise PayError("the contract returned a malformed account")
        try:
            return cls(
                int(str(data["balance"])),
                bytes.fromhex(str(data["device_key"])),
                int(str(data["max_per_bounty"])),
            )
        except (KeyError, ValueError) as exc:
            raise PayError("the contract returned a malformed account") from exc


@dataclass(frozen=True, slots=True)
class Status:
    config: NearConfig
    account: EscrowAccount | None
    local_release_key: bytes | None
    has_limited_key: bool
    posted: tuple[Bounty, ...]
    claimed: tuple[Bounty, ...]


async def fetch_account(near: NearClient, contract: str, account: str) -> EscrowAccount | None:
    data = await near.view(contract, "get_account", {"account_id": account})
    return None if data is None else EscrowAccount.from_view(data)


async def bounties_posted_by(near: NearClient, contract: str, sender: str) -> list[Bounty]:
    """Every bounty `sender` posted. Bounty ids run from 1 with no gaps, so a page whose first
    id does not exist is past the last bounty."""
    found: list[Bounty] = []
    for page in range(MAX_PAGES):
        start = 1 + page * PAGE
        if await near.view(contract, "get_bounty", {"bounty_id": start}) is None:
            break
        args = {"sender": sender, "from_index": start, "limit": PAGE}
        items = await near.view(contract, "get_bounties_by_sender", args)
        if not isinstance(items, list):
            raise PayError("the contract returned a malformed bounty list")
        found.extend(Bounty.from_view(item) for item in items)
    return found


async def fetch_bounties(near: NearClient, contract: str, ids: Sequence[int]) -> list[Bounty]:
    return [await fetch_bounty(near, contract, bounty_id) for bounty_id in ids]


def describe(status: Status) -> str:
    config = status.config
    lines = [f"account      {config.account_id} on {config.contract_id} ({config.network})"]
    account = status.account
    if account is None:
        lines.append(f"budget       not registered on {config.contract_id}; run `ehor near setup`")
    else:
        lines.append(
            f"budget       {format_near(account.balance_yocto)} NEAR available, at most "
            f"{format_near(account.max_per_bounty_yocto)} NEAR per bounty"
        )
    lines.append(f"release key  {_release(account, status.local_release_key)}")
    lines.append(
        "limited key  "
        + ("on this Mac" if status.has_limited_key else "missing; run `ehor near setup`")
    )
    lines += _bounties("posted", status.posted, SENT)
    lines += _bounties("claimed", status.claimed, TAKEN)
    return "\n".join(lines)


def _release(account: EscrowAccount | None, local: bytes | None) -> str:
    if local is None:
        return "this Mac has none, so it cannot pay; run `ehor near setup` on a Mac"
    if account is None:
        return f"{_short(local)} on this Mac, not registered yet"
    if account.release_key == local:
        return f"this Mac's ({_short(local)}), so `ehor pay` can release here"
    return (
        f"the contract holds {_short(account.release_key)}, not this Mac's {_short(local)}; "
        "run `ehor near setup` to pay from this Mac"
    )


def _bounties(label: str, bounties: Sequence[Bounty], role: str) -> list[str]:
    if not bounties:
        return [f"{label:<12} none"]
    lines = [f"{label:<12} {bounty_line(bounties[0], role)}"]
    lines += [f"{'':<12} {bounty_line(bounty, role)}" for bounty in bounties[1:]]
    return lines


def _short(key: bytes) -> str:
    return key.hex()[:_KEY_PREFIX] + "…"

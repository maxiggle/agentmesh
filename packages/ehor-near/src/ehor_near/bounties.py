import json
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from ehor.log.body import MessageType
from ehor.log.entry import Entry
from ehor.log.names import HANDLE
from ehor.service.accounts import AnnouncedAccount
from ehor.service.messenger import Message, MessageDetail
from ehor.service.plugins import PluginError
from ehor_near.client import TGAS, NearClient
from ehor_near.config import NearConfig
from ehor_near.pay import Bounty, fetch_bounty, registered_release_key
from ehor_near.payload import format_near, parse_near

LEDGER_FILE = "near-bounties.json"
NEAR_SYSTEM = "near-testnet"
DEFAULT_HOURS = 24
MAX_HOURS = 30 * 24
POST_GAS = 50 * TGAS
STEP_GAS = 30 * TGAS
OPEN = "Open"
CLAIMED = "Claimed"
DONE = "Done"
SENT = "sent"
TAKEN = "taken"
_NS_PER_SECOND = 10**9


class BountyError(PluginError):
    pass


class MessageBook(Protocol):
    """The parts of ehor's messenger the bounty tools use."""

    @property
    def author(self) -> bytes: ...

    def display_name(self, author: bytes) -> str: ...

    def read(self, id_prefix: str) -> MessageDetail: ...

    def reply(self, id_prefix: str, summary: str, *, links: Sequence[str] = ()) -> Entry: ...

    def set_status(self, id_prefix: str, status: str, note: str | None = None) -> Entry: ...


class AccountBook(Protocol):
    def announced_accounts(self, system: str) -> tuple[AnnouncedAccount, ...]: ...


ConnectNear = Callable[[], AbstractAsyncContextManager[NearClient]]


class BountyLedger:
    """Bounty ids this device posted or claimed, so `bounties` knows which to look up."""

    def __init__(self, home: Path) -> None:
        self._path = home / LEDGER_FILE

    def entries(self) -> list[tuple[int, str]]:
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return []
        except ValueError as exc:
            raise BountyError(f"invalid {self._path}: {exc}") from exc
        if not isinstance(data, list) or not all(_is_entry(item) for item in data):
            raise BountyError(f"invalid {self._path}")
        return [(int(item[0]), str(item[1])) for item in data]

    def add(self, bounty_id: int, role: str) -> None:
        entries = [entry for entry in self.entries() if entry[0] != bounty_id]
        entries.append((bounty_id, role))
        self._path.write_text(json.dumps([list(entry) for entry in sorted(entries)]) + "\n")


class BountyDesk:
    """Posts, claims and finishes bounties linked to ehor messages.

    Every term a tool reports comes from a view call. A teammate's NEAR account comes from the
    account_announce their own device signed, never from message text, and is used only while
    the escrow contract holds the release key that device announced with it.
    """

    def __init__(
        self,
        home: Path,
        config: NearConfig,
        connect: ConnectNear,
        *,
        messages: MessageBook,
        accounts: AccountBook,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._connect = connect
        self._messages = messages
        self._accounts = accounts
        self._clock = clock
        self._ledger = BountyLedger(home)

    async def post(
        self, message_id: str, amount: str, hours: int = DEFAULT_HOURS, claimant: str | None = None
    ) -> str:
        message = self._messages.read(message_id).message
        if message.entry.content.author != self._messages.author:
            raise BountyError("a bounty can only go on a request you sent")
        if message.body.type is not MessageType.FEATURE_REQUEST:
            raise BountyError("a bounty can only go on a feature_request")
        handle = claimant if claimant is not None else _addressed_handle(message)
        amount_yocto = parse_near(amount)
        if not 1 <= hours <= MAX_HOURS:
            raise BountyError(f"hours must be between 1 and {MAX_HOURS}")
        deadline_ns = (int(self._clock()) + hours * 3600) * _NS_PER_SECOND
        async with self._connect() as near:
            account = await self._checked(near, handle, self._announced_by(handle=handle))
            args = {
                "amount": str(amount_yocto),
                "message_id": message.entry.id.hex(),
                "allowed_claimants": [account],
                "deadline": str(deadline_ns),
            }
            result = await near.call(self._contract, "post_bounty", args, gas=POST_GAS)
            if isinstance(result, bool) or not isinstance(result, int):
                raise BountyError("the contract did not return a bounty id")
            bounty = await fetch_bounty(near, self._contract, result)
        self._ledger.add(bounty.id, SENT)
        terms = f"{format_near(bounty.amount_yocto)} NEAR for {handle} by {bounty.deadline}"
        self._messages.reply(message_id, f"NEAR bounty {bounty.id}: {terms}")
        return f"posted bounty {bounty.id}: {terms} ({account}, read from the chain)"

    async def claim(self, message_id: str, bounty_id: int) -> str:
        message, handle = self._teammate_request(message_id)
        async with self._connect() as near:
            sender = await self._sender(near, message, handle)
            bounty = await self._linked_bounty(near, bounty_id, message, sender)
            if self._config.account_id not in bounty.allowed_claimants:
                raise BountyError(f"bounty {bounty.id} is not offered to {self._config.account_id}")
            if bounty.status != OPEN:
                raise BountyError(f"bounty {bounty.id} is {bounty.status}, not {OPEN}")
            await near.call(self._contract, "claim", {"bounty_id": bounty.id}, gas=STEP_GAS)
        self._ledger.add(bounty.id, TAKEN)
        self._messages.set_status(message_id, "claimed", f"claimed NEAR bounty {bounty.id}")
        amount = format_near(bounty.amount_yocto)
        return f"claimed bounty {bounty.id}: {amount} NEAR from {sender}, due {bounty.deadline}"

    async def finish(self, message_id: str, bounty_id: int, note: str | None = None) -> str:
        message, handle = self._teammate_request(message_id)
        async with self._connect() as near:
            sender = await self._sender(near, message, handle)
            bounty = await self._linked_bounty(near, bounty_id, message, sender)
            if bounty.claimant != self._config.account_id or bounty.status != CLAIMED:
                raise BountyError(f"bounty {bounty.id} is not claimed by {self._config.account_id}")
            await near.call(self._contract, "mark_done", {"bounty_id": bounty.id}, gas=STEP_GAS)
        self._messages.set_status(message_id, "done", note or f"NEAR bounty {bounty.id} done")
        owner = self._messages.display_name(message.entry.content.author)
        return f"bounty {bounty.id} is done; {owner} can pay it with `ehor pay {bounty.id}`"

    async def overview(self) -> str:
        entries = self._ledger.entries()
        if not entries:
            return "no bounties yet"
        async with self._connect() as near:
            bounties = [
                (await fetch_bounty(near, self._contract, bounty_id), role)
                for bounty_id, role in entries
            ]
        return "\n".join(bounty_line(bounty, role) for bounty, role in bounties)

    @property
    def _contract(self) -> str:
        return self._config.contract_id

    def _teammate_request(self, message_id: str) -> tuple[Message, str]:
        message = self._messages.read(message_id).message
        author = message.entry.content.author
        if author == self._messages.author:
            raise BountyError(
                "that request is yours; bounty_claim and bounty_done are for its claimant"
            )
        return message, self._messages.display_name(author)

    def _announced_by(self, *, handle: str, device: bytes | None = None) -> list[AnnouncedAccount]:
        return [
            announced
            for announced in self._accounts.announced_accounts(NEAR_SYSTEM)
            if announced.handle == handle and device in {None, announced.device}
        ]

    async def _sender(self, near: NearClient, message: Message, handle: str) -> str:
        device = message.entry.content.author
        return await self._checked(near, handle, self._announced_by(handle=handle, device=device))

    async def _checked(
        self, near: NearClient, handle: str, announced: list[AnnouncedAccount]
    ) -> str:
        accounts = sorted({item.account for item in announced})
        if not accounts:
            raise BountyError(
                f"{handle} has not announced a NEAR account; they run `ehor near setup` "
                "and restart `ehor serve`"
            )
        if len(accounts) > 1:
            raise BountyError(f"{handle}'s devices announce different NEAR accounts: {accounts}")
        account = accounts[0]
        registered = await registered_release_key(near, self._contract, account)
        if registered not in {item.key for item in announced}:
            raise BountyError(
                f"{self._contract} holds another release key for {account} than {handle}'s "
                "device announced; they run `ehor near setup` again"
            )
        return account

    async def _linked_bounty(
        self, near: NearClient, bounty_id: int, message: Message, sender: str
    ) -> Bounty:
        bounty = await fetch_bounty(near, self._contract, bounty_id)
        if bounty.message_id != message.entry.id.hex():
            raise BountyError(f"bounty {bounty.id} is not linked to this message")
        if bounty.sender != sender:
            raise BountyError(f"bounty {bounty.id} was posted by {bounty.sender}, not {sender}")
        return bounty


def _addressed_handle(message: Message) -> str:
    handles = [target for target in message.body.to if HANDLE.fullmatch(target)]
    if len(handles) != 1:
        raise BountyError("the request is not addressed to one person; name the claimant")
    return handles[0]


def bounty_line(bounty: Bounty, role: str) -> str:
    parts = [f"{bounty.id}", f"{format_near(bounty.amount_yocto)} NEAR", bounty.status]
    if role == SENT:
        parts.append(f"for {bounty.claimant or ', '.join(bounty.allowed_claimants)}")
        if bounty.status == DONE:
            parts.append(f"pay with `ehor pay {bounty.id}`")
    else:
        parts.append(f"from {bounty.sender}")
    parts.append(f"message {bounty.message_id[:8]}")
    return " · ".join(parts)


def _is_entry(item: object) -> bool:
    match item:
        case [int() as bounty_id, str() as role]:
            return not isinstance(bounty_id, bool) and role in {SENT, TAKEN}
    return False

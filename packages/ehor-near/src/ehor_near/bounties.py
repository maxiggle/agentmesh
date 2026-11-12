import json
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from ehor.log.body import MessageType
from ehor.log.entry import Entry
from ehor.log.names import HANDLE
from ehor.service.messenger import Message, MessageDetail
from ehor.service.plugins import PluginError
from ehor_near.client import TGAS, NearClient
from ehor_near.config import NearConfig
from ehor_near.pay import Bounty, fetch_bounty
from ehor_near.payload import format_near, parse_near
from ehor_near.teammates import TeammateAccounts

LEDGER_FILE = "near-bounties.json"
DEFAULT_HOURS = 24
MAX_HOURS = 30 * 24
POST_GAS = 50 * TGAS
STEP_GAS = 30 * TGAS
OPEN = "Open"
CLAIMED = "Claimed"
DONE = "Done"
_SENT = "sent"
_TAKEN = "taken"
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

    Every term a tool reports comes from a view call. The claimant's account and the sender's
    account come from the links a person made with `ehor near teammate`, never from message text.
    """

    def __init__(
        self,
        home: Path,
        config: NearConfig,
        connect: ConnectNear,
        messages: MessageBook,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._home = home
        self._config = config
        self._connect = connect
        self._messages = messages
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
        account = self._linked(handle)
        amount_yocto = parse_near(amount)
        if not 1 <= hours <= MAX_HOURS:
            raise BountyError(f"hours must be between 1 and {MAX_HOURS}")
        deadline_ns = (int(self._clock()) + hours * 3600) * _NS_PER_SECOND
        args = {
            "amount": str(amount_yocto),
            "message_id": message.entry.id.hex(),
            "allowed_claimants": [account],
            "deadline": str(deadline_ns),
        }
        async with self._connect() as near:
            result = await near.call(self._contract, "post_bounty", args, gas=POST_GAS)
            if isinstance(result, bool) or not isinstance(result, int):
                raise BountyError("the contract did not return a bounty id")
            bounty = await fetch_bounty(near, self._contract, result)
        self._ledger.add(bounty.id, _SENT)
        terms = f"{format_near(bounty.amount_yocto)} NEAR for {handle} by {bounty.deadline}"
        self._messages.reply(message_id, f"NEAR bounty {bounty.id}: {terms}")
        return f"posted bounty {bounty.id}: {terms} ({account}, read from the chain)"

    async def claim(self, message_id: str, bounty_id: int) -> str:
        message, sender = self._teammate_request(message_id)
        async with self._connect() as near:
            bounty = await self._linked_bounty(near, bounty_id, message, sender)
            if self._config.account_id not in bounty.allowed_claimants:
                raise BountyError(f"bounty {bounty.id} is not offered to {self._config.account_id}")
            if bounty.status != OPEN:
                raise BountyError(f"bounty {bounty.id} is {bounty.status}, not {OPEN}")
            await near.call(self._contract, "claim", {"bounty_id": bounty.id}, gas=STEP_GAS)
        self._ledger.add(bounty.id, _TAKEN)
        self._messages.set_status(message_id, "claimed", f"claimed NEAR bounty {bounty.id}")
        amount = format_near(bounty.amount_yocto)
        return f"claimed bounty {bounty.id}: {amount} NEAR from {sender}, due {bounty.deadline}"

    async def finish(self, message_id: str, bounty_id: int, note: str | None = None) -> str:
        message, sender = self._teammate_request(message_id)
        async with self._connect() as near:
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
        return "\n".join(_line(bounty, role) for bounty, role in bounties)

    @property
    def _contract(self) -> str:
        return self._config.contract_id

    def _linked(self, handle: str) -> str:
        account = TeammateAccounts.load(self._home).account_for(handle)
        if account is None:
            raise BountyError(
                f"{handle} has no linked NEAR account; "
                f"your user can link one with `ehor near teammate {handle} <account>`"
            )
        return account

    def _teammate_request(self, message_id: str) -> tuple[Message, str]:
        message = self._messages.read(message_id).message
        author = message.entry.content.author
        if author == self._messages.author:
            raise BountyError(
                "that request is yours; bounty_claim and bounty_done are for its claimant"
            )
        return message, self._linked(self._messages.display_name(author))

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


def _line(bounty: Bounty, role: str) -> str:
    parts = [f"{bounty.id}", f"{format_near(bounty.amount_yocto)} NEAR", bounty.status]
    if role == _SENT:
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
            return not isinstance(bounty_id, bool) and role in {_SENT, _TAKEN}
    return False

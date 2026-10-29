import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from agentmesh.log.body import (
    BodyError,
    MessageBody,
    MessageType,
    Status,
    StatusBody,
)
from agentmesh.log.entry import Entry, Kind
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError
from agentmesh.log.names import HANDLE, ROLE, TEAM_CHANNEL, dm_channel, dm_members
from agentmesh.store.base import Store
from agentmesh.store.local_log import LocalLog, NotAuthorizedError

DEFAULT_CHANNEL = "#general"
INBOX_DEFAULT_LIMIT = 10
INBOX_MAX_LIMIT = 25
MIN_ID_PREFIX = 3
ALL_CHANNELS = "*"


class MessengerError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Identity:
    handle: str
    roles: tuple[str, ...] = ()
    channels: tuple[str, ...] = (ALL_CHANNELS,)


@dataclass(frozen=True, slots=True)
class Message:
    entry: Entry
    body: MessageBody
    status: Status
    position: int | None = None


@dataclass(frozen=True, slots=True)
class StatusChange:
    entry: Entry
    body: StatusBody
    target: Message
    position: int


@dataclass(frozen=True, slots=True)
class InboxPage:
    items: tuple[Message | StatusChange, ...]
    cursor: int
    remaining: int


@dataclass(frozen=True, slots=True)
class MessageDetail:
    message: Message
    replies: tuple[Message, ...]
    history: tuple[tuple[Entry, StatusBody], ...]


@dataclass(frozen=True, slots=True)
class Digest:
    new: int
    blocked_on_you: int
    questions: int
    shipped: int
    open_requests: int


@dataclass(frozen=True, slots=True)
class Teammate:
    author: bytes
    messages: int
    last_ts: int
    claimed: tuple[Message, ...]


class Directory(Protocol):
    """Who this device is, and what to call other authors."""

    def me(self) -> Identity: ...

    def name(self, author: bytes) -> str | None: ...

    def known_authors(self) -> tuple[bytes, ...]: ...


class StaticDirectory:
    def __init__(self, me: Identity, names: dict[bytes, str] | None = None) -> None:
        self._me = me
        self._names = dict(names or {})

    def me(self) -> Identity:
        return self._me

    def name(self, author: bytes) -> str | None:
        return self._names.get(author)

    def known_authors(self) -> tuple[bytes, ...]:
        return tuple(self._names)


class BodyCodec(Protocol):
    """Turns a body into what is stored in an entry, and back."""

    def seal(self, channel: str, kind: Kind, body: bytes) -> bytes: ...

    def open(self, entry: Entry) -> bytes | None:
        """The plaintext body, or None if this device cannot read it."""
        ...


class PlainCodec:
    def seal(self, channel: str, kind: Kind, body: bytes) -> bytes:
        return Envelope.plain(body).encode()

    def open(self, entry: Entry) -> bytes | None:
        try:
            envelope = Envelope.decode(entry.content.body)
        except EnvelopeError:
            return None
        return envelope.data if envelope.enc is Encryption.NONE else None


class Messenger:
    """The operations behind the MCP tools, independent of how results are shown."""

    def __init__(
        self,
        store: Store,
        local_log: LocalLog,
        directory: Directory,
        codec: BodyCodec | None = None,
    ) -> None:
        self._store = store
        self._log = local_log
        self._directory = directory
        self._codec = codec or PlainCodec()

    @property
    def me(self) -> Identity:
        return self._directory.me()

    @property
    def _me(self) -> Identity:
        return self._directory.me()

    def display_name(self, author: bytes) -> str:
        if author == self.author:
            return "me"
        return self._directory.name(author) or author[1:4].hex()

    @property
    def author(self) -> bytes:
        return self._log.author

    def post(
        self,
        to: Sequence[str],
        message_type: str,
        summary: str,
        *,
        links: Sequence[str] = (),
        acceptance: str | None = None,
        detail: str | None = None,
    ) -> Entry:
        targets = tuple(_clean(target) for target in to)
        body = self._build_message(
            message_type=message_type,
            to=targets,
            summary=summary,
            links=links,
            acceptance=acceptance,
            detail=detail,
        )
        return self._append(self._channel_for(targets), Kind.MESSAGE, body.encode(), ())

    def reply(self, id_prefix: str, summary: str, *, links: Sequence[str] = ()) -> Entry:
        original = self._resolve(id_prefix)
        members = dm_members(original.entry.content.channel)
        if members is None:
            to: tuple[str, ...] = (original.entry.content.channel,)
        else:
            to = tuple(member for member in members if member != self._me.handle) or members
        body = self._build_message(
            message_type=MessageType.FYI, to=to, summary=summary, links=links
        )
        return self._append(
            original.entry.content.channel, Kind.MESSAGE, body.encode(), (original.entry.id,)
        )

    def set_status(self, id_prefix: str, status: str, note: str | None = None) -> Entry:
        target = self._resolve(id_prefix)
        try:
            body = StatusBody(target.entry.id, Status(status), _optional(note))
        except ValueError as exc:
            raise MessengerError(str(exc)) from exc
        replaced = [entry.id for entry, _ in self._status_heads(target.entry.id)]
        return self._append(
            target.entry.content.channel,
            Kind.STATUS,
            body.encode(),
            (target.entry.id, *replaced),
        )

    def inbox(
        self, *, since: int | None = None, channel: str | None = None, limit: int | None = None
    ) -> InboxPage:
        size = _clamp(limit or INBOX_DEFAULT_LIMIT, 1, INBOX_MAX_LIMIT)
        after = since or 0
        relevant: list[Message | StatusChange] = []
        for arrival in self._store.arrivals(
            after, [Kind.MESSAGE, Kind.STATUS], unread_only=since is None
        ):
            entry = arrival.entry
            if entry.author == self.author:
                continue
            if channel is not None and entry.content.channel != channel:
                continue
            item = self._inbox_item(entry, arrival.position)
            if item is not None:
                relevant.append(item)
        page = tuple(relevant[:size])
        if since is None:
            self._store.mark_read(item.entry.id for item in page)
        cursor = page[-1].position if page and page[-1].position is not None else after
        return InboxPage(page, cursor, len(relevant) - len(page))

    def read(self, id_prefix: str) -> MessageDetail:
        message = self._resolve(id_prefix)
        replies = tuple(
            reply
            for entry in self._store.referencing(message.entry.id)
            if entry.content.kind is Kind.MESSAGE and (reply := self._as_message(entry)) is not None
        )
        history = tuple(
            sorted(self._statuses(message.entry.id), key=lambda pair: pair[0].content.ts)
        )
        self._store.mark_read([message.entry.id, *(r.entry.id for r in replies)])
        return MessageDetail(message, replies, history)

    def digest(self) -> Digest:
        new = blocked = questions = shipped = 0
        for arrival in self._store.arrivals(kinds=[Kind.MESSAGE], unread_only=True):
            message = self._as_message(arrival.entry)
            if (
                message is None
                or message.entry.author == self.author
                or not self._is_for_me(message)
            ):
                continue
            new += 1
            blocked += message.body.type is MessageType.BLOCKED and self._names_me(message)
            questions += message.body.type is MessageType.QUESTION
            shipped += message.body.type is MessageType.SHIPPED
        open_requests = sum(
            1
            for arrival in self._store.arrivals(kinds=[Kind.MESSAGE])
            if (message := self._as_message(arrival.entry)) is not None
            and message.body.type is MessageType.FEATURE_REQUEST
            and message.status is Status.OPEN
            and self._names_me(message)
        )
        return Digest(new, blocked, questions, shipped, open_requests)

    def team(self) -> tuple[Teammate, ...]:
        seen: dict[bytes, list[Entry]] = {
            author: [] for author in self._directory.known_authors() if author != self.author
        }
        for arrival in self._store.arrivals(kinds=[Kind.MESSAGE, Kind.STATUS]):
            if arrival.entry.author != self.author:
                seen.setdefault(arrival.entry.author, []).append(arrival.entry)
        teammates = []
        for author, entries in seen.items():
            claimed = tuple(
                message
                for entry in entries
                if entry.content.kind is Kind.STATUS
                and (status := self._status_body(entry)) is not None
                and status.status is Status.CLAIMED
                and (target := self._store.get(status.target)) is not None
                and (message := self._as_message(target)) is not None
                and message.status is Status.CLAIMED
            )
            messages = sum(entry.content.kind is Kind.MESSAGE for entry in entries)
            last_ts = max((entry.content.ts for entry in entries), default=0)
            teammates.append(Teammate(author, messages, last_ts, claimed))
        return tuple(sorted(teammates, key=lambda t: (-t.last_ts, self.display_name(t.author))))

    def _message_body(self, entry: Entry) -> MessageBody | None:
        data = self._codec.open(entry) if entry.content.kind is Kind.MESSAGE else None
        try:
            return None if data is None else MessageBody.decode(data)
        except BodyError:
            return None

    def _status_body(self, entry: Entry) -> StatusBody | None:
        data = self._codec.open(entry) if entry.content.kind is Kind.STATUS else None
        try:
            return None if data is None else StatusBody.decode(data)
        except BodyError:
            return None

    def _append(self, channel: str, kind: Kind, body: bytes, refs: Iterable[bytes]) -> Entry:
        try:
            return self._log.append(channel, kind, self._codec.seal(channel, kind, body), refs)
        except NotAuthorizedError as exc:
            raise MessengerError(f"cannot post: {exc}") from exc

    def _build_message(
        self,
        *,
        message_type: str,
        to: tuple[str, ...],
        summary: str,
        links: Sequence[str] = (),
        acceptance: str | None = None,
        detail: str | None = None,
    ) -> MessageBody:
        try:
            return MessageBody(
                type=MessageType(_clean(message_type)),
                to=to,
                summary=_clean(summary),
                detail=_optional(detail),
                acceptance=_optional(acceptance),
                links=tuple(_clean(link) for link in links),
            )
        except ValueError as exc:
            raise MessengerError(str(exc)) from exc

    def _channel_for(self, targets: tuple[str, ...]) -> str:
        channels = [target for target in targets if TEAM_CHANNEL.fullmatch(target)]
        if channels:
            return channels[0]
        handles = [target for target in targets if HANDLE.fullmatch(target)]
        if len(targets) == 1 and len(handles) == 1 and handles[0] != self._me.handle:
            return dm_channel(self._me.handle, handles[0])
        return DEFAULT_CHANNEL

    def _resolve(self, id_prefix: str) -> Message:
        text = id_prefix.strip().lower()
        if len(text) < MIN_ID_PREFIX * 2 or len(text) % 2:
            raise MessengerError("id must be an even number of hex chars, at least 6")
        try:
            prefix = bytes.fromhex(text)
        except ValueError as exc:
            raise MessengerError("id must be hex") from exc
        matches = [
            message
            for entry in self._store.find_by_prefix(prefix)
            if (message := self._as_message(entry)) is not None
        ]
        if not matches:
            raise MessengerError(f"no message {text}")
        if len(matches) > 1:
            raise MessengerError(f"{text} matches {len(matches)} messages; use more chars")
        return matches[0]

    def _inbox_item(self, entry: Entry, position: int) -> Message | StatusChange | None:
        if entry.content.kind is Kind.MESSAGE:
            message = self._as_message(entry, position)
            return message if message is not None and self._is_for_me(message) else None
        body = self._status_body(entry)
        if body is None:
            return None
        target_entry = self._store.get(body.target)
        if target_entry is None or target_entry.author != self.author:
            return None
        target = self._as_message(target_entry)
        return None if target is None else StatusChange(entry, body, target, position)

    def _as_message(self, entry: Entry, position: int | None = None) -> Message | None:
        body = self._message_body(entry)
        if body is None:
            return None
        return Message(entry, body, self._current_status(entry.id), position)

    def _is_for_me(self, message: Message) -> bool:
        if self._names_me(message):
            return True
        channel = message.entry.content.channel
        members = dm_members(channel)
        if members is not None:
            return self._me.handle in members
        return ALL_CHANNELS in self._me.channels or channel in self._me.channels

    def _names_me(self, message: Message) -> bool:
        return any(
            target == self._me.handle or (ROLE.fullmatch(target) and target in self._me.roles)
            for target in message.body.to
        )

    def _statuses(self, message_id: bytes) -> list[tuple[Entry, StatusBody]]:
        return [
            (entry, body)
            for entry in self._store.referencing(message_id)
            if entry.content.kind is Kind.STATUS
            and (body := self._status_body(entry)) is not None
            and body.target == message_id
        ]

    def _status_heads(self, message_id: bytes) -> list[tuple[Entry, StatusBody]]:
        statuses = self._statuses(message_id)
        replaced = {ref for entry, _ in statuses for ref in entry.content.refs}
        newest_seq: dict[bytes, int] = {}
        for entry, _ in statuses:
            newest_seq[entry.author] = max(newest_seq.get(entry.author, 0), entry.seq)
        return [
            (entry, body)
            for entry, body in statuses
            if entry.id not in replaced and entry.seq == newest_seq[entry.author]
        ]

    def _current_status(self, message_id: bytes) -> Status:
        heads = self._status_heads(message_id)
        if not heads:
            return Status.OPEN
        return max(heads, key=lambda pair: pair[0].id)[1].status


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", text.strip())


def _optional(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = _clean(text)
    return cleaned or None


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))

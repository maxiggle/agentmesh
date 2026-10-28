from agentmesh.identity import es256
from agentmesh.log.body import BodyError, MessageBody, StatusBody
from agentmesh.log.entry import (
    PROTOCOL_VERSION,
    SIG_ALG_ES256,
    Entry,
    EntryFormatError,
    Kind,
)
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError
from agentmesh.log.names import CONTROL_CHANNEL, TEAM_CHANNEL, dm_members

MAX_ENTRY_SIZE = 16 * 1024
MAX_REFS = 16
_CONTROL_KINDS = frozenset({Kind.ROSTER, Kind.KEY_ANNOUNCE})


class DroppedEntryError(Exception):
    pass


class RejectedEntryError(Exception):
    pass


def is_valid_channel(channel: str, kind: Kind) -> bool:
    if kind in _CONTROL_KINDS:
        return channel == CONTROL_CHANNEL
    return TEAM_CHANNEL.fullmatch(channel) is not None or dm_members(channel) is not None


class EntryVerifier:
    """Stateless checks from protocol §5 that need no stored entries or roster.

    `decode` covers steps 1-3 and `check` covers steps 5-7, so a caller can
    run the duplicate check (step 4) in between.
    """

    def __init__(self, team: bytes) -> None:
        self._team = team

    def decode(self, raw: bytes) -> Entry:
        if len(raw) > MAX_ENTRY_SIZE:
            raise DroppedEntryError(f"entry is {len(raw)} bytes, max {MAX_ENTRY_SIZE}")
        try:
            entry = Entry.decode(raw)
        except EntryFormatError as exc:
            raise RejectedEntryError(f"malformed: {exc}") from exc
        if entry.content.v != PROTOCOL_VERSION:
            raise RejectedEntryError(f"unsupported version {entry.content.v}")
        return entry

    def check(self, entry: Entry) -> None:
        self._check_signature(entry)
        if entry.content.team != self._team:
            raise RejectedEntryError("wrong team")
        self._check_shape(entry)

    def verify(self, raw: bytes) -> Entry:
        entry = self.decode(raw)
        self.check(entry)
        return entry

    @staticmethod
    def _check_signature(entry: Entry) -> None:
        if entry.sig.alg != SIG_ALG_ES256:
            raise RejectedEntryError(f"unsupported signature algorithm {entry.sig.alg!r}")
        if not es256.verify(entry.author, entry.sig.value, entry.content.signing_input):
            raise RejectedEntryError("bad signature")

    @staticmethod
    def _check_shape(entry: Entry) -> None:
        content = entry.content
        if content.seq < 1:
            raise RejectedEntryError("seq must start at 1")
        if (content.prev is None) != (content.seq == 1):
            raise RejectedEntryError("prev must be null exactly when seq is 1")
        if not is_valid_channel(content.channel, content.kind):
            raise RejectedEntryError(f"channel {content.channel!r} not allowed for {content.kind}")
        if len(content.refs) > MAX_REFS:
            raise RejectedEntryError(f"more than {MAX_REFS} refs")
        if list(content.refs) != sorted(set(content.refs)):
            raise RejectedEntryError("refs must be sorted and unique")
        if content.kind in (Kind.MESSAGE, Kind.STATUS):
            EntryVerifier._check_body(entry)

    @staticmethod
    def _check_body(entry: Entry) -> None:
        try:
            envelope = Envelope.decode(entry.content.body)
        except EnvelopeError as exc:
            raise RejectedEntryError(f"bad envelope: {exc}") from exc
        if envelope.enc is not Encryption.NONE:
            return
        try:
            if entry.content.kind is Kind.MESSAGE:
                MessageBody.decode(envelope.data)
                return
            status = StatusBody.decode(envelope.data)
        except BodyError as exc:
            raise RejectedEntryError(f"bad body: {exc}") from exc
        if status.target not in entry.content.refs:
            raise RejectedEntryError("status target must be in refs")

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from agentmesh.crypto.sealing import Recipient, Sealed, SealError, open_sealed, public_bytes, seal
from agentmesh.log.entry import PROTOCOL_VERSION, Entry, Kind
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError, body_aad
from agentmesh.log.key_announce import KeyAnnounce, KeyAnnounceError
from agentmesh.log.names import dm_members
from agentmesh.log.roster import Member
from agentmesh.roster.book import RosterBook
from agentmesh.store.base import Store


class KeyDirectory:
    """Each device's current X25519 key: the one in its latest key_announce."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def encryption_keys(self) -> dict[bytes, bytes]:
        latest: dict[bytes, tuple[int, bytes]] = {}
        for arrival in self._store.arrivals(kinds=[Kind.KEY_ANNOUNCE]):
            entry = arrival.entry
            try:
                key = KeyAnnounce.decode(entry.content.body).x25519
            except KeyAnnounceError:
                continue
            if entry.seq >= latest.get(entry.author, (0, b""))[0]:
                latest[entry.author] = (entry.seq, key)
        return {author: key for author, (_, key) in latest.items()}


class SealingCodec:
    """Seals bodies for every device allowed to read the channel (protocol §6)."""

    def __init__(
        self,
        book: RosterBook,
        keys: KeyDirectory,
        private: X25519PrivateKey,
        author: bytes,
        team: bytes,
    ) -> None:
        self._book = book
        self._keys = keys
        self._private = private
        self._public = public_bytes(private)
        self._author = author
        self._team = team

    @property
    def public_key(self) -> bytes:
        return self._public

    def recipients(self, channel: str) -> list[Recipient]:
        roster = self._book.current
        announced = self._keys.encryption_keys()
        found = [Recipient(self._public)]
        if roster is None:
            return found
        readers = [m for m in roster.members if _can_read(m, channel)]
        me = roster.member_for_key(self._author)
        if me is not None and me not in readers:
            readers.append(me)
        for member in readers:
            for device in member.devices:
                key = announced.get(device.key)
                if key is not None and device.key != self._author:
                    found.append(Recipient(key))
        return found

    def seal(self, channel: str, kind: Kind, body: bytes) -> bytes:
        sealed = seal(body, self.recipients(channel), self._aad(self._author, channel, kind))
        roster = self._book.current
        return Envelope(
            Encryption.SEALED,
            0 if roster is None else roster.version,
            sealed.data,
            ephemeral=sealed.ephemeral,
            keys=sealed.keys,
            nonce=sealed.nonce,
        ).encode()

    def open(self, entry: Entry) -> bytes | None:
        try:
            envelope = Envelope.decode(entry.content.body)
        except EnvelopeError:
            return None
        if envelope.enc is Encryption.NONE:
            return envelope.data
        if envelope.ephemeral is None or envelope.nonce is None:
            return None
        sealed = Sealed(envelope.ephemeral, envelope.keys, envelope.nonce, envelope.data)
        aad = self._aad(entry.author, entry.content.channel, entry.content.kind)
        try:
            return open_sealed(sealed, self._private, aad)
        except SealError:
            return None

    def _aad(self, author: bytes, channel: str, kind: Kind) -> bytes:
        return body_aad(PROTOCOL_VERSION, author, self._team, channel, kind.value)


def _can_read(member: Member, channel: str) -> bool:
    members = dm_members(channel)
    return member.handle in members if members is not None else member.can_use(channel)

from dataclasses import dataclass
from enum import StrEnum

from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode

NONCE_SIZE = 12
EPHEMERAL_SIZE = 32
HINT_SIZE = 8
WRAPPED_KEY_SIZE = 48
MAX_RECIPIENTS = 256
_PLAIN_FIELDS = frozenset({"enc", "epoch", "data"})
_SEALED_FIELDS = frozenset({"enc", "epoch", "ephemeral", "keys", "nonce", "data"})


class EnvelopeError(ValueError):
    pass


class Encryption(StrEnum):
    NONE = "none"
    SEALED = "x25519-aes256gcm"


@dataclass(frozen=True, slots=True)
class Envelope:
    enc: Encryption
    epoch: int
    data: bytes
    ephemeral: bytes | None = None
    keys: tuple[tuple[bytes, bytes], ...] = ()
    nonce: bytes | None = None

    def __post_init__(self) -> None:
        if self.epoch < 0:
            raise EnvelopeError("epoch must not be negative")
        if self.enc is Encryption.NONE:
            if self.epoch or self.ephemeral is not None or self.keys or self.nonce is not None:
                raise EnvelopeError("unencrypted envelopes carry only data")
            return
        if self.ephemeral is None or len(self.ephemeral) != EPHEMERAL_SIZE:
            raise EnvelopeError(f"sealed envelopes need a {EPHEMERAL_SIZE}-byte ephemeral key")
        if self.nonce is None or len(self.nonce) != NONCE_SIZE:
            raise EnvelopeError(f"sealed envelopes need a {NONCE_SIZE}-byte nonce")
        if not 1 <= len(self.keys) <= MAX_RECIPIENTS:
            raise EnvelopeError(f"sealed envelopes need 1-{MAX_RECIPIENTS} wrapped keys")
        hints = [hint for hint, _ in self.keys]
        if hints != sorted(set(hints)):
            raise EnvelopeError("wrapped keys must be sorted by hint without repeats")
        for hint, wrapped in self.keys:
            if len(hint) != HINT_SIZE or len(wrapped) != WRAPPED_KEY_SIZE:
                raise EnvelopeError("malformed wrapped key")

    @classmethod
    def plain(cls, data: bytes) -> "Envelope":
        return cls(Encryption.NONE, 0, data)

    def encode(self) -> bytes:
        fields: dict[str, CborValue] = {
            "enc": self.enc.value,
            "epoch": self.epoch,
            "data": self.data,
        }
        if self.enc is Encryption.SEALED:
            fields["ephemeral"] = self.ephemeral
            fields["keys"] = [[hint, wrapped] for hint, wrapped in self.keys]
            fields["nonce"] = self.nonce
        return encode(fields)

    @classmethod
    def decode(cls, raw: bytes) -> "Envelope":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise EnvelopeError(str(exc)) from exc
        if not isinstance(value, dict):
            raise EnvelopeError("envelope must be a map")
        enc = value.get("enc")
        if enc not in Encryption.__members__.values():
            raise EnvelopeError(f"unknown encryption {enc!r}")
        encryption = Encryption(str(enc))
        expected = _PLAIN_FIELDS if encryption is Encryption.NONE else _SEALED_FIELDS
        if value.keys() != expected:
            raise EnvelopeError(f"{encryption} envelope fields must be {sorted(expected)}")
        epoch = value["epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise EnvelopeError("epoch must be an integer")
        if encryption is Encryption.NONE:
            return cls(encryption, epoch, _bytes(value["data"]))
        return cls(
            encryption,
            epoch,
            _bytes(value["data"]),
            ephemeral=_bytes(value["ephemeral"]),
            keys=_keys(value["keys"]),
            nonce=_bytes(value["nonce"]),
        )


def body_aad(version: int, author: bytes, team: bytes, channel: str, kind: str) -> bytes:
    """Binds a ciphertext to its author, team, channel and kind (protocol §6)."""
    return encode([version, author, team, channel, kind])


def _bytes(value: CborValue) -> bytes:
    if not isinstance(value, bytes):
        raise EnvelopeError("expected bytes")
    return value


def _keys(value: CborValue) -> tuple[tuple[bytes, bytes], ...]:
    if not isinstance(value, list):
        raise EnvelopeError("keys must be an array")
    pairs = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:  # noqa: PLR2004 - [hint, wrapped key]
            raise EnvelopeError("each key is [hint, wrapped]")
        pairs.append((_bytes(pair[0]), _bytes(pair[1])))
    return tuple(pairs)

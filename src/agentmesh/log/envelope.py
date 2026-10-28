from dataclasses import dataclass
from enum import StrEnum

from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode

NONCE_SIZE = 12


class EnvelopeError(ValueError):
    pass


class Encryption(StrEnum):
    NONE = "none"
    AES256GCM = "aes256gcm"


@dataclass(frozen=True, slots=True)
class Envelope:
    enc: Encryption
    epoch: int
    data: bytes
    nonce: bytes | None = None

    def __post_init__(self) -> None:
        if self.epoch < 0:
            raise EnvelopeError("epoch must not be negative")
        if self.enc is Encryption.NONE and (self.epoch != 0 or self.nonce is not None):
            raise EnvelopeError("unencrypted envelopes have epoch 0 and no nonce")
        if self.enc is Encryption.AES256GCM and (
            self.nonce is None or len(self.nonce) != NONCE_SIZE
        ):
            raise EnvelopeError(f"aes256gcm needs a {NONCE_SIZE}-byte nonce")

    @classmethod
    def plain(cls, data: bytes) -> "Envelope":
        return cls(Encryption.NONE, 0, data)

    def encode(self) -> bytes:
        fields: dict[str, CborValue] = {
            "enc": self.enc.value,
            "epoch": self.epoch,
            "data": self.data,
        }
        if self.nonce is not None:
            fields["nonce"] = self.nonce
        return encode(fields)

    @classmethod
    def decode(cls, raw: bytes) -> "Envelope":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise EnvelopeError(str(exc)) from exc
        if not isinstance(value, dict) or not {"enc", "epoch", "data"} <= value.keys():
            raise EnvelopeError("envelope must be a map with enc, epoch and data")
        if not value.keys() <= {"enc", "epoch", "data", "nonce"}:
            raise EnvelopeError("unknown envelope fields")
        enc, epoch, data, nonce = value["enc"], value["epoch"], value["data"], value.get("nonce")
        if not isinstance(enc, str) or enc not in Encryption.__members__.values():
            raise EnvelopeError(f"unknown encryption {enc!r}")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise EnvelopeError("epoch must be an integer")
        if not isinstance(data, bytes) or ("nonce" in value and not isinstance(nonce, bytes)):
            raise EnvelopeError("data and nonce must be bytes")
        return cls(Encryption(enc), epoch, data, nonce if isinstance(nonce, bytes) else None)

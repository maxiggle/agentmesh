from dataclasses import dataclass

from ehor.log.canonical import CanonicalError, decode, encode

IROH_ID_SIZE = 32


class EndpointAnnounceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EndpointAnnounce:
    iroh: bytes

    def __post_init__(self) -> None:
        if len(self.iroh) != IROH_ID_SIZE:
            raise EndpointAnnounceError(f"iroh endpoint ID must be {IROH_ID_SIZE} bytes")

    def encode(self) -> bytes:
        return encode({"iroh": self.iroh})

    @classmethod
    def decode(cls, raw: bytes) -> "EndpointAnnounce":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise EndpointAnnounceError(str(exc)) from exc
        if not isinstance(value, dict) or value.keys() != {"iroh"}:
            raise EndpointAnnounceError("endpoint_announce must be a map with only iroh")
        iroh = value["iroh"]
        if not isinstance(iroh, bytes):
            raise EndpointAnnounceError("iroh must be bytes")
        return cls(iroh)

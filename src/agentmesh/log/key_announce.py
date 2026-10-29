from dataclasses import dataclass

from agentmesh.log.canonical import CanonicalError, decode, encode

X25519_KEY_SIZE = 32


class KeyAnnounceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KeyAnnounce:
    x25519: bytes

    def __post_init__(self) -> None:
        if len(self.x25519) != X25519_KEY_SIZE:
            raise KeyAnnounceError(f"x25519 key must be {X25519_KEY_SIZE} bytes")

    def encode(self) -> bytes:
        return encode({"x25519": self.x25519})

    @classmethod
    def decode(cls, raw: bytes) -> "KeyAnnounce":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise KeyAnnounceError(str(exc)) from exc
        if not isinstance(value, dict) or value.keys() != {"x25519"}:
            raise KeyAnnounceError("key_announce must be a map with only x25519")
        key = value["x25519"]
        if not isinstance(key, bytes):
            raise KeyAnnounceError("x25519 must be bytes")
        return cls(key)

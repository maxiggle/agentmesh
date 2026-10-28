from dataclasses import dataclass
from typing import Protocol


class IdentityError(Exception):
    pass


class KeyExistsError(IdentityError):
    pass


class KeyNotFoundError(IdentityError):
    pass


class InvalidLabelError(IdentityError):
    pass


@dataclass(frozen=True, slots=True)
class KeyHandle:
    backend: str
    key_id: str


class Signer(Protocol):
    def create_key(self, label: str) -> KeyHandle: ...

    def public_key(self, handle: KeyHandle) -> bytes:
        """Return the SEC1 compressed P-256 public key (33 bytes)."""
        ...

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        """Sign `data` with ES256 and return the raw 64-byte `r || s` signature."""
        ...

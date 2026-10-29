import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class IdentityError(Exception):
    pass


class KeyExistsError(IdentityError):
    pass


class KeyNotFoundError(IdentityError):
    pass


class InvalidLabelError(IdentityError):
    pass


class HardwareUnavailableError(IdentityError):
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


def key_path(directory: Path, label: str, suffix: str) -> Path:
    """Where a backend keeps key `label`; the label check stops it escaping `directory`."""
    if not _LABEL.fullmatch(label):
        raise InvalidLabelError(label)
    return directory / f"{label}{suffix}"

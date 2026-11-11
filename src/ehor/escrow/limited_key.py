import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_KEY_FILE_MODE = 0o600
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_RAW = (serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


class LimitedKeyError(Exception):
    pass


def base58(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    digits = ""
    while number:
        number, remainder = divmod(number, 58)
        digits = _ALPHABET[remainder] + digits
    zeros = len(data) - len(data.lstrip(b"\0"))
    return "1" * zeros + digits


class LimitedKey:
    """The device's NEAR function-call key: an Ed25519 key that never leaves this machine."""

    __slots__ = ("_key",)

    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key

    @classmethod
    def generate(cls) -> "LimitedKey":
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key(self) -> str:
        raw = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return "ed25519:" + base58(raw)

    def near_secret(self) -> str:
        """The key in NEAR's `ed25519:<seed||public>` form, for the RPC client only."""
        seed = self._key.private_bytes(*_RAW)
        public = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return "ed25519:" + base58(seed + public)

    def __repr__(self) -> str:
        return f"LimitedKey({self.public_key})"


class LimitedKeyFile:
    """The limited key, kept as a PKCS#8 file encrypted with the device passphrase."""

    def __init__(self, path: Path, passphrase: bytes) -> None:
        if not passphrase:
            raise LimitedKeyError("passphrase must not be empty")
        self._path = path
        self._passphrase = passphrase

    def exists(self) -> bool:
        return self._path.exists()

    def create(self) -> LimitedKey:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(self._passphrase),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
        except FileExistsError as exc:
            raise LimitedKeyError(f"{self._path} already exists") from exc
        with os.fdopen(fd, "wb") as file:
            file.write(pem)
        return LimitedKey(key)

    def load(self) -> LimitedKey:
        try:
            pem = self._path.read_bytes()
        except FileNotFoundError as exc:
            raise LimitedKeyError(f"no NEAR key at {self._path}") from exc
        try:
            key = serialization.load_pem_private_key(pem, self._passphrase)
        except ValueError as exc:
            raise LimitedKeyError("cannot decrypt the NEAR key; is the passphrase right?") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise LimitedKeyError(f"{self._path} is not an Ed25519 key")
        return LimitedKey(key)

    def load_or_create(self) -> LimitedKey:
        return self.load() if self.exists() else self.create()

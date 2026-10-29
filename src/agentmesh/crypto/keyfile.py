import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

_KEY_FILE_MODE = 0o600


class KeyFileError(Exception):
    pass


class EncryptionKeyFile:
    """This device's X25519 decryption key, kept as a passphrase-encrypted PKCS#8 file."""

    def __init__(self, path: Path, passphrase: bytes) -> None:
        if not passphrase:
            raise KeyFileError("passphrase must not be empty")
        self._path = path
        self._passphrase = passphrase

    def exists(self) -> bool:
        return self._path.exists()

    def create(self) -> X25519PrivateKey:
        key = X25519PrivateKey.generate()
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(self._passphrase),
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
        except FileExistsError as exc:
            raise KeyFileError(f"{self._path} already exists") from exc
        with os.fdopen(fd, "wb") as file:
            file.write(pem)
        return key

    def load(self) -> X25519PrivateKey:
        try:
            pem = self._path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyFileError(f"no encryption key at {self._path}") from exc
        try:
            key = serialization.load_pem_private_key(pem, self._passphrase)
        except ValueError as exc:
            raise KeyFileError("cannot decrypt the encryption key") from exc
        if not isinstance(key, X25519PrivateKey):
            raise KeyFileError(f"{self._path} is not an X25519 key")
        return key

    def load_or_create(self) -> X25519PrivateKey:
        return self.load() if self.exists() else self.create()

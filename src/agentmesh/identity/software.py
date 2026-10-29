import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.identity.base import (
    IdentityError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    key_path,
)
from agentmesh.identity.es256 import compress_public_key, raw_signature

_KEY_FILE_MODE = 0o600


class SoftwareSigner:
    """P-256 keys stored as passphrase-encrypted PKCS#8 files.

    Lower trust than hardware backends: the key can be copied off disk
    together with the passphrase.
    """

    backend = "software"

    def __init__(self, directory: Path, passphrase: bytes) -> None:
        if not passphrase:
            raise IdentityError("passphrase must not be empty")
        self._directory = directory
        self._passphrase = passphrase
        self._loaded: dict[str, ec.EllipticCurvePrivateKey] = {}

    def create_key(self, label: str) -> KeyHandle:
        path = self._path(label)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(self._passphrase),
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
        except FileExistsError as exc:
            raise KeyExistsError(label) from exc
        with os.fdopen(fd, "wb") as file:
            file.write(pem)
        self._loaded[label] = key
        return KeyHandle(self.backend, label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return compress_public_key(self._private_key(handle).public_key())

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        der = self._private_key(handle).sign(data, ec.ECDSA(hashes.SHA256()))
        return raw_signature(der)

    def _private_key(self, handle: KeyHandle) -> ec.EllipticCurvePrivateKey:
        if handle.backend != self.backend:
            raise KeyNotFoundError(f"{handle.backend}:{handle.key_id}")
        if handle.key_id in self._loaded:
            return self._loaded[handle.key_id]
        path = self._path(handle.key_id)
        try:
            pem = path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyNotFoundError(handle.key_id) from exc
        try:
            key = serialization.load_pem_private_key(pem, self._passphrase)
        except ValueError as exc:
            raise IdentityError(f"cannot decrypt key {handle.key_id}") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise IdentityError(f"key {handle.key_id} is not a P-256 key")
        self._loaded[handle.key_id] = key
        return key

    def _path(self, label: str) -> Path:
        return key_path(self._directory, label, ".key")

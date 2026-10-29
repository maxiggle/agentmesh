import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentmesh.identity.assertion import (
    RP_ID,
    Assertion,
    InvalidAssertionError,
    check_assertion,
    client_data_hash,
)
from agentmesh.identity.base import (
    IdentityError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    key_path,
)
from agentmesh.identity.es256 import PUBLIC_KEY_SIZE

_SUFFIX = ".fido2"
_FILE_MODE = 0o600
_USER_ID_SIZE = 16


@dataclass(frozen=True, slots=True)
class Credential:
    credential_id: bytes
    public_key: bytes


class Authenticator(Protocol):
    def make_credential(self, rp_id: str, user_id: bytes, user_name: str) -> Credential:
        """Create a non-discoverable ES256 credential; the key never leaves the device."""
        ...

    def get_assertion(self, rp_id: str, credential_id: bytes, client_data_hash: bytes) -> Assertion:
        """Sign with user presence; the device waits for a touch."""
        ...


class SecurityKeySigner:
    """FIDO2 credentials on a security key.

    Only the credential ID and public key are kept on disk. They are not secret: the
    credential is useless without the physical key and a touch.
    """

    backend = "security-key"

    def __init__(self, directory: Path, connect: Callable[[], Authenticator]) -> None:
        self._directory = directory
        self._connect = connect
        self._authenticator: Authenticator | None = None

    def labels(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.stem for path in self._directory.glob(f"*{_SUFFIX}"))

    def create_key(self, label: str) -> KeyHandle:
        path = self._path(label)
        if path.exists():
            raise KeyExistsError(label)
        user_id = secrets.token_bytes(_USER_ID_SIZE)
        credential = self._device().make_credential(RP_ID, user_id, f"agentmesh {label}")
        if len(credential.public_key) != PUBLIC_KEY_SIZE:
            raise IdentityError("security key returned a malformed public key")
        record = {
            "credential_id": credential.credential_id.hex(),
            "public_key": credential.public_key.hex(),
        }
        self._directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        except FileExistsError as exc:
            raise KeyExistsError(label) from exc
        with os.fdopen(fd, "w") as file:
            json.dump(record, file)
        return KeyHandle(self.backend, label)

    def forget(self, handle: KeyHandle) -> None:
        """Drop the local record of a credential, e.g. when enrolling it failed."""
        self._path(self._label(handle)).unlink(missing_ok=True)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self._load(handle).public_key

    def sign_assertion(self, handle: KeyHandle, message: bytes) -> Assertion:
        credential = self._load(handle)
        assertion = self._device().get_assertion(
            RP_ID, credential.credential_id, client_data_hash(message)
        )
        try:
            check_assertion(credential.public_key, assertion, message)
        except InvalidAssertionError as exc:
            raise IdentityError(f"security key returned an unusable assertion: {exc}") from exc
        return assertion

    def _device(self) -> Authenticator:
        if self._authenticator is None:
            self._authenticator = self._connect()
        return self._authenticator

    def _load(self, handle: KeyHandle) -> Credential:
        path = self._path(self._label(handle))
        try:
            record = json.loads(path.read_text())
            return Credential(
                bytes.fromhex(record["credential_id"]), bytes.fromhex(record["public_key"])
            )
        except FileNotFoundError as exc:
            raise KeyNotFoundError(handle.key_id) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise IdentityError(f"invalid security key record {path}") from exc

    def _label(self, handle: KeyHandle) -> str:
        if handle.backend != self.backend:
            raise KeyNotFoundError(f"{handle.backend}:{handle.key_id}")
        return handle.key_id

    def _path(self, label: str) -> Path:
        return key_path(self._directory, label, _SUFFIX)

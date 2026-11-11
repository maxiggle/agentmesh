import hashlib
import os
import sys
import tempfile
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from ehor.identity.base import (
    HardwareUnavailableError,
    IdentityError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    key_path,
)
from ehor.identity.es256 import PUBLIC_KEY_SIZE, SIGNATURE_SIZE
from ehor.identity.tools import DEFAULT_TIMEOUT, ToolError, ToolRunner, run_tool

_SOURCE = "enclave_helper.swift"
_BUILD_TIMEOUT = 600.0
_PRIVATE_DIR_MODE = 0o700
_KEY_FILE_MODE = 0o600
_MAX_REASON = 200
_APPROVAL_TIMEOUT = 120.0


class EnclaveHelper(Protocol):
    def __call__(
        self, args: Sequence[str], stdin: bytes = b"", timeout: float = DEFAULT_TIMEOUT
    ) -> bytes: ...


class SwiftHelper:
    """Runs the bundled Swift helper, compiling it on first use.

    CryptoKit is the only supported way to create Secure Enclave keys outside the
    keychain, and it has no C or Python API.
    """

    def __init__(self, cache: Path, runner: ToolRunner = run_tool) -> None:
        self._cache = cache
        self._runner = runner

    def __call__(
        self, args: Sequence[str], stdin: bytes = b"", timeout: float = DEFAULT_TIMEOUT
    ) -> bytes:
        return self._runner([str(self.binary()), *args], stdin=stdin, timeout=timeout)

    def binary(self) -> Path:
        source = files("ehor.identity").joinpath(_SOURCE).read_bytes()
        target = self._cache / f"ehor-se-{hashlib.sha256(source).hexdigest()[:16]}"
        if target.exists():
            return target
        if sys.platform != "darwin":
            raise HardwareUnavailableError("the Secure Enclave is only available on Macs")
        self._cache.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        with tempfile.TemporaryDirectory(dir=self._cache) as work:
            main = Path(work) / "main.swift"
            main.write_bytes(source)
            built = Path(work) / "helper"
            try:
                run_tool(
                    ["xcrun", "swiftc", "-O", str(main), "-o", str(built)],
                    timeout=_BUILD_TIMEOUT,
                )
            except ToolError as exc:
                raise HardwareUnavailableError(
                    f"cannot build the Secure Enclave helper; install the Xcode command line "
                    f"tools ({exc})"
                ) from exc
            built.replace(target)
        return target


class _EnclaveKeys:
    """Wrapped key blobs on disk, one file per label; only this Mac's enclave can unwrap them."""

    def __init__(
        self, directory: Path, helper: EnclaveHelper, backend: str, suffix: str, create: str
    ) -> None:
        self._directory = directory
        self._helper = helper
        self._backend = backend
        self._suffix = suffix
        self._create = create
        self._public: dict[str, bytes] = {}

    def create(self, label: str) -> KeyHandle:
        path = key_path(self._directory, label, self._suffix)
        if path.exists():
            raise KeyExistsError(label)
        blob = self._helper([self._create])
        self._directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
        except FileExistsError as exc:
            raise KeyExistsError(label) from exc
        with os.fdopen(fd, "wb") as file:
            file.write(blob)
        return KeyHandle(self._backend, label)

    def public_key(self, handle: KeyHandle) -> bytes:
        if handle.key_id not in self._public:
            key = self._helper(["public", str(self.path(handle))])
            if len(key) != PUBLIC_KEY_SIZE:
                raise IdentityError("Secure Enclave helper returned a malformed public key")
            self._public[handle.key_id] = key
        return self._public[handle.key_id]

    def sign(
        self, handle: KeyHandle, data: bytes, *reason: str, timeout: float = DEFAULT_TIMEOUT
    ) -> bytes:
        args = ["sign", str(self.path(handle)), *reason]
        signature = self._helper(args, stdin=data, timeout=timeout)
        if len(signature) != SIGNATURE_SIZE:
            raise IdentityError("Secure Enclave helper returned a malformed signature")
        return signature

    def path(self, handle: KeyHandle) -> Path:
        if handle.backend != self._backend:
            raise KeyNotFoundError(f"{handle.backend}:{handle.key_id}")
        path = key_path(self._directory, handle.key_id, self._suffix)
        if not path.exists():
            raise KeyNotFoundError(handle.key_id)
        return path


class SecureEnclaveSigner:
    """P-256 keys generated inside this Mac's Secure Enclave.

    The key file holds a blob that only this Mac's enclave can unwrap, so the private
    key cannot be copied off the machine. Signing needs no approval, because these keys
    sign every message.
    """

    backend = "secure-enclave"

    def __init__(self, directory: Path, helper: EnclaveHelper) -> None:
        self._helper = helper
        self._keys = _EnclaveKeys(directory, helper, self.backend, ".se", "create")

    def available(self) -> bool:
        try:
            self._helper(["available"])
        except IdentityError:
            return False
        return True

    def create_key(self, label: str) -> KeyHandle:
        return self._keys.create(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self._keys.public_key(handle)

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        return self._keys.sign(handle, data)


class SecureEnclavePresenceSigner:
    """Secure Enclave keys that need Touch ID or the login password for every signature.

    The enclave enforces this, so even a process running as the user cannot sign without
    the person approving. There is deliberately no plain `sign`.
    """

    backend = "secure-enclave-presence"

    def __init__(self, directory: Path, helper: EnclaveHelper) -> None:
        self._keys = _EnclaveKeys(directory, helper, self.backend, ".sep", "create-presence")

    def create_key(self, label: str) -> KeyHandle:
        return self._keys.create(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self._keys.public_key(handle)

    def sign_with_presence(self, handle: KeyHandle, data: bytes, reason: str) -> bytes:
        if not reason.strip() or len(reason) > _MAX_REASON:
            raise IdentityError(f"approval reason must be 1 to {_MAX_REASON} characters")
        return self._keys.sign(handle, data, reason, timeout=_APPROVAL_TIMEOUT)

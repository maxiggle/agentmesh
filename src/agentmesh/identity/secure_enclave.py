import hashlib
import os
import sys
import tempfile
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from agentmesh.identity.base import (
    HardwareUnavailableError,
    IdentityError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    key_path,
)
from agentmesh.identity.es256 import PUBLIC_KEY_SIZE, SIGNATURE_SIZE
from agentmesh.identity.tools import ToolError, ToolRunner, run_tool

_SOURCE = "enclave_helper.swift"
_BUILD_TIMEOUT = 600.0
_PRIVATE_DIR_MODE = 0o700
_KEY_FILE_MODE = 0o600


class EnclaveHelper(Protocol):
    def __call__(self, args: Sequence[str], stdin: bytes = b"") -> bytes: ...


class SwiftHelper:
    """Runs the bundled Swift helper, compiling it on first use.

    CryptoKit is the only supported way to create Secure Enclave keys outside the
    keychain, and it has no C or Python API.
    """

    def __init__(self, cache: Path, runner: ToolRunner = run_tool) -> None:
        self._cache = cache
        self._runner = runner

    def __call__(self, args: Sequence[str], stdin: bytes = b"") -> bytes:
        return self._runner([str(self.binary()), *args], stdin=stdin)

    def binary(self) -> Path:
        source = files("agentmesh.identity").joinpath(_SOURCE).read_bytes()
        target = self._cache / f"agentmesh-se-{hashlib.sha256(source).hexdigest()[:16]}"
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


class SecureEnclaveSigner:
    """P-256 keys generated inside this Mac's Secure Enclave.

    The key file holds a blob that only this Mac's enclave can unwrap, so the private
    key cannot be copied off the machine.
    """

    backend = "secure-enclave"

    def __init__(self, directory: Path, helper: EnclaveHelper) -> None:
        self._directory = directory
        self._helper = helper
        self._public: dict[str, bytes] = {}

    def available(self) -> bool:
        try:
            self._helper(["available"])
        except IdentityError:
            return False
        return True

    def create_key(self, label: str) -> KeyHandle:
        path = key_path(self._directory, label, ".se")
        if path.exists():
            raise KeyExistsError(label)
        blob = self._helper(["create"])
        self._directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
        except FileExistsError as exc:
            raise KeyExistsError(label) from exc
        with os.fdopen(fd, "wb") as file:
            file.write(blob)
        return KeyHandle(self.backend, label)

    def public_key(self, handle: KeyHandle) -> bytes:
        if handle.key_id not in self._public:
            key = self._helper(["public", str(self._existing(handle))])
            if len(key) != PUBLIC_KEY_SIZE:
                raise IdentityError("Secure Enclave helper returned a malformed public key")
            self._public[handle.key_id] = key
        return self._public[handle.key_id]

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        signature = self._helper(["sign", str(self._existing(handle))], stdin=data)
        if len(signature) != SIGNATURE_SIZE:
            raise IdentityError("Secure Enclave helper returned a malformed signature")
        return signature

    def _existing(self, handle: KeyHandle) -> Path:
        if handle.backend != self.backend:
            raise KeyNotFoundError(f"{handle.backend}:{handle.key_id}")
        path = key_path(self._directory, handle.key_id, ".se")
        if not path.exists():
            raise KeyNotFoundError(handle.key_id)
        return path

import hashlib
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.identity.base import (
    IdentityError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    key_path,
)
from agentmesh.identity.es256 import compress_public_key, raw_signature
from agentmesh.identity.tools import ToolError, ToolRunner, run_tool

_KEY_ALGORITHM = "ecc256:ecdsa-sha256"
_KEY_ATTRIBUTES = "fixedtpm|fixedparent|sensitivedataorigin|userwithauth|sign"
_PRIMARY = "primary.ctx"
_KEY_FILE_MODE = 0o600


class TpmSigner:
    """P-256 keys created inside a TPM 2.0 chip, driven through tpm2-tools.

    The stored key is wrapped by a primary key the TPM re-derives from its owner seed,
    so it only loads on this TPM. Nothing is written to the TPM's persistent memory.
    Each command leaves its objects loaded, so the TPM must sit behind a resource
    manager such as the kernel's /dev/tpmrm0, which frees them when the command exits.
    """

    backend = "tpm"

    def __init__(self, directory: Path, runner: ToolRunner = run_tool) -> None:
        self._directory = directory
        self._runner = runner
        self._work = tempfile.TemporaryDirectory(prefix="agentmesh-tpm-")
        self._loaded: set[str] = set()
        self._public: dict[str, bytes] = {}

    def available(self) -> bool:
        try:
            self._run("tpm2_getcap", "properties-fixed")
        except IdentityError:
            return False
        return True

    def create_key(self, label: str) -> KeyHandle:
        public, private = self._paths(label)
        if public.exists() or private.exists():
            raise KeyExistsError(label)
        self._create_primary()
        self._run(
            "tpm2_create",
            "-C",
            _PRIMARY,
            "-G",
            _KEY_ALGORITHM,
            "-a",
            _KEY_ATTRIBUTES,
            "-u",
            "new.pub",
            "-r",
            "new.priv",
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        _write_new(public, self._work_file("new.pub").read_bytes(), label)
        _write_new(private, self._work_file("new.priv").read_bytes(), label)
        return KeyHandle(self.backend, label)

    def public_key(self, handle: KeyHandle) -> bytes:
        if handle.key_id not in self._public:
            context = self._load(handle)
            pem = f"{handle.key_id}.pem"
            self._run("tpm2_readpublic", "-c", context, "-f", "pem", "-o", pem)
            key = serialization.load_pem_public_key(self._work_file(pem).read_bytes())
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise IdentityError(f"TPM key {handle.key_id} is not a P-256 key")
            self._public[handle.key_id] = compress_public_key(key)
        return self._public[handle.key_id]

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        digest = hashlib.sha256(data).digest()
        try:
            return self._sign(handle, digest)
        except ToolError:
            # Saved contexts do not survive a TPM reset, so reload the key once.
            self._loaded.discard(handle.key_id)
            return self._sign(handle, digest)

    def _sign(self, handle: KeyHandle, digest: bytes) -> bytes:
        context = self._load(handle)
        self._work_file("digest.bin").write_bytes(digest)
        self._run(
            "tpm2_sign",
            "-c",
            context,
            "-g",
            "sha256",
            "-s",
            "ecdsa",
            "-d",
            "-f",
            "plain",
            "-o",
            "signature.der",
            "digest.bin",
        )
        try:
            return raw_signature(self._work_file("signature.der").read_bytes())
        except ValueError as exc:
            raise IdentityError("tpm2_sign returned a malformed signature") from exc

    def _load(self, handle: KeyHandle) -> str:
        if handle.backend != self.backend:
            raise KeyNotFoundError(f"{handle.backend}:{handle.key_id}")
        public, private = self._paths(handle.key_id)
        if not (public.exists() and private.exists()):
            raise KeyNotFoundError(handle.key_id)
        context = f"{handle.key_id}.ctx"
        if handle.key_id not in self._loaded:
            self._create_primary()
            self._run(
                "tpm2_load", "-C", _PRIMARY, "-u", str(public), "-r", str(private), "-c", context
            )
            self._loaded.add(handle.key_id)
        return context

    def _create_primary(self) -> None:
        self._run("tpm2_createprimary", "-C", "o", "-g", "sha256", "-G", "ecc256", "-c", _PRIMARY)

    def _run(self, *args: str) -> bytes:
        return self._runner(args, cwd=Path(self._work.name))

    def _work_file(self, name: str) -> Path:
        return Path(self._work.name) / name

    def _paths(self, label: str) -> tuple[Path, Path]:
        return (
            key_path(self._directory, label, ".tpm.pub"),
            key_path(self._directory, label, ".tpm.priv"),
        )


def _write_new(path: Path, data: bytes, label: str) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_FILE_MODE)
    except FileExistsError as exc:
        raise KeyExistsError(label) from exc
    with os.fdopen(fd, "wb") as file:
        file.write(data)

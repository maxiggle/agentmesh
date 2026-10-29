import sys
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agentmesh.identity.base import Signer
from agentmesh.identity.secure_enclave import SecureEnclaveSigner, SwiftHelper
from agentmesh.identity.software import SoftwareSigner
from agentmesh.identity.tpm import TpmSigner


class Backend(StrEnum):
    SOFTWARE = "software"
    SECURE_ENCLAVE = "secure-enclave"
    TPM = "tpm"

    @property
    def hardware(self) -> bool:
        return self is not Backend.SOFTWARE


class HardwareSigner(Signer, Protocol):
    def available(self) -> bool: ...


def hardware_candidates(platform: str = sys.platform) -> tuple[Backend, ...]:
    if platform == "darwin":
        return (Backend.SECURE_ENCLAVE,)
    if platform.startswith("linux"):
        return (Backend.TPM,)
    return ()


def hardware_signer(backend: Backend, keys: Path, *, tools: Path) -> HardwareSigner:
    if backend is Backend.SECURE_ENCLAVE:
        return SecureEnclaveSigner(keys, SwiftHelper(tools))
    if backend is Backend.TPM:
        return TpmSigner(keys)
    raise ValueError(f"{backend} is not a hardware backend")


def open_signer(backend: Backend, keys: Path, passphrase: bytes, *, tools: Path) -> Signer:
    """The signer for `backend`; `tools` caches helper programs that hardware backends build."""
    if backend is Backend.SOFTWARE:
        return SoftwareSigner(keys, passphrase)
    return hardware_signer(backend, keys, tools=tools)


def detect_hardware(keys: Path, *, tools: Path, platform: str = sys.platform) -> Backend | None:
    """The first hardware backend that works on this machine, if any."""
    for backend in hardware_candidates(platform):
        if hardware_signer(backend, keys, tools=tools).available():
            return backend
    return None

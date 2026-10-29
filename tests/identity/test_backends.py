from pathlib import Path

import pytest

from agentmesh.identity.backends import (
    Backend,
    detect_hardware,
    hardware_candidates,
    hardware_signer,
    open_signer,
)
from agentmesh.identity.secure_enclave import SecureEnclaveSigner
from agentmesh.identity.software import SoftwareSigner
from agentmesh.identity.tpm import TpmSigner


@pytest.mark.parametrize(
    ("backend", "kind"),
    [
        (Backend.SOFTWARE, SoftwareSigner),
        (Backend.SECURE_ENCLAVE, SecureEnclaveSigner),
        (Backend.TPM, TpmSigner),
    ],
)
def test_open_signer(tmp_path: Path, backend: Backend, kind: type) -> None:
    signer = open_signer(backend, tmp_path / "keys", b"pass", tools=tmp_path / "bin")
    assert isinstance(signer, kind)


def test_only_software_is_not_hardware() -> None:
    assert [b for b in Backend if not b.hardware] == [Backend.SOFTWARE]


def test_software_has_no_hardware_signer(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a hardware backend"):
        hardware_signer(Backend.SOFTWARE, tmp_path, tools=tmp_path)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", (Backend.SECURE_ENCLAVE,)),
        ("linux", (Backend.TPM,)),
        ("win32", ()),
    ],
)
def test_hardware_candidates(platform: str, expected: tuple[Backend, ...]) -> None:
    assert hardware_candidates(platform) == expected


def test_nothing_to_detect_without_candidates(tmp_path: Path) -> None:
    assert detect_hardware(tmp_path, tools=tmp_path, platform="win32") is None

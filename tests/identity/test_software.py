import sys
from pathlib import Path

import pytest

from agentmesh.identity import (
    IdentityError,
    InvalidLabelError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    Signer,
    es256,
)
from agentmesh.identity.software import SoftwareSigner

PASSPHRASE = b"correct horse"


@pytest.fixture
def signer(tmp_path: Path) -> SoftwareSigner:
    return SoftwareSigner(tmp_path, PASSPHRASE)


def test_implements_signer_protocol(signer: SoftwareSigner) -> None:
    protocol: Signer = signer
    assert protocol is signer


def test_signs_and_verifies(signer: SoftwareSigner) -> None:
    handle = signer.create_key("laptop")
    signature = signer.sign(handle, b"data")
    assert es256.verify(signer.public_key(handle), signature, b"data")


def test_key_survives_reload(tmp_path: Path, signer: SoftwareSigner) -> None:
    handle = signer.create_key("laptop")
    reloaded = SoftwareSigner(tmp_path, PASSPHRASE)
    assert reloaded.public_key(handle) == signer.public_key(handle)
    assert es256.verify(signer.public_key(handle), reloaded.sign(handle, b"x"), b"x")


def test_key_file_is_encrypted(tmp_path: Path, signer: SoftwareSigner) -> None:
    signer.create_key("laptop")
    assert b"ENCRYPTED PRIVATE KEY" in (tmp_path / "laptop.key").read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_key_file_is_owner_only(tmp_path: Path, signer: SoftwareSigner) -> None:
    signer.create_key("laptop")
    assert (tmp_path / "laptop.key").stat().st_mode & 0o777 == 0o600


def test_wrong_passphrase_fails(tmp_path: Path, signer: SoftwareSigner) -> None:
    handle = signer.create_key("laptop")
    with pytest.raises(IdentityError):
        SoftwareSigner(tmp_path, b"wrong").sign(handle, b"x")


def test_empty_passphrase_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IdentityError):
        SoftwareSigner(tmp_path, b"")


def test_duplicate_label_is_refused(signer: SoftwareSigner) -> None:
    signer.create_key("laptop")
    with pytest.raises(KeyExistsError):
        signer.create_key("laptop")


@pytest.mark.parametrize("label", ["", "Laptop", "../escape", "a/b", "-x", "x" * 65])
def test_invalid_label_is_refused(signer: SoftwareSigner, label: str) -> None:
    with pytest.raises(InvalidLabelError):
        signer.create_key(label)


def test_unknown_key(signer: SoftwareSigner) -> None:
    with pytest.raises(KeyNotFoundError):
        signer.sign(KeyHandle("software", "missing"), b"x")


def test_foreign_backend_handle(signer: SoftwareSigner) -> None:
    signer.create_key("laptop")
    with pytest.raises(KeyNotFoundError):
        signer.sign(KeyHandle("tpm", "laptop"), b"x")

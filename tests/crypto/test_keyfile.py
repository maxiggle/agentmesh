import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.crypto.keyfile import EncryptionKeyFile, KeyFileError
from agentmesh.crypto.sealing import public_bytes

PASSPHRASE = b"correct horse"


def test_create_then_load(tmp_path: Path) -> None:
    file = EncryptionKeyFile(tmp_path / "keys" / "encryption.key", PASSPHRASE)
    assert not file.exists()
    created = file.create()
    assert file.exists()
    assert public_bytes(file.load()) == public_bytes(created)
    assert public_bytes(file.load_or_create()) == public_bytes(created)
    assert b"ENCRYPTED PRIVATE KEY" in (tmp_path / "keys" / "encryption.key").read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_file_is_owner_only(tmp_path: Path) -> None:
    EncryptionKeyFile(tmp_path / "e.key", PASSPHRASE).create()
    assert (tmp_path / "e.key").stat().st_mode & 0o777 == 0o600


def test_never_overwrites(tmp_path: Path) -> None:
    file = EncryptionKeyFile(tmp_path / "e.key", PASSPHRASE)
    file.create()
    with pytest.raises(KeyFileError, match="already exists"):
        file.create()


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("missing", "no encryption key"),
        ("wrong", "cannot decrypt"),
        ("not-x25519", "not an X25519"),
    ],
)
def test_load_errors(tmp_path: Path, setup: str, error: str) -> None:
    path = tmp_path / "e.key"
    if setup == "wrong":
        EncryptionKeyFile(path, b"other").create()
    if setup == "not-x25519":
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSPHRASE),
        )
        path.write_bytes(pem)
    with pytest.raises(KeyFileError, match=error):
        EncryptionKeyFile(path, PASSPHRASE).load()


def test_empty_passphrase_is_refused(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError):
        EncryptionKeyFile(tmp_path / "e.key", b"")

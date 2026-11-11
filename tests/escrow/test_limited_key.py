import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ehor.escrow.limited_key import LimitedKey, LimitedKeyError, LimitedKeyFile, base58

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _decode(text: str) -> bytes:
    number = 0
    for char in text:
        number = number * 58 + ALPHABET.index(char)
    zeros = len(text) - len(text.lstrip("1"))
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * zeros + body


@pytest.mark.parametrize(
    ("data", "text"),
    [
        (b"", ""),
        (b"\0", "1"),
        (b"\0\0\x01", "112"),
        (b"hello world", "StV1DL6CwTryKyV"),
        (bytes(range(1, 9)), "An6UebxCZd"),
    ],
)
def test_base58_matches_known_vectors(data: bytes, text: str) -> None:
    assert base58(data) == text


def test_keys_use_near_formats() -> None:
    key = LimitedKey.generate()
    public = _decode(key.public_key.removeprefix("ed25519:"))
    secret = _decode(key.near_secret().removeprefix("ed25519:"))
    assert len(public) == 32
    assert len(secret) == 64
    assert secret[32:] == public
    rebuilt = Ed25519PrivateKey.from_private_bytes(secret[:32])
    raw = rebuilt.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert raw == public


def test_repr_shows_only_the_public_key() -> None:
    key = LimitedKey.generate()
    assert repr(key) == f"LimitedKey({key.public_key})"
    assert key.near_secret() not in repr(key)


def test_file_round_trips_and_is_private(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "near.key"
    created = LimitedKeyFile(path, b"pw").create()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"ENCRYPTED PRIVATE KEY" in path.read_bytes()
    assert created.near_secret().encode() not in path.read_bytes()
    assert LimitedKeyFile(path, b"pw").load().public_key == created.public_key


def test_load_or_create_keeps_an_existing_key(tmp_path: Path) -> None:
    path = tmp_path / "near.key"
    first = LimitedKeyFile(path, b"pw").load_or_create()
    assert LimitedKeyFile(path, b"pw").load_or_create().public_key == first.public_key


def test_create_never_replaces_a_key(tmp_path: Path) -> None:
    path = tmp_path / "near.key"
    LimitedKeyFile(path, b"pw").create()
    with pytest.raises(LimitedKeyError, match="already exists"):
        LimitedKeyFile(path, b"pw").create()


def test_wrong_passphrase_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "near.key"
    LimitedKeyFile(path, b"pw").create()
    with pytest.raises(LimitedKeyError, match="passphrase"):
        LimitedKeyFile(path, b"other").load()


def test_missing_and_foreign_keys_are_refused(tmp_path: Path) -> None:
    with pytest.raises(LimitedKeyError, match="no NEAR key"):
        LimitedKeyFile(tmp_path / "none.key", b"pw").load()
    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"pw"),
    )
    (tmp_path / "p256.key").write_bytes(other)
    with pytest.raises(LimitedKeyError, match="not an Ed25519 key"):
        LimitedKeyFile(tmp_path / "p256.key", b"pw").load()


def test_empty_passphrase_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LimitedKeyError, match="empty"):
        LimitedKeyFile(tmp_path / "near.key", b"")

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from agentmesh.crypto.sealing import (
    HINT_SIZE,
    WRAPPED_KEY_SIZE,
    NotARecipientError,
    Recipient,
    SealError,
    open_sealed,
    public_bytes,
    seal,
)

AAD = b"entry header"


@pytest.fixture
def keys() -> list[X25519PrivateKey]:
    return [X25519PrivateKey.generate() for _ in range(3)]


def _recipients(keys: list[X25519PrivateKey]) -> list[Recipient]:
    return [Recipient(public_bytes(key)) for key in keys]


def test_every_recipient_can_open(keys: list[X25519PrivateKey]) -> None:
    sealed = seal(b"secret plan", _recipients(keys), AAD)
    assert len(sealed.keys) == 3
    assert all(len(h) == HINT_SIZE and len(w) == WRAPPED_KEY_SIZE for h, w in sealed.keys)
    assert b"secret plan" not in sealed.data
    for key in keys:
        assert open_sealed(sealed, key, AAD) == b"secret plan"


def test_each_seal_is_fresh(keys: list[X25519PrivateKey]) -> None:
    first = seal(b"same", _recipients(keys), AAD)
    second = seal(b"same", _recipients(keys), AAD)
    assert first.ephemeral != second.ephemeral
    assert first.data != second.data


def test_duplicate_recipients_are_wrapped_once(keys: list[X25519PrivateKey]) -> None:
    sealed = seal(b"x", _recipients(keys[:1]) * 3, AAD)
    assert len(sealed.keys) == 1


def test_needs_a_recipient() -> None:
    with pytest.raises(SealError, match="no recipients"):
        seal(b"x", [], AAD)


def test_outsider_cannot_open(keys: list[X25519PrivateKey]) -> None:
    sealed = seal(b"x", _recipients(keys[:2]), AAD)
    with pytest.raises(NotARecipientError):
        open_sealed(sealed, keys[2], AAD)


def test_outsider_cannot_reuse_someone_elses_slot(keys: list[X25519PrivateKey]) -> None:
    sealed = seal(b"x", _recipients(keys[:1]), AAD)
    own_hint = Recipient(public_bytes(keys[1])).hint
    stolen = replace(sealed, keys=((own_hint, sealed.keys[0][1]),))
    with pytest.raises(SealError, match="cannot decrypt"):
        open_sealed(stolen, keys[1], AAD)


@pytest.mark.parametrize("part", ["data", "nonce", "wrapped", "aad"])
def test_tampering_is_detected(keys: list[X25519PrivateKey], part: str) -> None:
    sealed = seal(b"secret", _recipients(keys[:1]), AAD)
    aad = AAD
    if part == "data":
        sealed = replace(sealed, data=bytes([sealed.data[0] ^ 1]) + sealed.data[1:])
    elif part == "nonce":
        sealed = replace(sealed, nonce=bytes(12))
    elif part == "wrapped":
        hint, wrapped = sealed.keys[0]
        sealed = replace(sealed, keys=((hint, bytes([wrapped[0] ^ 1]) + wrapped[1:]),))
    else:
        aad = b"moved to another entry"
    with pytest.raises(SealError, match="cannot decrypt"):
        open_sealed(sealed, keys[0], aad)


def test_rejects_a_low_order_ephemeral_key(keys: list[X25519PrivateKey]) -> None:
    sealed = replace(seal(b"x", _recipients(keys[:1]), AAD), ephemeral=bytes(32))
    with pytest.raises(SealError, match="invalid x25519"):
        open_sealed(sealed, keys[0], AAD)

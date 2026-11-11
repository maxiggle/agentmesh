import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ehor.escrow.payload import PayloadError
from ehor.escrow.release import ReleaseSignError, ReleaseTerms, sign_release
from ehor.identity.base import KeyHandle
from ehor.identity.es256 import CURVE_ORDER, verify
from ehor.identity.software import SoftwareSigner

VECTOR = Path(__file__).resolve().parents[2] / "docs" / "vectors" / "release-v1.json"
TERMS = ReleaseTerms("testnet", "ehor-escrow.testnet", 7, "bob.testnet", 10**23)


@pytest.fixture
def signer(tmp_path: Path) -> SoftwareSigner:
    return SoftwareSigner(tmp_path, b"test passphrase")


@pytest.fixture
def handle(signer: SoftwareSigner) -> KeyHandle:
    return signer.create_key("device")


@dataclass
class HighSSigner:
    inner: SoftwareSigner

    def create_key(self, label: str) -> KeyHandle:
        return self.inner.create_key(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self.inner.public_key(handle)

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        signature = self.inner.sign(handle, data)
        s = int.from_bytes(signature[32:], "big")
        return signature[:32] + max(s, CURVE_ORDER - s).to_bytes(32, "big")


@dataclass
class WrongDataSigner:
    inner: SoftwareSigner

    def create_key(self, label: str) -> KeyHandle:
        return self.inner.create_key(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self.inner.public_key(handle)

    def sign(self, handle: KeyHandle, data: bytes) -> bytes:
        return self.inner.sign(handle, data + b"!")


def _decoded(signature: str) -> bytes:
    raw = bytes.fromhex(signature)
    assert len(raw) == 64
    return raw


def test_release_signature_verifies_and_is_low_s(signer: SoftwareSigner, handle: KeyHandle) -> None:
    public = signer.public_key(handle)
    for _ in range(8):
        raw = _decoded(sign_release(signer, handle, TERMS))
        assert int.from_bytes(raw[32:], "big") <= CURVE_ORDER // 2
        assert verify(public, raw, TERMS.payload().encode())


def test_high_s_from_the_backend_is_normalized(signer: SoftwareSigner, handle: KeyHandle) -> None:
    raw = _decoded(sign_release(HighSSigner(signer), handle, TERMS))
    assert int.from_bytes(raw[32:], "big") <= CURVE_ORDER // 2
    assert verify(signer.public_key(handle), raw, TERMS.payload().encode())


def test_a_signature_that_does_not_verify_is_refused(
    signer: SoftwareSigner, handle: KeyHandle
) -> None:
    with pytest.raises(ReleaseSignError, match="does not verify"):
        sign_release(WrongDataSigner(signer), handle, TERMS)


@pytest.mark.parametrize(
    "other",
    [
        replace(TERMS, chain_id="mainnet"),
        replace(TERMS, contract="other-escrow.testnet"),
        replace(TERMS, bounty_id=8),
        replace(TERMS, claimant="mallory.testnet"),
        replace(TERMS, amount_yocto=10**23 + 1),
    ],
)
def test_signature_does_not_carry_over_to_other_terms(
    signer: SoftwareSigner, handle: KeyHandle, other: ReleaseTerms
) -> None:
    raw = _decoded(sign_release(signer, handle, TERMS))
    assert not verify(signer.public_key(handle), raw, other.payload().encode())


def test_terms_build_the_shared_vector_payload() -> None:
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    terms = ReleaseTerms(
        vector["chain_id"],
        vector["contract"],
        vector["bounty_id"],
        vector["claimant"],
        int(vector["amount_yocto"]),
    )
    assert terms.payload() == vector["payload"]


def test_invalid_terms_are_refused_before_signing(
    signer: SoftwareSigner, handle: KeyHandle
) -> None:
    with pytest.raises(PayloadError):
        sign_release(signer, handle, replace(TERMS, amount_yocto=0))

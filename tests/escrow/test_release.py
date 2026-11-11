import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from ehor.escrow.payload import PayloadError
from ehor.escrow.release import ReleaseSignError, ReleaseTerms, format_near, sign_release
from ehor.identity.base import KeyHandle
from ehor.identity.es256 import CURVE_ORDER, verify
from ehor.identity.software import SoftwareSigner

VECTOR = Path(__file__).resolve().parents[2] / "docs" / "vectors" / "release-v1.json"
TERMS = ReleaseTerms("testnet", "ehor-escrow.testnet", 7, "bob.testnet", 10**23)


@dataclass
class TouchSigner:
    """A software key standing in for a presence key; records what the person was asked."""

    inner: SoftwareSigner
    reasons: list[str] = field(default_factory=list)
    flip_s: bool = False
    tamper: bool = False

    def create_key(self, label: str) -> KeyHandle:
        return self.inner.create_key(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self.inner.public_key(handle)

    def sign_with_presence(self, handle: KeyHandle, data: bytes, reason: str) -> bytes:
        self.reasons.append(reason)
        signature = self.inner.sign(handle, data + b"!" if self.tamper else data)
        if not self.flip_s:
            return signature
        s = int.from_bytes(signature[32:], "big")
        return signature[:32] + max(s, CURVE_ORDER - s).to_bytes(32, "big")


@pytest.fixture
def signer(tmp_path: Path) -> TouchSigner:
    return TouchSigner(SoftwareSigner(tmp_path, b"test passphrase"))


@pytest.fixture
def handle(signer: TouchSigner) -> KeyHandle:
    return signer.create_key("release")


def _decoded(signature: str) -> bytes:
    raw = bytes.fromhex(signature)
    assert len(raw) == 64
    return raw


def test_release_signature_verifies_and_is_low_s(signer: TouchSigner, handle: KeyHandle) -> None:
    public = signer.public_key(handle)
    for _ in range(8):
        raw = _decoded(sign_release(signer, handle, TERMS))
        assert int.from_bytes(raw[32:], "big") <= CURVE_ORDER // 2
        assert verify(public, raw, TERMS.payload().encode())


def test_high_s_from_the_backend_is_normalized(signer: TouchSigner, handle: KeyHandle) -> None:
    signer.flip_s = True
    raw = _decoded(sign_release(signer, handle, TERMS))
    assert int.from_bytes(raw[32:], "big") <= CURVE_ORDER // 2
    assert verify(signer.public_key(handle), raw, TERMS.payload().encode())


def test_a_signature_that_does_not_verify_is_refused(
    signer: TouchSigner, handle: KeyHandle
) -> None:
    signer.tamper = True
    with pytest.raises(ReleaseSignError, match="does not verify"):
        sign_release(signer, handle, TERMS)


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
    signer: TouchSigner, handle: KeyHandle, other: ReleaseTerms
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


def test_invalid_terms_are_refused_before_signing(signer: TouchSigner, handle: KeyHandle) -> None:
    with pytest.raises(PayloadError):
        sign_release(signer, handle, replace(TERMS, amount_yocto=0))
    assert signer.reasons == []


def test_the_person_is_asked_with_the_amount_claimant_and_bounty(
    signer: TouchSigner, handle: KeyHandle
) -> None:
    sign_release(signer, handle, TERMS)
    assert signer.reasons == ["pay 0.1 NEAR to bob.testnet for ehor bounty 7"]


@pytest.mark.parametrize(
    ("yocto", "text"),
    [
        (1, "0.000000000000000000000001"),
        (10**24, "1"),
        (25 * 10**23, "2.5"),
        (10**30, "1000000"),
        (123456789012345678901234567890123, "123456789.012345678901234567890123"),
    ],
)
def test_format_near_is_exact(yocto: int, text: str) -> None:
    assert format_near(yocto) == text

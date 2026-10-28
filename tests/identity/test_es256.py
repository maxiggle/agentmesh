import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.identity import es256

CURVE_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _sign(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    return es256.raw_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))


def test_compressed_public_key_is_33_bytes(key: ec.EllipticCurvePrivateKey) -> None:
    public = es256.compress_public_key(key.public_key())
    assert len(public) == es256.PUBLIC_KEY_SIZE
    assert public[0] in (2, 3)


def test_valid_signature_verifies(key: ec.EllipticCurvePrivateKey) -> None:
    public = es256.compress_public_key(key.public_key())
    signature = _sign(key, b"hello")
    assert len(signature) == es256.SIGNATURE_SIZE
    assert es256.verify(public, signature, b"hello")


def test_high_s_signature_verifies(key: ec.EllipticCurvePrivateKey) -> None:
    public = es256.compress_public_key(key.public_key())
    signature = _sign(key, b"hello")
    s = int.from_bytes(signature[32:], "big")
    flipped = signature[:32] + (CURVE_ORDER - s).to_bytes(32, "big")
    assert flipped != signature
    assert es256.verify(public, flipped, b"hello")


def test_rejects_other_data(key: ec.EllipticCurvePrivateKey) -> None:
    public = es256.compress_public_key(key.public_key())
    assert not es256.verify(public, _sign(key, b"hello"), b"hellO")


def test_rejects_other_key(key: ec.EllipticCurvePrivateKey) -> None:
    other = es256.compress_public_key(ec.generate_private_key(ec.SECP256R1()).public_key())
    assert not es256.verify(other, _sign(key, b"hello"), b"hello")


@pytest.mark.parametrize(
    ("public", "signature"),
    [
        (b"\x02" + b"\xff" * 32, b"\x01" * 64),
        (b"\x02" * 32, b"\x01" * 64),
        (None, b"\x01" * 63),
        (None, b"\x00" * 64),
    ],
)
def test_rejects_malformed_input(
    key: ec.EllipticCurvePrivateKey, public: bytes | None, signature: bytes
) -> None:
    public = public or es256.compress_public_key(key.public_key())
    assert not es256.verify(public, signature, b"hello")

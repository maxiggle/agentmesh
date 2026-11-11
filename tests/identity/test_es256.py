import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from ehor.identity import es256

CURVE_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
HALF_ORDER = CURVE_ORDER // 2


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


def _with_s(signature: bytes, s: int) -> bytes:
    return signature[:32] + s.to_bytes(32, "big")


def _s(signature: bytes) -> int:
    return int.from_bytes(signature[32:], "big")


def test_curve_order_is_p256() -> None:
    assert es256.CURVE_ORDER == CURVE_ORDER


def test_low_s_flips_a_high_s_signature_and_it_still_verifies(
    key: ec.EllipticCurvePrivateKey,
) -> None:
    public = es256.compress_public_key(key.public_key())
    for _ in range(16):
        signature = _sign(key, b"hello")
        high = _with_s(signature, max(_s(signature), CURVE_ORDER - _s(signature)))
        normalized = es256.low_s(high)
        assert _s(normalized) <= HALF_ORDER
        assert normalized[:32] == high[:32]
        assert es256.verify(public, normalized, b"hello")


def test_low_s_leaves_a_low_s_signature_unchanged(key: ec.EllipticCurvePrivateKey) -> None:
    signature = _sign(key, b"hello")
    low = _with_s(signature, min(_s(signature), CURVE_ORDER - _s(signature)))
    assert es256.low_s(low) == low


@pytest.mark.parametrize(
    ("s", "expected"),
    [(HALF_ORDER, HALF_ORDER), (HALF_ORDER + 1, HALF_ORDER), (CURVE_ORDER - 1, 1)],
)
def test_low_s_boundary(s: int, expected: int) -> None:
    assert _s(es256.low_s(b"\x01" * 32 + s.to_bytes(32, "big"))) == expected


@pytest.mark.parametrize(
    "signature",
    [
        b"\x01" * 63,
        b"\x01" * 65,
        b"\x00" * 32 + b"\x01" * 32,
        b"\x01" * 32 + b"\x00" * 32,
        CURVE_ORDER.to_bytes(32, "big") + b"\x01" * 32,
        b"\x01" * 32 + CURVE_ORDER.to_bytes(32, "big"),
    ],
)
def test_low_s_rejects_malformed_signatures(signature: bytes) -> None:
    with pytest.raises(es256.SignatureFormatError):
        es256.low_s(signature)

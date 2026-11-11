from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PUBLIC_KEY_SIZE = 33
SIGNATURE_SIZE = 64
CURVE_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_SCALAR_SIZE = 32


class SignatureFormatError(ValueError):
    pass


def compress_public_key(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(Encoding.X962, PublicFormat.CompressedPoint)


def raw_signature(der_signature: bytes) -> bytes:
    r, s = decode_dss_signature(der_signature)
    return r.to_bytes(_SCALAR_SIZE, "big") + s.to_bytes(_SCALAR_SIZE, "big")


def low_s(signature: bytes) -> bytes:
    """Return the low-S form of a raw `r || s` signature.

    Both forms verify, but NEAR's `p256_verify` accepts only low-S, so anything sent on chain
    goes through here. Hardware backends may return either form.
    """
    if len(signature) != SIGNATURE_SIZE:
        raise SignatureFormatError(f"signature must be {SIGNATURE_SIZE} bytes")
    r = int.from_bytes(signature[:_SCALAR_SIZE], "big")
    s = int.from_bytes(signature[_SCALAR_SIZE:], "big")
    if not (0 < r < CURVE_ORDER and 0 < s < CURVE_ORDER):
        raise SignatureFormatError("signature scalars must be in 1..n-1")
    if s > CURVE_ORDER // 2:
        s = CURVE_ORDER - s
    return signature[:_SCALAR_SIZE] + s.to_bytes(_SCALAR_SIZE, "big")


def verify(public_key: bytes, signature: bytes, data: bytes) -> bool:
    """Check a raw ES256 signature. Accepts both low-S and high-S forms."""
    if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
        return False
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    except ValueError:
        return False
    r = int.from_bytes(signature[:_SCALAR_SIZE], "big")
    s = int.from_bytes(signature[_SCALAR_SIZE:], "big")
    try:
        key.verify(encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True

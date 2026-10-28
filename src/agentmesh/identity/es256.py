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
_SCALAR_SIZE = 32


def compress_public_key(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(Encoding.X962, PublicFormat.CompressedPoint)


def raw_signature(der_signature: bytes) -> bytes:
    r, s = decode_dss_signature(der_signature)
    return r.to_bytes(_SCALAR_SIZE, "big") + s.to_bytes(_SCALAR_SIZE, "big")


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

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PUBLIC_KEY_SIZE = 32
HINT_SIZE = 8
NONCE_SIZE = 12
CONTENT_KEY_SIZE = 32
WRAPPED_KEY_SIZE = CONTENT_KEY_SIZE + 16
_WRAP_INFO = b"agentmesh/wrap/v0\x00"
_WRAP_NONCE = bytes(NONCE_SIZE)


class SealError(Exception):
    pass


class NotARecipientError(SealError):
    pass


@dataclass(frozen=True, slots=True)
class Recipient:
    public: bytes

    @property
    def hint(self) -> bytes:
        return recipient_hint(self.public)


@dataclass(frozen=True, slots=True)
class Sealed:
    ephemeral: bytes
    keys: tuple[tuple[bytes, bytes], ...]
    nonce: bytes
    data: bytes


def recipient_hint(public: bytes) -> bytes:
    """Lets a device find its wrapped key without trying every entry."""
    return hashlib.sha256(public).digest()[:HINT_SIZE]


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def seal(plaintext: bytes, recipients: Iterable[Recipient], aad: bytes) -> Sealed:
    """Encrypt once with a fresh content key, then wrap that key for each recipient.

    Each message uses a new ephemeral X25519 key, so every wrapping key is
    unique and the fixed wrap nonce is never reused under the same key.
    """
    unique = {recipient.hint: recipient for recipient in recipients}
    if not unique:
        raise SealError("no recipients")
    content_key = AESGCM.generate_key(bit_length=CONTENT_KEY_SIZE * 8)
    nonce = secrets.token_bytes(NONCE_SIZE)
    data = AESGCM(content_key).encrypt(nonce, plaintext, aad)
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = public_bytes(ephemeral)
    keys = []
    for hint, recipient in sorted(unique.items()):
        kek = _wrapping_key(ephemeral, recipient.public, ephemeral_public, recipient.public)
        keys.append((hint, AESGCM(kek).encrypt(_WRAP_NONCE, content_key, hint)))
    return Sealed(ephemeral_public, tuple(keys), nonce, data)


def open_sealed(sealed: Sealed, private: X25519PrivateKey, aad: bytes) -> bytes:
    own_public = public_bytes(private)
    hint = recipient_hint(own_public)
    wrapped = next((key for key_hint, key in sealed.keys if key_hint == hint), None)
    if wrapped is None:
        raise NotARecipientError("this device was not a recipient")
    kek = _wrapping_key(private, sealed.ephemeral, sealed.ephemeral, own_public)
    try:
        content_key = AESGCM(kek).decrypt(_WRAP_NONCE, wrapped, hint)
        return AESGCM(content_key).decrypt(sealed.nonce, sealed.data, aad)
    except InvalidTag as exc:
        raise SealError("cannot decrypt: wrong key or tampered data") from exc


def _wrapping_key(
    private: X25519PrivateKey, peer_public: bytes, ephemeral_public: bytes, recipient_public: bytes
) -> bytes:
    try:
        shared = private.exchange(X25519PublicKey.from_public_bytes(peer_public))
    except ValueError as exc:
        raise SealError("invalid x25519 public key") from exc
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=CONTENT_KEY_SIZE,
        salt=None,
        info=_WRAP_INFO + ephemeral_public + recipient_public,
    )
    return hkdf.derive(shared)

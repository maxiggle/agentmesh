from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.identity.assertion import (
    FLAG_USER_PRESENT,
    RP_ID,
    Assertion,
    client_data_hash,
    rp_id_hash,
)
from agentmesh.identity.es256 import compress_public_key, raw_signature


class SoftwareAuthenticator:
    """Signs like a FIDO2 authenticator, ES256 over `auth_data || client_data_hash`,
    with an in-memory key and settable flags so tests can produce bad assertions."""

    def __init__(
        self, *, rp_id: str = RP_ID, flags: int = FLAG_USER_PRESENT, extensions: bytes = b""
    ) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = compress_public_key(self._key.public_key())
        self.rp_id = rp_id
        self.flags = flags
        self.extensions = extensions
        self.counter = 0

    def sign(self, message: bytes) -> Assertion:
        return self.sign_hash(client_data_hash(message))

    def sign_hash(self, client_data_hash: bytes) -> Assertion:
        self.counter += 1
        auth_data = (
            rp_id_hash(self.rp_id)
            + bytes([self.flags])
            + self.counter.to_bytes(4, "big")
            + self.extensions
        )
        der = self._key.sign(auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        return Assertion(auth_data, raw_signature(der))

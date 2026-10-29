import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agentmesh.identity import IdentityError
from agentmesh.identity.assertion import (
    FLAG_USER_PRESENT,
    RP_ID,
    Assertion,
    client_data_hash,
    rp_id_hash,
)
from agentmesh.identity.es256 import compress_public_key, raw_signature
from agentmesh.identity.security_key import Credential


class SoftwareAuthenticator:
    """Signs like a FIDO2 authenticator, ES256 over `auth_data || client_data_hash`,
    with in-memory keys and settable flags so tests can produce bad assertions.

    It starts with one credential, used by `public_key` and `sign`, and implements
    the `Authenticator` protocol for credentials made with `make_credential`.
    `rp_id`, when set, replaces the relying party a caller asks for.
    """

    def __init__(
        self,
        *,
        rp_id: str | None = None,
        flags: int = FLAG_USER_PRESENT,
        extensions: bytes = b"",
    ) -> None:
        self.rp_id = rp_id
        self.flags = flags
        self.extensions = extensions
        self.counter = 0
        self.touches = 0
        self._keys: dict[bytes, ec.EllipticCurvePrivateKey] = {}
        self._default = self.make_credential(RP_ID, b"user", "user")

    @property
    def public_key(self) -> bytes:
        return self._default.public_key

    def sign(self, message: bytes) -> Assertion:
        return self.get_assertion(RP_ID, self._default.credential_id, client_data_hash(message))

    def make_credential(self, rp_id: str, user_id: bytes, user_name: str) -> Credential:
        key = ec.generate_private_key(ec.SECP256R1())
        credential_id = secrets.token_bytes(32)
        self._keys[credential_id] = key
        return Credential(credential_id, compress_public_key(key.public_key()))

    def get_assertion(self, rp_id: str, credential_id: bytes, client_data_hash: bytes) -> Assertion:
        key = self._keys.get(credential_id)
        if key is None:
            raise IdentityError("this security key does not hold that credential")
        self.touches += 1
        self.counter += 1
        auth_data = (
            rp_id_hash(self.rp_id or rp_id)
            + bytes([self.flags])
            + self.counter.to_bytes(4, "big")
            + self.extensions
        )
        der = key.sign(auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        return Assertion(auth_data, raw_signature(der))

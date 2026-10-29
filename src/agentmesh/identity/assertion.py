import hashlib
from dataclasses import dataclass

from agentmesh.identity import es256

RP_ID = "agentmesh:"
"""Relying party ID for agentmesh credentials. The colon makes it an invalid web domain,
so no website can ask a browser for an assertion under it (OpenSSH uses `ssh:` likewise)."""

RP_ID_HASH_SIZE = 32
AUTH_DATA_SIZE = 37
FLAG_USER_PRESENT = 0x01
FLAG_ATTESTED_CREDENTIAL = 0x40
FLAG_EXTENSIONS = 0x80
_FLAGS_OFFSET = RP_ID_HASH_SIZE


class InvalidAssertionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Assertion:
    """A CTAP2 `getAssertion` result: ES256 over `auth_data || client_data_hash`."""

    auth_data: bytes
    signature: bytes


def rp_id_hash(rp_id: str = RP_ID) -> bytes:
    return hashlib.sha256(rp_id.encode()).digest()


def client_data_hash(message: bytes) -> bytes:
    return hashlib.sha256(message).digest()


def check_assertion(
    public_key: bytes, assertion: Assertion, message: bytes, *, rp_id: str = RP_ID
) -> None:
    """Raise unless `assertion` is a user-present signature over `message` for `rp_id`."""
    auth_data = assertion.auth_data
    if len(auth_data) < AUTH_DATA_SIZE:
        raise InvalidAssertionError(f"auth_data is shorter than {AUTH_DATA_SIZE} bytes")
    flags = auth_data[_FLAGS_OFFSET]
    if flags & FLAG_ATTESTED_CREDENTIAL:
        raise InvalidAssertionError("auth_data carries attested credential data")
    has_extensions = bool(flags & FLAG_EXTENSIONS)
    if has_extensions != (len(auth_data) > AUTH_DATA_SIZE):
        raise InvalidAssertionError("auth_data length does not match its extension flag")
    if auth_data[:RP_ID_HASH_SIZE] != rp_id_hash(rp_id):
        raise InvalidAssertionError("wrong rpId")
    if not flags & FLAG_USER_PRESENT:
        raise InvalidAssertionError("user presence flag not set")
    if not es256.verify(public_key, assertion.signature, auth_data + client_data_hash(message)):
        raise InvalidAssertionError("bad signature")

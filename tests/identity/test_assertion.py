from dataclasses import replace

import pytest

from agentmesh.identity.assertion import (
    FLAG_ATTESTED_CREDENTIAL,
    FLAG_EXTENSIONS,
    FLAG_USER_PRESENT,
    RP_ID,
    Assertion,
    InvalidAssertionError,
    check_assertion,
)
from security_key_emulator import SoftwareAuthenticator

MESSAGE = b"agentmesh/sig/v0\x00content"
USER_VERIFIED = 0x04


def _flip(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 0x01]) + data[index + 1 :]


def test_rp_id_is_not_a_web_domain() -> None:
    assert RP_ID == "agentmesh:"


def test_accepts_a_user_present_assertion() -> None:
    key = SoftwareAuthenticator()
    check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


def test_accepts_user_verification_on_top_of_presence() -> None:
    key = SoftwareAuthenticator(flags=FLAG_USER_PRESENT | USER_VERIFIED)
    check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


def test_accepts_extension_data_when_flagged() -> None:
    key = SoftwareAuthenticator(flags=FLAG_USER_PRESENT | FLAG_EXTENSIONS, extensions=b"\xa0")
    check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


def test_rejects_a_missing_user_presence_flag() -> None:
    key = SoftwareAuthenticator(flags=USER_VERIFIED)
    with pytest.raises(InvalidAssertionError, match="user presence"):
        check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


def test_rejects_a_wrong_rp_id() -> None:
    key = SoftwareAuthenticator(rp_id="example.com")
    with pytest.raises(InvalidAssertionError, match="wrong rpId"):
        check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


@pytest.mark.parametrize("index", [0, 32, 33, 36])
def test_rejects_tampered_auth_data(index: int) -> None:
    key = SoftwareAuthenticator()
    assertion = key.sign(MESSAGE)
    tampered = replace(assertion, auth_data=_flip(assertion.auth_data, index))
    with pytest.raises(InvalidAssertionError):
        check_assertion(key.public_key, tampered, MESSAGE)


def test_rejects_a_signature_over_another_message() -> None:
    key = SoftwareAuthenticator()
    with pytest.raises(InvalidAssertionError, match="bad signature"):
        check_assertion(key.public_key, key.sign(b"other"), MESSAGE)


def test_rejects_another_key() -> None:
    key = SoftwareAuthenticator()
    with pytest.raises(InvalidAssertionError, match="bad signature"):
        check_assertion(SoftwareAuthenticator().public_key, key.sign(MESSAGE), MESSAGE)


def test_rejects_short_auth_data() -> None:
    key = SoftwareAuthenticator()
    assertion = Assertion(key.sign(MESSAGE).auth_data[:36], bytes(64))
    with pytest.raises(InvalidAssertionError, match="shorter"):
        check_assertion(key.public_key, assertion, MESSAGE)


def test_rejects_attested_credential_data() -> None:
    key = SoftwareAuthenticator(flags=FLAG_USER_PRESENT | FLAG_ATTESTED_CREDENTIAL)
    with pytest.raises(InvalidAssertionError, match="attested"):
        check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)


@pytest.mark.parametrize(
    ("flags", "extensions"),
    [(FLAG_USER_PRESENT, b"\xa0"), (FLAG_USER_PRESENT | FLAG_EXTENSIONS, b"")],
)
def test_rejects_trailing_bytes_that_disagree_with_the_extension_flag(
    flags: int, extensions: bytes
) -> None:
    key = SoftwareAuthenticator(flags=flags, extensions=extensions)
    with pytest.raises(InvalidAssertionError, match="extension"):
        check_assertion(key.public_key, key.sign(MESSAGE), MESSAGE)

import stat
import sys
from pathlib import Path

import pytest

from agentmesh.identity import (
    AssertionSigner,
    IdentityError,
    InvalidLabelError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
)
from agentmesh.identity.assertion import check_assertion
from agentmesh.identity.security_key import Authenticator, SecurityKeySigner
from security_key_emulator import SoftwareAuthenticator

MESSAGE = b"agentmesh/sig/v0\x00roster"


class Connector:
    def __init__(self, authenticator: SoftwareAuthenticator) -> None:
        self.authenticator = authenticator
        self.connects = 0

    def __call__(self) -> Authenticator:
        self.connects += 1
        return self.authenticator


@pytest.fixture
def device() -> SoftwareAuthenticator:
    return SoftwareAuthenticator()


@pytest.fixture
def connector(device: SoftwareAuthenticator) -> Connector:
    return Connector(device)


@pytest.fixture
def signer(tmp_path: Path, connector: Connector) -> SecurityKeySigner:
    return SecurityKeySigner(tmp_path / "keys", connector)


def test_creates_a_credential_and_signs_with_a_touch(
    signer: SecurityKeySigner, device: SoftwareAuthenticator
) -> None:
    keys: AssertionSigner = signer
    handle = keys.create_key("yubikey")
    assertion = keys.sign_assertion(handle, MESSAGE)
    check_assertion(keys.public_key(handle), assertion, MESSAGE)
    assert device.touches == 1
    assert handle == KeyHandle("security-key", "yubikey")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_record_is_owner_only(tmp_path: Path, signer: SecurityKeySigner) -> None:
    signer.create_key("yubikey")
    mode = (tmp_path / "keys" / "yubikey.fido2").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_lists_and_forgets_credentials(signer: SecurityKeySigner) -> None:
    assert signer.labels() == []
    signer.create_key("yubikey")
    backup = signer.create_key("backup")
    assert signer.labels() == ["backup", "yubikey"]
    signer.forget(backup)
    assert signer.labels() == ["yubikey"]


def test_reads_keys_without_touching_the_device(
    tmp_path: Path, signer: SecurityKeySigner, device: SoftwareAuthenticator
) -> None:
    handle = signer.create_key("yubikey")
    other = Connector(device)
    reopened = SecurityKeySigner(tmp_path / "keys", other)
    assert reopened.labels() == ["yubikey"]
    assert reopened.public_key(handle) == signer.public_key(handle)
    assert other.connects == 0
    reopened.sign_assertion(handle, MESSAGE)
    reopened.sign_assertion(handle, MESSAGE)
    assert other.connects == 1


def test_refuses_an_existing_label_before_asking_the_device(
    signer: SecurityKeySigner, connector: Connector
) -> None:
    signer.create_key("yubikey")
    with pytest.raises(KeyExistsError):
        signer.create_key("yubikey")
    assert connector.connects == 1


def test_refuses_a_bad_label(signer: SecurityKeySigner) -> None:
    with pytest.raises(InvalidLabelError):
        signer.create_key("../escape")


@pytest.mark.parametrize(
    "handle", [KeyHandle("security-key", "missing"), KeyHandle("software", "yubikey")]
)
def test_unknown_keys_are_not_found(signer: SecurityKeySigner, handle: KeyHandle) -> None:
    signer.create_key("yubikey")
    with pytest.raises(KeyNotFoundError):
        signer.sign_assertion(handle, MESSAGE)


def test_refuses_an_assertion_without_user_presence(
    signer: SecurityKeySigner, device: SoftwareAuthenticator
) -> None:
    handle = signer.create_key("yubikey")
    device.flags = 0
    with pytest.raises(IdentityError, match="user presence"):
        signer.sign_assertion(handle, MESSAGE)


def test_refuses_an_assertion_for_another_relying_party(
    signer: SecurityKeySigner, device: SoftwareAuthenticator
) -> None:
    handle = signer.create_key("yubikey")
    device.rp_id = "example.com"
    with pytest.raises(IdentityError, match="rpId"):
        signer.sign_assertion(handle, MESSAGE)


def test_reports_a_corrupt_record(tmp_path: Path, signer: SecurityKeySigner) -> None:
    handle = signer.create_key("yubikey")
    (tmp_path / "keys" / "yubikey.fido2").write_text("[]")
    with pytest.raises(IdentityError, match="invalid security key record"):
        signer.public_key(handle)

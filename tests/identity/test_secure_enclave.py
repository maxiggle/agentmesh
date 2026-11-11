import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ehor.identity import (
    HardwareUnavailableError,
    IdentityError,
    InvalidLabelError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    Signer,
    es256,
)
from ehor.identity.base import PresenceSigner
from ehor.identity.secure_enclave import (
    SecureEnclavePresenceSigner,
    SecureEnclaveSigner,
    SwiftHelper,
)
from ehor.identity.tools import ToolError


class FakeEnclave:
    """Stands in for the Swift helper; the blob is an unencrypted key instead of a wrapped one."""

    def __init__(self, *, available: bool = True) -> None:
        self.is_available = available
        self.calls: list[str] = []
        self.public_override: bytes | None = None
        self.signature_override: bytes | None = None
        self.reasons: list[str] = []
        self.timeouts: list[float] = []

    def __call__(self, args: Sequence[str], stdin: bytes = b"", timeout: float = 30.0) -> bytes:
        self.calls.append(args[0])
        self.timeouts.append(timeout)
        if args[0] == "sign" and len(args) == 3:
            self.reasons.append(args[2])
        if args[0] == "available":
            if not self.is_available:
                raise ToolError("ehor-se failed: this Mac has no Secure Enclave")
            return b""
        if args[0] in {"create", "create-presence"}:
            key = ec.generate_private_key(ec.SECP256R1())
            return key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        loaded = serialization.load_der_private_key(Path(args[1]).read_bytes(), None)
        assert isinstance(loaded, ec.EllipticCurvePrivateKey)
        if args[0] == "public":
            return self.public_override or es256.compress_public_key(loaded.public_key())
        der = loaded.sign(stdin, ec.ECDSA(hashes.SHA256()))
        return self.signature_override or es256.raw_signature(der)


@pytest.fixture
def enclave() -> FakeEnclave:
    return FakeEnclave()


@pytest.fixture
def signer(tmp_path: Path, enclave: FakeEnclave) -> SecureEnclaveSigner:
    return SecureEnclaveSigner(tmp_path, enclave)


def test_implements_signer_protocol(signer: SecureEnclaveSigner) -> None:
    protocol: Signer = signer
    assert protocol is signer


def test_signs_and_verifies(signer: SecureEnclaveSigner) -> None:
    handle = signer.create_key("device")
    assert handle == KeyHandle("secure-enclave", "device")
    assert es256.verify(signer.public_key(handle), signer.sign(handle, b"data"), b"data")


def test_key_survives_reload(tmp_path: Path, signer: SecureEnclaveSigner) -> None:
    handle = signer.create_key("device")
    reloaded = SecureEnclaveSigner(tmp_path, FakeEnclave())
    assert reloaded.public_key(handle) == signer.public_key(handle)


def test_public_key_is_asked_for_once(signer: SecureEnclaveSigner, enclave: FakeEnclave) -> None:
    handle = signer.create_key("device")
    signer.public_key(handle)
    signer.public_key(handle)
    assert enclave.calls == ["create", "public"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_key_file_is_owner_only(tmp_path: Path, signer: SecureEnclaveSigner) -> None:
    signer.create_key("device")
    assert (tmp_path / "device.se").stat().st_mode & 0o777 == 0o600


def test_duplicate_label_is_refused_before_making_a_key(
    signer: SecureEnclaveSigner, enclave: FakeEnclave
) -> None:
    signer.create_key("device")
    with pytest.raises(KeyExistsError):
        signer.create_key("device")
    assert enclave.calls == ["create"]


def test_invalid_label_is_refused(signer: SecureEnclaveSigner) -> None:
    with pytest.raises(InvalidLabelError):
        signer.create_key("../escape")


@pytest.mark.parametrize("handle", [KeyHandle("secure-enclave", "missing"), KeyHandle("tpm", "x")])
def test_unknown_key(signer: SecureEnclaveSigner, handle: KeyHandle) -> None:
    with pytest.raises(KeyNotFoundError):
        signer.sign(handle, b"x")


def test_malformed_helper_output_is_rejected(
    signer: SecureEnclaveSigner, enclave: FakeEnclave
) -> None:
    handle = signer.create_key("device")
    enclave.signature_override = b"\x00" * 10
    with pytest.raises(IdentityError, match="malformed signature"):
        signer.sign(handle, b"x")
    enclave.public_override = b"\x02" * 5
    with pytest.raises(IdentityError, match="malformed public key"):
        signer.public_key(handle)


def test_available(tmp_path: Path) -> None:
    assert SecureEnclaveSigner(tmp_path, FakeEnclave()).available()
    assert not SecureEnclaveSigner(tmp_path, FakeEnclave(available=False)).available()


@pytest.fixture
def presence(tmp_path: Path, enclave: FakeEnclave) -> SecureEnclavePresenceSigner:
    return SecureEnclavePresenceSigner(tmp_path, enclave)


def test_presence_signer_implements_its_protocol(presence: SecureEnclavePresenceSigner) -> None:
    protocol: PresenceSigner = presence
    assert protocol is presence
    assert not hasattr(presence, "sign")


def test_presence_key_signs_with_the_approval_reason(
    tmp_path: Path, presence: SecureEnclavePresenceSigner, enclave: FakeEnclave
) -> None:
    handle = presence.create_key("release")
    assert handle == KeyHandle("secure-enclave-presence", "release")
    assert (tmp_path / "release.sep").exists()
    signature = presence.sign_with_presence(handle, b"data", "pay 1 NEAR")
    assert es256.verify(presence.public_key(handle), signature, b"data")
    assert enclave.calls[0] == "create-presence"
    assert enclave.reasons == ["pay 1 NEAR"]
    assert enclave.timeouts[enclave.calls.index("sign")] >= 120


def test_device_and_presence_keys_cannot_be_swapped(
    tmp_path: Path, signer: SecureEnclaveSigner, presence: SecureEnclavePresenceSigner
) -> None:
    device = signer.create_key("device")
    release = presence.create_key("release")
    with pytest.raises(KeyNotFoundError):
        presence.sign_with_presence(device, b"x", "pay")
    with pytest.raises(KeyNotFoundError):
        signer.sign(release, b"x")
    with pytest.raises(KeyNotFoundError):
        presence.sign_with_presence(KeyHandle("secure-enclave-presence", "device"), b"x", "pay")


@pytest.mark.parametrize("reason", ["", "   ", "x" * 201])
def test_presence_reason_must_be_readable(
    presence: SecureEnclavePresenceSigner, enclave: FakeEnclave, reason: str
) -> None:
    handle = presence.create_key("release")
    with pytest.raises(IdentityError, match="approval reason"):
        presence.sign_with_presence(handle, b"x", reason)
    assert "sign" not in enclave.calls


@pytest.mark.skipif(sys.platform == "darwin", reason="the helper builds on macOS")
def test_helper_needs_a_mac(tmp_path: Path) -> None:
    with pytest.raises(HardwareUnavailableError, match="only available on Macs"):
        SwiftHelper(tmp_path).binary()


@pytest.fixture(scope="session")
def built_helper(tmp_path_factory: pytest.TempPathFactory) -> SwiftHelper:
    if sys.platform != "darwin":
        pytest.skip("the Secure Enclave is only on Macs")
    helper = SwiftHelper(tmp_path_factory.mktemp("helper"))
    try:
        helper.binary()
    except HardwareUnavailableError as exc:
        pytest.skip(str(exc))
    return helper


def test_helper_is_built_once_and_cached(built_helper: SwiftHelper) -> None:
    binary = built_helper.binary()
    assert os.access(binary, os.X_OK)
    assert built_helper.binary() == binary


def test_helper_rejects_bad_usage(built_helper: SwiftHelper) -> None:
    with pytest.raises(ToolError, match="usage"):
        built_helper(["unknown"])


def test_real_secure_enclave_key(tmp_path: Path, built_helper: SwiftHelper) -> None:
    signer = SecureEnclaveSigner(tmp_path, built_helper)
    if not signer.available():
        pytest.skip("this Mac has no Secure Enclave")
    handle = signer.create_key("device")
    public = signer.public_key(handle)
    assert es256.verify(public, signer.sign(handle, b"data"), b"data")
    reloaded = SecureEnclaveSigner(tmp_path, built_helper)
    assert reloaded.public_key(handle) == public
    assert es256.verify(public, reloaded.sign(handle, b"more"), b"more")
    with pytest.raises(ToolError):
        built_helper(["sign", str(tmp_path / "missing.se")])


def test_real_presence_key_is_created_without_a_prompt(
    tmp_path: Path, built_helper: SwiftHelper
) -> None:
    if not SecureEnclaveSigner(tmp_path, built_helper).available():
        pytest.skip("this Mac has no Secure Enclave")
    presence = SecureEnclavePresenceSigner(tmp_path, built_helper)
    handle = presence.create_key("release")
    assert len(presence.public_key(handle)) == es256.PUBLIC_KEY_SIZE


@pytest.mark.skipif(
    os.environ.get("EHOR_PRESENCE_TEST") != "1",
    reason="asks for Touch ID; set EHOR_PRESENCE_TEST=1 and approve the prompt",
)
def test_real_presence_key_signs_after_approval(tmp_path: Path, built_helper: SwiftHelper) -> None:
    presence = SecureEnclavePresenceSigner(tmp_path, built_helper)
    handle = presence.create_key("release")
    signature = presence.sign_with_presence(handle, b"data", "run the ehor presence test")
    assert es256.verify(presence.public_key(handle), signature, b"data")

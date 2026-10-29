import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from agentmesh.identity import (
    HardwareUnavailableError,
    IdentityError,
    InvalidLabelError,
    KeyExistsError,
    KeyHandle,
    KeyNotFoundError,
    Signer,
    es256,
)
from agentmesh.identity.tools import ToolError
from agentmesh.identity.tpm import TpmSigner

REAL_TPM = os.environ.get("AGENTMESH_TPM_TEST") == "1"


class FakeTpm:
    """Plays the tpm2-tools commands TpmSigner uses, with software keys as the wrapped blobs."""

    def __init__(self, *, present: bool = True) -> None:
        self.present = present
        self.commands: list[str] = []
        self.stale_contexts = 0
        self.curve: ec.EllipticCurve = ec.SECP256R1()
        self.broken_signature = False

    def __call__(
        self,
        args: Sequence[str],
        *,
        stdin: bytes = b"",
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        assert cwd is not None
        assert env is not None
        assert env["TPM2TOOLS_AUTOFLUSH"] == "yes"
        command, options = args[0], _options(args[1:])
        self.commands.append(command)
        if not self.present:
            raise ToolError(f"{command} failed: could not load tcti")
        if command == "tpm2_createprimary":
            (cwd / options["-c"]).write_bytes(b"primary")
        elif command == "tpm2_create":
            assert (cwd / options["-C"]).read_bytes() == b"primary"
            assert options["-G"] == "ecc256:ecdsa-sha256"
            assert "sign" in options["-a"].split("|")
            assert "decrypt" not in options["-a"].split("|")
            key = ec.generate_private_key(self.curve)
            (cwd / options["-u"]).write_bytes(b"public part")
            (cwd / options["-r"]).write_bytes(
                key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        elif command == "tpm2_load":
            assert (cwd / options["-C"]).read_bytes() == b"primary"
            assert Path(options["-u"]).read_bytes() == b"public part"
            (cwd / options["-c"]).write_bytes(Path(options["-r"]).read_bytes())
        elif command == "tpm2_readpublic":
            assert options["-f"] == "pem"
            pem = (
                self._key(cwd / options["-c"])
                .public_key()
                .public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )
            (cwd / options["-o"]).write_bytes(pem)
        elif command == "tpm2_sign":
            if self.stale_contexts:
                self.stale_contexts -= 1
                raise ToolError("tpm2_sign failed: context load failed")
            assert options["-d"] == ""
            assert options["-f"] == "plain"
            digest = (cwd / args[-1]).read_bytes()
            der = self._key(cwd / options["-c"]).sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
            (cwd / options["-o"]).write_bytes(b"junk" if self.broken_signature else der)
        return b""

    @staticmethod
    def _key(context: Path) -> ec.EllipticCurvePrivateKey:
        key = serialization.load_der_private_key(context.read_bytes(), None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return key


def _options(args: Sequence[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    flag: str | None = None
    for arg in args:
        if arg.startswith("-") and len(arg) == 2:
            if flag is not None:
                options[flag] = ""
            flag = arg
        elif flag is not None:
            options[flag] = arg
            flag = None
    if flag is not None:
        options[flag] = ""
    return options


@pytest.fixture
def tpm() -> FakeTpm:
    return FakeTpm()


@pytest.fixture
def signer(tmp_path: Path, tpm: FakeTpm) -> TpmSigner:
    return TpmSigner(tmp_path, tpm)


def test_implements_signer_protocol(signer: TpmSigner) -> None:
    protocol: Signer = signer
    assert protocol is signer


def test_signs_and_verifies(signer: TpmSigner) -> None:
    handle = signer.create_key("device")
    assert handle == KeyHandle("tpm", "device")
    assert es256.verify(signer.public_key(handle), signer.sign(handle, b"data"), b"data")


def test_key_survives_reload(tmp_path: Path, signer: TpmSigner) -> None:
    handle = signer.create_key("device")
    reloaded = TpmSigner(tmp_path, FakeTpm())
    assert reloaded.public_key(handle) == signer.public_key(handle)


def test_key_is_loaded_once_per_process(signer: TpmSigner, tpm: FakeTpm) -> None:
    handle = signer.create_key("device")
    signer.public_key(handle)
    signer.sign(handle, b"one")
    signer.sign(handle, b"two")
    assert tpm.commands.count("tpm2_load") == 1
    assert tpm.commands.count("tpm2_readpublic") == 1


def test_stale_context_is_reloaded_once(signer: TpmSigner, tpm: FakeTpm) -> None:
    handle = signer.create_key("device")
    signer.sign(handle, b"warm up")
    tpm.stale_contexts = 1
    assert es256.verify(signer.public_key(handle), signer.sign(handle, b"x"), b"x")
    assert tpm.commands.count("tpm2_load") == 2


def test_persistent_failure_is_raised(signer: TpmSigner, tpm: FakeTpm) -> None:
    handle = signer.create_key("device")
    tpm.stale_contexts = 2
    with pytest.raises(ToolError):
        signer.sign(handle, b"x")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_key_files_are_owner_only(tmp_path: Path, signer: TpmSigner) -> None:
    signer.create_key("device")
    for name in ("device.tpm.pub", "device.tpm.priv"):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_duplicate_label_is_refused_before_making_a_key(signer: TpmSigner, tpm: FakeTpm) -> None:
    signer.create_key("device")
    tpm.commands.clear()
    with pytest.raises(KeyExistsError):
        signer.create_key("device")
    assert tpm.commands == []


def test_invalid_label_is_refused(signer: TpmSigner) -> None:
    with pytest.raises(InvalidLabelError):
        signer.create_key("../escape")


@pytest.mark.parametrize("handle", [KeyHandle("tpm", "missing"), KeyHandle("software", "x")])
def test_unknown_key(signer: TpmSigner, handle: KeyHandle) -> None:
    with pytest.raises(KeyNotFoundError):
        signer.sign(handle, b"x")


def test_wrong_curve_is_rejected(signer: TpmSigner, tpm: FakeTpm) -> None:
    tpm.curve = ec.SECP384R1()
    handle = signer.create_key("device")
    with pytest.raises(IdentityError, match="not a P-256 key"):
        signer.public_key(handle)


def test_malformed_signature_is_rejected(signer: TpmSigner, tpm: FakeTpm) -> None:
    handle = signer.create_key("device")
    tpm.broken_signature = True
    with pytest.raises(IdentityError, match="malformed signature"):
        signer.sign(handle, b"x")


def test_available(tmp_path: Path) -> None:
    assert TpmSigner(tmp_path, FakeTpm()).available()
    assert not TpmSigner(tmp_path, FakeTpm(present=False)).available()
    assert not TpmSigner(tmp_path, _missing_tools).available()


def _missing_tools(
    args: Sequence[str],
    *,
    stdin: bytes = b"",
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    raise HardwareUnavailableError(f"{args[0]} is not installed")


@pytest.mark.skipif(not REAL_TPM, reason="set AGENTMESH_TPM_TEST=1 with a TPM or simulator")
def test_real_tpm_key(tmp_path: Path) -> None:
    signer = TpmSigner(tmp_path)
    assert signer.available()
    handle = signer.create_key("device")
    public = signer.public_key(handle)
    for message in (b"one", b"two", b"three" * 1000):
        assert es256.verify(public, signer.sign(handle, message), message)
    reloaded = TpmSigner(tmp_path)
    assert reloaded.public_key(handle) == public
    assert es256.verify(public, reloaded.sign(handle, b"again"), b"again")

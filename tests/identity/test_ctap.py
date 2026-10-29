import hashlib
import os
import secrets
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.ctap import STATUS, CtapDevice, CtapError
from fido2.ctap2.pin import PinProtocol, PinProtocolV1, PinProtocolV2
from fido2.hid import CAPABILITY, CTAPHID, CtapHidDevice

from agentmesh.identity import HardwareUnavailableError, IdentityError
from agentmesh.identity.assertion import RP_ID, check_assertion, rp_id_hash
from agentmesh.identity.ctap import Ctap2Authenticator
from agentmesh.identity.security_key import SecurityKeySigner

REAL_KEY = os.environ.get("AGENTMESH_FIDO2_TEST") == "1"
MESSAGE = b"agentmesh/sig/v0\x00roster"
PIN = "123456"
AAGUID = b"\x00" * 16
_MAKE_CREDENTIAL, _GET_ASSERTION, _GET_INFO, _CLIENT_PIN = 0x01, 0x02, 0x04, 0x06
_GET_KEY_AGREEMENT, _GET_TOKEN_LEGACY, _GET_TOKEN = 0x02, 0x05, 0x09
_UP, _UV, _AT = 0x01, 0x04, 0x40


def _cose(key: ec.EllipticCurvePublicKey, alg: int) -> dict[int, Any]:
    numbers = key.public_numbers()
    return {
        1: 2,
        3: alg,
        -1: 1,
        -2: numbers.x.to_bytes(32, "big"),
        -3: numbers.y.to_bytes(32, "big"),
    }


class FakeKey(CtapDevice):
    """Answers CTAP2 commands the way a USB security key does, including the PIN protocol."""

    def __init__(self, options: Mapping[str, bool] | None = None, pin: str | None = None) -> None:
        self.options = dict(options or {})
        self.pin = pin
        if pin is not None:
            self.options.setdefault("clientPin", True)
        self.fail_with: int | None = None
        self.curve = 1
        self.pins_checked = 0
        self._keys: dict[bytes, ec.EllipticCurvePrivateKey] = {}
        self._agreement = ec.generate_private_key(ec.SECP256R1())
        self._token = secrets.token_bytes(32)
        self._counter = 0

    @property
    def capabilities(self) -> int:
        return CAPABILITY.CBOR

    def call(
        self,
        cmd: int,
        data: bytes = b"",
        event: Event | None = None,
        on_keepalive: Callable[[STATUS], None] | None = None,
    ) -> bytes:
        assert cmd == CTAPHID.CBOR
        request = cast(dict[int, Any], cbor.decode(data[1:])) if len(data) > 1 else {}
        handlers = {
            _GET_INFO: self._info,
            _MAKE_CREDENTIAL: self._make_credential,
            _GET_ASSERTION: self._get_assertion,
            _CLIENT_PIN: self._client_pin,
        }
        try:
            return b"\x00" + cbor.encode(handlers[data[0]](request))
        except CtapError as exc:
            return bytes([exc.code])

    @classmethod
    def list_devices(cls) -> Iterator[CtapDevice]:
        return iter(())

    def _info(self, _: dict[int, Any]) -> dict[int, Any]:
        return {1: ["FIDO_2_0", "FIDO_2_1"], 3: AAGUID, 4: self.options, 6: [2, 1]}

    def _make_credential(self, request: dict[int, Any]) -> dict[int, Any]:
        self._fail_if_asked()
        needs_pin = not self.options.get("makeCredUvNotRqd") or self.options.get("alwaysUv")
        flags = self._check_pin(request, needs_pin=bool(needs_pin), fields=(1, 8, 9))
        assert request[4] == [{"type": "public-key", "alg": -7}]
        key = ec.generate_private_key(ec.SECP256R1())
        credential_id = secrets.token_bytes(48)
        self._keys[credential_id] = key
        attested = (
            AAGUID
            + len(credential_id).to_bytes(2, "big")
            + credential_id
            + cbor.encode({**_cose(key.public_key(), -7), -1: self.curve})
        )
        auth_data = self._auth_data(request[2]["id"], flags | _AT) + attested
        return {1: "none", 2: auth_data, 3: {}}

    def _get_assertion(self, request: dict[int, Any]) -> dict[int, Any]:
        self._fail_if_asked()
        flags = self._check_pin(
            request, needs_pin=bool(self.options.get("alwaysUv")), fields=(2, 6, 7)
        )
        credential_id = request[3][0]["id"]
        key = self._keys.get(credential_id)
        if key is None:
            raise CtapError(CtapError.ERR.NO_CREDENTIALS)
        auth_data = self._auth_data(request[1], flags)
        signature = key.sign(auth_data + request[2], ec.ECDSA(hashes.SHA256()))
        return {1: {"type": "public-key", "id": credential_id}, 2: auth_data, 3: signature}

    def _client_pin(self, request: dict[int, Any]) -> dict[int, Any]:
        protocol = self._protocol(request[1])
        if request[2] == _GET_KEY_AGREEMENT:
            return {1: _cose(self._agreement.public_key(), -25)}
        assert request[2] in (_GET_TOKEN, _GET_TOKEN_LEGACY)
        peer = request[3]
        point = ec.EllipticCurvePublicNumbers(
            int.from_bytes(peer[-2], "big"), int.from_bytes(peer[-3], "big"), ec.SECP256R1()
        ).public_key()
        shared = protocol.kdf(self._agreement.exchange(ec.ECDH(), point))
        cipher: PinProtocol = protocol
        pin_hash = cipher.decrypt(shared, request[6])
        self.pins_checked += 1
        if self.pin is None or pin_hash != hashlib.sha256(self.pin.encode()).digest()[:16]:
            raise CtapError(CtapError.ERR.PIN_INVALID)
        return {2: cipher.encrypt(shared, self._token)}

    def _check_pin(
        self, request: dict[int, Any], *, needs_pin: bool, fields: tuple[int, int, int]
    ) -> int:
        """`fields` are the request keys of the client data hash, PIN param and protocol."""
        client_data_hash, param, protocol = fields
        if param in request:
            pin_protocol: PinProtocol = self._protocol(request[protocol])
            expected = pin_protocol.authenticate(self._token, request[client_data_hash])
            if request[param] != expected:
                raise CtapError(CtapError.ERR.PIN_AUTH_INVALID)
            return _UP | _UV
        if needs_pin and self.options.get("clientPin"):
            raise CtapError(CtapError.ERR.PUAT_REQUIRED)
        return _UP

    def _auth_data(self, rp_id: str, flags: int) -> bytes:
        self._counter += 1
        return rp_id_hash(rp_id) + bytes([flags]) + self._counter.to_bytes(4, "big")

    def _fail_if_asked(self) -> None:
        if self.fail_with is not None:
            raise CtapError(self.fail_with)

    @staticmethod
    def _protocol(version: int) -> PinProtocolV1:
        return PinProtocolV2() if version == PinProtocolV2.VERSION else PinProtocolV1()


class PinPrompt:
    def __init__(self, pin: str = PIN) -> None:
        self.pin = pin
        self.asked = 0

    def __call__(self) -> str:
        self.asked += 1
        return self.pin


def _signer(
    tmp_path: Path, key: FakeKey, pin: Callable[[], str] | None = None
) -> SecurityKeySigner:
    return SecurityKeySigner(tmp_path, lambda: Ctap2Authenticator(key, pin=pin))


def test_enrolls_and_signs_without_a_pin(tmp_path: Path) -> None:
    signer = _signer(tmp_path, FakeKey())
    handle = signer.create_key("yubikey")
    assertion = signer.sign_assertion(handle, MESSAGE)
    check_assertion(signer.public_key(handle), assertion, MESSAGE)
    assert assertion.auth_data[:32] == rp_id_hash(RP_ID)


def test_asks_for_the_pin_only_to_enroll(tmp_path: Path) -> None:
    key, prompt = FakeKey(pin=PIN), PinPrompt()
    signer = _signer(tmp_path, key, prompt)
    handle = signer.create_key("yubikey")
    signer.sign_assertion(handle, MESSAGE)
    assert (prompt.asked, key.pins_checked) == (1, 1)


def test_skips_the_pin_when_the_key_allows_it(tmp_path: Path) -> None:
    key, prompt = FakeKey({"makeCredUvNotRqd": True}, pin=PIN), PinPrompt()
    _signer(tmp_path, key, prompt).create_key("yubikey")
    assert prompt.asked == 0


def test_always_uv_keys_need_the_pin_to_sign(tmp_path: Path) -> None:
    key, prompt = FakeKey({"alwaysUv": True}, pin=PIN), PinPrompt()
    signer = _signer(tmp_path, key, prompt)
    handle = signer.create_key("yubikey")
    signer.sign_assertion(handle, MESSAGE)
    assert prompt.asked == 2


def test_a_pin_protected_key_needs_a_pin_prompt(tmp_path: Path) -> None:
    with pytest.raises(IdentityError, match="needs its PIN"):
        _signer(tmp_path, FakeKey(pin=PIN)).create_key("yubikey")


def test_reports_a_wrong_pin(tmp_path: Path) -> None:
    with pytest.raises(IdentityError, match="wrong security key PIN"):
        _signer(tmp_path, FakeKey(pin=PIN), PinPrompt("000000")).create_key("yubikey")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (CtapError.ERR.USER_ACTION_TIMEOUT, "timed out waiting for a touch"),
        (CtapError.ERR.OPERATION_DENIED, "refused"),
        (CtapError.ERR.INVALID_PARAMETER, "security key error"),
    ],
)
def test_reports_device_errors(tmp_path: Path, code: int, message: str) -> None:
    key = FakeKey()
    signer = _signer(tmp_path, key)
    handle = signer.create_key("yubikey")
    key.fail_with = code
    with pytest.raises(IdentityError, match=message):
        signer.sign_assertion(handle, MESSAGE)


def test_another_key_does_not_hold_the_credential(tmp_path: Path) -> None:
    handle = _signer(tmp_path, FakeKey()).create_key("yubikey")
    with pytest.raises(IdentityError, match="key you enrolled"):
        _signer(tmp_path, FakeKey()).sign_assertion(handle, MESSAGE)


def test_refuses_a_credential_on_another_curve(tmp_path: Path) -> None:
    key = FakeKey()
    key.curve = 2
    with pytest.raises(IdentityError, match="P-256"):
        _signer(tmp_path, key).create_key("yubikey")


def test_refuses_a_device_without_ctap2() -> None:
    class U2fOnly(FakeKey):
        @property
        def capabilities(self) -> int:
            return 0

    with pytest.raises(HardwareUnavailableError, match="FIDO2"):
        Ctap2Authenticator(U2fOnly())


@pytest.mark.parametrize(("count", "message"), [(0, "no security key"), (2, "more than one")])
def test_connect_needs_exactly_one_key(
    monkeypatch: pytest.MonkeyPatch, count: int, message: str
) -> None:
    monkeypatch.setattr(CtapHidDevice, "list_devices", lambda: iter([FakeKey()] * count))
    with pytest.raises(HardwareUnavailableError, match=message):
        Ctap2Authenticator.connect()


def test_connect_uses_the_only_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(CtapHidDevice, "list_devices", lambda: iter([FakeKey()]))
    signer = SecurityKeySigner(tmp_path, Ctap2Authenticator.connect)
    handle = signer.create_key("yubikey")
    check_assertion(signer.public_key(handle), signer.sign_assertion(handle, MESSAGE), MESSAGE)


@pytest.mark.skipif(not REAL_KEY, reason="set AGENTMESH_FIDO2_TEST=1 and plug in a security key")
def test_real_security_key(tmp_path: Path) -> None:
    """Needs two touches: one to enroll, one to sign. Set AGENTMESH_FIDO2_PIN if it has a PIN."""
    pin = os.environ.get("AGENTMESH_FIDO2_PIN")
    signer = SecurityKeySigner(
        tmp_path, lambda: Ctap2Authenticator.connect(pin=None if pin is None else lambda: pin)
    )
    handle = signer.create_key("pytest")
    assertion = signer.sign_assertion(handle, MESSAGE)
    check_assertion(signer.public_key(handle), assertion, MESSAGE)

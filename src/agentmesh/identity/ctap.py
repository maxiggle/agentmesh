"""USB security keys over CTAP2, through the optional `fido2` package."""

import secrets
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from cryptography.hazmat.primitives.asymmetric import ec
from fido2.cose import ES256, CoseKey
from fido2.ctap import CtapDevice, CtapError
from fido2.ctap2.base import Ctap2
from fido2.ctap2.pin import ClientPin
from fido2.hid import CtapHidDevice

from agentmesh.identity.assertion import Assertion
from agentmesh.identity.base import HardwareUnavailableError, IdentityError
from agentmesh.identity.es256 import compress_public_key, raw_signature
from agentmesh.identity.security_key import Credential

T = TypeVar("T")

_RP_NAME = "agentmesh"
_P256 = 1
_ERRORS = {
    CtapError.ERR.NO_CREDENTIALS: "this security key does not hold that credential; "
    "plug in the key you enrolled",
    CtapError.ERR.USER_ACTION_TIMEOUT: "timed out waiting for a touch",
    CtapError.ERR.ACTION_TIMEOUT: "timed out waiting for a touch",
    CtapError.ERR.OPERATION_DENIED: "the security key refused",
    CtapError.ERR.KEEPALIVE_CANCEL: "cancelled on the security key",
    CtapError.ERR.PIN_INVALID: "wrong security key PIN",
    CtapError.ERR.PIN_BLOCKED: "the security key PIN is blocked",
    CtapError.ERR.PIN_AUTH_BLOCKED: "too many wrong PINs; unplug the key and try again",
    CtapError.ERR.PUAT_REQUIRED: "this security key needs its PIN",
}


class Ctap2Authenticator:
    """A security key driven directly over CTAP2.

    It talks CTAP2 rather than going through a WebAuthn client because the client data
    hash must be SHA-256 of the entry's signature input, not of a `clientDataJSON`.
    `pin` is asked only when the key insists on one.
    """

    def __init__(self, device: CtapDevice, *, pin: Callable[[], str] | None = None) -> None:
        try:
            self._ctap = Ctap2(device)
        except (CtapError, ValueError, OSError) as exc:
            raise HardwareUnavailableError("the security key does not support FIDO2") from exc
        self._pin = pin

    @classmethod
    def connect(cls, *, pin: Callable[[], str] | None = None) -> "Ctap2Authenticator":
        try:
            devices = list(CtapHidDevice.list_devices())
        except OSError as exc:
            raise HardwareUnavailableError(f"cannot look for security keys: {exc}") from exc
        if not devices:
            raise HardwareUnavailableError("no security key found; plug one in")
        if len(devices) > 1:
            raise HardwareUnavailableError("more than one security key is plugged in")
        return cls(devices[0], pin=pin)

    def make_credential(self, rp_id: str, user_id: bytes, user_name: str) -> Credential:
        client_data_hash = secrets.token_bytes(32)
        needs_pin = not self._option("makeCredUvNotRqd") or self._option("alwaysUv")
        pin_auth = self._pin_auth(
            ClientPin.PERMISSION.MAKE_CREDENTIAL, rp_id, client_data_hash, needs_pin=needs_pin
        )
        response = self._run(
            lambda: self._ctap.make_credential(
                client_data_hash,
                {"id": rp_id, "name": _RP_NAME},
                {"id": user_id, "name": user_name},
                [{"type": "public-key", "alg": ES256.ALGORITHM}],
                **pin_auth,
            )
        )
        data = response.auth_data.credential_data
        if data is None:
            raise IdentityError("the security key returned no credential")
        return Credential(bytes(data.credential_id), _compressed(data.public_key))

    def get_assertion(self, rp_id: str, credential_id: bytes, client_data_hash: bytes) -> Assertion:
        pin_auth = self._pin_auth(
            ClientPin.PERMISSION.GET_ASSERTION,
            rp_id,
            client_data_hash,
            needs_pin=self._option("alwaysUv"),
        )
        response = self._run(
            lambda: self._ctap.get_assertion(
                rp_id,
                client_data_hash,
                [{"type": "public-key", "id": credential_id}],
                **pin_auth,
            )
        )
        return Assertion(bytes(response.auth_data), raw_signature(response.signature))

    def _option(self, name: str) -> bool:
        return self._ctap.info.options.get(name) is True

    def _pin_auth(
        self,
        permission: ClientPin.PERMISSION,
        rp_id: str,
        client_data_hash: bytes,
        *,
        needs_pin: bool,
    ) -> Mapping[str, Any]:
        if not needs_pin or not self._option("clientPin"):
            return {}
        pin = self._pin
        if pin is None:
            raise IdentityError(_ERRORS[CtapError.ERR.PUAT_REQUIRED])
        client_pin = ClientPin(self._ctap)
        token = self._run(lambda: client_pin.get_pin_token(pin(), permission, rp_id))
        return {
            "pin_uv_param": client_pin.protocol.authenticate(token, client_data_hash),
            "pin_uv_protocol": client_pin.protocol.VERSION,
        }

    @staticmethod
    def _run(call: Callable[[], T]) -> T:
        try:
            return call()
        except CtapError as exc:
            message = _ERRORS.get(exc.code, f"security key error: {exc}")
            raise IdentityError(message) from exc
        except OSError as exc:
            raise HardwareUnavailableError(f"lost the security key: {exc}") from exc


def _compressed(key: CoseKey) -> bytes:
    if not isinstance(key, ES256) or key.get(-1) != _P256:
        raise IdentityError("the security key made a credential that is not ES256 on P-256")
    x, y = key[-2], key[-3]
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    )
    return compress_public_key(numbers.public_key())

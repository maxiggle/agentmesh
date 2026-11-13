import hashlib
import re
from decimal import Decimal, localcontext
from enum import Enum

PAYLOAD_DOMAIN = "ehor-escrow/v1"
KEY_PAYLOAD_DOMAIN = "ehor-escrow/key/v1"
X25519_KEY_BYTES = 32
SEPARATOR = "|"
YOCTO_PER_NEAR = 10**24
_NEAR_AMOUNT = re.compile(r"(\d{1,12})(?:\.(\d{1,24}))?")


class PayloadError(ValueError):
    pass


class KeyAction(Enum):
    ADD = "add"
    REMOVE = "remove"


def format_near(amount_yocto: int) -> str:
    with localcontext(prec=80):
        near = Decimal(amount_yocto).scaleb(-24).normalize()
    return f"{near:f}"


def parse_near(text: str) -> int:
    """Turn a positive NEAR amount such as `0.5` into yoctoNEAR, exactly."""
    match = _NEAR_AMOUNT.fullmatch(text.strip())
    if match is None:
        raise PayloadError(f"not a NEAR amount: {text!r}")
    whole, fraction = match.group(1), match.group(2) or ""
    amount = int(whole) * YOCTO_PER_NEAR + int(fraction.ljust(24, "0"))
    if amount == 0:
        raise PayloadError("a NEAR amount must be more than 0")
    return amount


def release_payload(
    chain_id: str, contract: str, bounty_id: int, claimant: str, amount_yocto: int
) -> str:
    """Build the string the sender's device key signs to release a bounty.

    It must match `release_payload` in the contract byte for byte; see docs/escrow.md.
    """
    for name, field in (("chain_id", chain_id), ("contract", contract), ("claimant", claimant)):
        if not field or SEPARATOR in field:
            raise PayloadError(f"{name} must be non-empty and contain no {SEPARATOR!r}")
    # bool is an int subclass; True would silently encode as bounty 1.
    for name, number in (("bounty_id", bounty_id), ("amount_yocto", amount_yocto)):
        if isinstance(number, bool) or number < 0:
            raise PayloadError(f"{name} must be a non-negative integer")
    if amount_yocto == 0:
        raise PayloadError("amount_yocto must be positive")
    return SEPARATOR.join(
        (PAYLOAD_DOMAIN, chain_id, contract, str(bounty_id), claimant, str(amount_yocto))
    )


def release_digest(payload: str) -> bytes:
    return hashlib.sha256(payload.encode("utf-8")).digest()


def encryption_key_payload(
    chain_id: str,
    contract: str,
    account: str,
    *,
    nonce: int,
    action: KeyAction,
    key: bytes,
) -> str:
    """Build the string the release key signs to add or remove an encryption key.

    It must match `encryption_key_payload` in the contract byte for byte; see docs/escrow.md.
    """
    for name, field in (("chain_id", chain_id), ("contract", contract), ("account", account)):
        if not field or SEPARATOR in field:
            raise PayloadError(f"{name} must be non-empty and contain no {SEPARATOR!r}")
    if isinstance(nonce, bool) or nonce < 0:
        raise PayloadError("nonce must be a non-negative integer")
    if len(key) != X25519_KEY_BYTES:
        raise PayloadError(f"an encryption key is {X25519_KEY_BYTES} bytes")
    return SEPARATOR.join(
        (KEY_PAYLOAD_DOMAIN, chain_id, contract, account, str(nonce), action.value, key.hex())
    )

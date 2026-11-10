import hashlib

PAYLOAD_DOMAIN = "ehor-escrow/v1"
SEPARATOR = "|"


class PayloadError(ValueError):
    pass


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

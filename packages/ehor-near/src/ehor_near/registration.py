import asyncio

from ehor_near.client import ONE_YOCTO, NearClient
from ehor_near.limited_key import LimitedKeyError, check_access_key
from ehor_near.payload import format_near
from ehor_near.wallet_page import WalletCall

CONFIRM_ATTEMPTS = 10
CONFIRM_DELAY = 2.0


class RegistrationError(Exception):
    pass


async def _registered_key(near: NearClient, contract: str, account: str) -> bytes | None:
    data = await near.view(contract, "get_account", {"account_id": account})
    if data is None:
        return None
    key = data.get("device_key") if isinstance(data, dict) else None
    if not isinstance(key, str):
        raise RegistrationError("the contract returned a malformed account")
    try:
        return bytes.fromhex(key)
    except ValueError as exc:
        raise RegistrationError("the contract returned a malformed account") from exc


async def registration_call(
    near: NearClient,
    contract: str,
    account: str,
    release_key: bytes,
    *,
    max_per_bounty_yocto: int,
    deposit_yocto: int,
) -> WalletCall | None:
    """The call that makes the contract trust `release_key` for `account`, if one is needed."""
    current = await _registered_key(near, contract, account)
    if current == release_key:
        return None
    if current is None:
        return WalletCall(
            contract,
            "register",
            {"p256_public_key": release_key.hex(), "max_per_bounty": str(max_per_bounty_yocto)},
            deposit_yocto,
            f"This registers {account} with a deposit of {format_near(deposit_yocto)} NEAR "
            f"as your bounty budget and a limit of {format_near(max_per_bounty_yocto)} NEAR "
            "per bounty.",
        )
    return WalletCall(
        contract,
        "set_device_key",
        {"p256_public_key": release_key.hex()},
        ONE_YOCTO,
        f"{account} is already registered with another release key. This replaces it with "
        "this Mac's key, so only this Mac can approve payouts.",
    )


async def confirm_registration(
    near: NearClient,
    contract: str,
    account: str,
    *,
    limited_public_key: str,
    release_key: bytes,
    max_allowance_yocto: int,
    attempts: int = CONFIRM_ATTEMPTS,
    delay: float = CONFIRM_DELAY,
) -> None:
    """Wait until the chain shows the limited key and the release key, then check both.

    The wallet page only reports what happened; this is the confirmation ehor relies on.
    """
    for attempt in range(attempts):
        permission = await near.access_key(account, limited_public_key)
        registered = await _registered_key(near, contract, account)
        if permission is not None and registered == release_key:
            check_access_key(permission, contract, max_allowance_yocto)
            return
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    if permission is None:
        raise LimitedKeyError(f"the NEAR key {limited_public_key} is not on {account}")
    check_access_key(permission, contract, max_allowance_yocto)
    raise RegistrationError(f"{contract} does not have this Mac's release key for {account}")

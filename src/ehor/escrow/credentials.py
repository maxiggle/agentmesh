import json
from pathlib import Path

from ehor.escrow.client import TESTNET, is_account_id


class CredentialsError(Exception):
    pass


def default_credentials_dir() -> Path:
    return Path.home() / ".near-credentials"


def load_account_key(account_id: str, root: Path | None = None, network: str = TESTNET) -> str:
    """The private key NEAR CLI or cargo-near saved for `account_id`.

    Errors name the file but never include its contents, which hold the key.
    """
    if network != TESTNET:
        raise CredentialsError(f"only {TESTNET} keys are supported, got {network!r}")
    if not is_account_id(account_id):
        raise CredentialsError(f"not a NEAR account id: {account_id!r}")
    path = (root or default_credentials_dir()) / network / f"{account_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CredentialsError(
            f"no key for {account_id} in {path.parent}; log in with NEAR CLI first"
        ) from exc
    except (OSError, ValueError) as exc:
        raise CredentialsError(f"cannot read {path}") from exc
    if not isinstance(data, dict):
        raise CredentialsError(f"{path} is not a NEAR credentials file")
    if data.get("account_id", account_id) != account_id:
        raise CredentialsError(f"{path} holds a key for another account")
    key = data.get("private_key")
    if not isinstance(key, str) or not key:
        raise CredentialsError(f"{path} has no private_key")
    return key

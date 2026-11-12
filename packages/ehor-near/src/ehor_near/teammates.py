import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ehor.log.names import HANDLE
from ehor_near.client import is_account_id

TEAMMATES_FILE = "near-teammates.json"


class TeammateError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TeammateAccounts:
    """The NEAR account a person linked to each teammate's ehor handle.

    Only `ehor near teammate` writes this file. Bounties are posted to, and claimed from, these
    accounts, so no message or agent can redirect a bounty by naming another account.
    """

    accounts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for handle, account in self.accounts.items():
            _check(handle, account)
        object.__setattr__(self, "accounts", MappingProxyType(dict(sorted(self.accounts.items()))))

    def account_for(self, handle: str) -> str | None:
        return self.accounts.get(handle)

    def linked(self, handle: str, account: str) -> "TeammateAccounts":
        _check(handle, account)
        others = {key: value for key, value in self.accounts.items() if value != account}
        return TeammateAccounts({**others, handle: account})

    def unlinked(self, handle: str) -> "TeammateAccounts":
        if handle not in self.accounts:
            raise TeammateError(f"{handle} has no linked NEAR account")
        return TeammateAccounts({k: v for k, v in self.accounts.items() if k != handle})

    def save(self, home: Path) -> None:
        (home / TEAMMATES_FILE).write_text(json.dumps(dict(self.accounts), indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "TeammateAccounts":
        path = home / TEAMMATES_FILE
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except ValueError as exc:
            raise TeammateError(f"invalid {path}: {exc}") from exc
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in data.items()
        ):
            raise TeammateError(f"invalid {path}: expected handle to account pairs")
        return cls(data)


def _check(handle: str, account: str) -> None:
    if HANDLE.fullmatch(handle) is None:
        raise TeammateError(f"not an ehor handle: {handle!r}")
    if not is_account_id(account):
        raise TeammateError(f"not a NEAR account id: {account!r}")

import re
import unicodedata
from dataclasses import dataclass

from ehor.log.canonical import CanonicalError, CborValue, decode, encode

SYSTEM = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
ACCOUNT_MAX = 64
KEY_MAX = 65


class AccountAnnounceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AccountAnnounce:
    """A device's account in another system, such as a NEAR account, and an optional public key
    that system holds for it. ehor never interprets either; it only carries them signed."""

    system: str
    account: str
    key: bytes | None = None

    def __post_init__(self) -> None:
        if SYSTEM.fullmatch(self.system) is None:
            raise AccountAnnounceError(f"system must match {SYSTEM.pattern}")
        if not 1 <= len(self.account) <= ACCOUNT_MAX or any(
            unicodedata.category(char).startswith("C") for char in self.account
        ):
            raise AccountAnnounceError(
                f"account must be 1-{ACCOUNT_MAX} characters with no control characters"
            )
        if self.key is not None and not 1 <= len(self.key) <= KEY_MAX:
            raise AccountAnnounceError(f"key must be 1-{KEY_MAX} bytes")

    def encode(self) -> bytes:
        value: dict[str, CborValue] = {"system": self.system, "account": self.account}
        if self.key is not None:
            value["key"] = self.key
        return encode(value)

    @classmethod
    def decode(cls, raw: bytes) -> "AccountAnnounce":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise AccountAnnounceError(str(exc)) from exc
        if not isinstance(value, dict) or not {"system", "account"} <= value.keys() <= {
            "system",
            "account",
            "key",
        }:
            raise AccountAnnounceError("account_announce must have system, account and maybe key")
        system, account, key = value["system"], value["account"], value.get("key")
        if not isinstance(system, str) or not isinstance(account, str):
            raise AccountAnnounceError("system and account must be text")
        if key is not None and not isinstance(key, bytes):
            raise AccountAnnounceError("key must be bytes")
        return cls(system, account, key)

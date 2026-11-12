from dataclasses import dataclass

from ehor.log.account_announce import AccountAnnounce, AccountAnnounceError
from ehor.log.entry import Kind
from ehor.store.base import Store


@dataclass(frozen=True, slots=True)
class AnnouncedAccount:
    handle: str
    device: bytes
    account: str
    key: bytes | None


class AccountDirectory:
    """Each device's current account per system: the one in its latest account_announce."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def accounts(self, system: str) -> dict[bytes, AccountAnnounce]:
        latest: dict[bytes, tuple[int, AccountAnnounce]] = {}
        for arrival in self._store.arrivals(kinds=[Kind.ACCOUNT_ANNOUNCE]):
            entry = arrival.entry
            try:
                announced = AccountAnnounce.decode(entry.content.body)
            except AccountAnnounceError:
                continue
            if announced.system == system and entry.seq >= latest.get(entry.author, (0,))[0]:
                latest[entry.author] = (entry.seq, announced)
        return {author: announced for author, (_, announced) in latest.items()}

from typing import Protocol

from agentmesh.log.entry import Entry


class StoreError(Exception):
    pass


class DuplicateEntryError(StoreError):
    pass


class ChainError(StoreError):
    pass


class Store(Protocol):
    def contains(self, entry_id: bytes) -> bool: ...

    def get(self, entry_id: bytes) -> Entry | None: ...

    def at(self, author: bytes, seq: int) -> list[Entry]:
        """All stored entries at this position; more than one means a fork."""
        ...

    def head(self, author: bytes) -> Entry | None: ...

    def log(self, author: bytes) -> list[Entry]: ...

    def append(self, entry: Entry) -> None:
        """Store the author's next entry, refusing anything that breaks `seq` or `prev`."""
        ...

    def insert(self, entry: Entry) -> None:
        """Store an entry that has already passed validation."""
        ...

    def mark_forked(self, author: bytes, seq: int) -> None: ...

    def forked_at(self, author: bytes) -> int | None: ...

    def park(self, entry: Entry) -> None: ...

    def parked(self, author: bytes, seq: int) -> list[Entry]: ...

    def unpark(self, entry_id: bytes) -> None: ...

from dataclasses import dataclass
from enum import StrEnum

from agentmesh.log.entry import Entry
from agentmesh.log.verify import DroppedEntryError, EntryVerifier, RejectedEntryError
from agentmesh.store.base import Store


class Outcome(StrEnum):
    STORED = "stored"
    FORKED = "forked"
    PARKED = "parked"
    IGNORED = "ignored"
    REJECTED = "rejected"
    DROPPED = "dropped"


@dataclass(frozen=True, slots=True)
class Receipt:
    outcome: Outcome
    entry_id: bytes | None = None
    reason: str | None = None
    released: tuple[bytes, ...] = ()


class Ingestor:
    """Runs protocol §5 on entries from other devices and stores the ones that pass.

    Roster authorization (step 8) is not applied yet; it arrives with the roster.
    """

    def __init__(self, store: Store, verifier: EntryVerifier) -> None:
        self._store = store
        self._verifier = verifier

    def receive(self, raw: bytes) -> Receipt:
        try:
            entry = self._verifier.decode(raw)
        except DroppedEntryError as exc:
            return Receipt(Outcome.DROPPED, reason=str(exc))
        except RejectedEntryError as exc:
            return Receipt(Outcome.REJECTED, reason=str(exc))
        if self._store.contains(entry.id):
            return Receipt(Outcome.IGNORED, entry.id)
        try:
            self._verifier.check(entry)
        except RejectedEntryError as exc:
            return Receipt(Outcome.REJECTED, entry.id, str(exc))
        receipt = self._place(entry)
        if receipt.outcome is not Outcome.STORED:
            return receipt
        return Receipt(Outcome.STORED, entry.id, released=self._release(entry))

    def _place(self, entry: Entry) -> Receipt:
        if self._store.contains(entry.id):
            self._store.unpark(entry.id)
            return Receipt(Outcome.IGNORED, entry.id)
        if self._store.at(entry.author, entry.seq):
            self._store.insert(entry)
            self._store.mark_forked(entry.author, entry.seq)
            return Receipt(Outcome.FORKED, entry.id, f"second entry at seq {entry.seq}")
        if entry.seq > 1:
            previous = self._store.at(entry.author, entry.seq - 1)
            if not previous:
                self._store.park(entry)
                return Receipt(Outcome.PARKED, entry.id, f"missing seq {entry.seq - 1}")
            if entry.content.prev not in {p.id for p in previous}:
                return Receipt(Outcome.REJECTED, entry.id, "prev does not match seq - 1")
        self._store.insert(entry)
        self._store.unpark(entry.id)
        return Receipt(Outcome.STORED, entry.id)

    def _release(self, stored: Entry) -> tuple[bytes, ...]:
        released: list[bytes] = []
        frontier = [stored]
        while frontier:
            current = frontier.pop()
            for waiting in self._store.parked(current.author, current.seq + 1):
                self._store.unpark(waiting.id)
                if self._place(waiting).outcome is Outcome.STORED:
                    released.append(waiting.id)
                    frontier.append(waiting)
        return tuple(released)

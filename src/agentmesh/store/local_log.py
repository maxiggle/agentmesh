import time
from collections.abc import Callable, Iterable

from agentmesh.identity import KeyHandle, Signer
from agentmesh.log.authorize import Authorizer, Verdict
from agentmesh.log.entry import Entry, EntryContent, Kind
from agentmesh.log.verify import EntryVerifier
from agentmesh.store.base import Store


class NotAuthorizedError(Exception):
    pass


def wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class LocalLog:
    """Writes this device's own append-only log."""

    def __init__(
        self,
        store: Store,
        signer: Signer,
        handle: KeyHandle,
        team: bytes,
        verifier: EntryVerifier,
        *,
        clock: Callable[[], int] = wall_clock_ms,
        authorizer: Authorizer | None = None,
    ) -> None:
        self._store = store
        self._signer = signer
        self._handle = handle
        self._team = team
        self._verifier = verifier
        self._clock = clock
        self._authorizer = authorizer
        self._author = signer.public_key(handle)

    @property
    def author(self) -> bytes:
        return self._author

    def append(self, channel: str, kind: Kind, body: bytes, refs: Iterable[bytes] = ()) -> Entry:
        head = self._store.head(self._author)
        content = EntryContent(
            author=self._author,
            seq=1 if head is None else head.seq + 1,
            prev=None if head is None else head.id,
            team=self._team,
            channel=channel,
            kind=kind,
            refs=tuple(sorted(set(refs))),
            ts=self._clock(),
            body=body,
        )
        entry = content.sign(self._signer, self._handle)
        self._verifier.verify(entry.encode())
        if self._authorizer is not None:
            decision = self._authorizer.authorize(entry)
            if decision.verdict is not Verdict.ALLOW:
                raise NotAuthorizedError(decision.reason)
        self._store.append(entry)
        if self._authorizer is not None:
            self._authorizer.stored(entry)
        return entry

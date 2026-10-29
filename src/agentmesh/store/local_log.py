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
    """Writes the logs this device authors: its own key's, and those of security keys
    plugged into it, which sign outside the device (see `draft` and `commit`)."""

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
        content = self.draft(channel, kind, body, refs)
        return self.commit(content.sign(self._signer, self._handle))

    def draft(
        self,
        channel: str,
        kind: Kind,
        body: bytes,
        refs: Iterable[bytes] = (),
        *,
        author: bytes | None = None,
    ) -> EntryContent:
        """The next entry in `author`'s log (this device's by default), not yet signed."""
        author = self._author if author is None else author
        head = self._store.head(author)
        return EntryContent(
            author=author,
            seq=1 if head is None else head.seq + 1,
            prev=None if head is None else head.id,
            team=self._team,
            channel=channel,
            kind=kind,
            refs=tuple(sorted(set(refs))),
            ts=self._clock(),
            body=body,
        )

    def commit(self, entry: Entry) -> Entry:
        """Validate and store a signed draft. Fails if the author's log moved on meanwhile."""
        self._verifier.verify(entry.encode())
        if self._authorizer is not None:
            decision = self._authorizer.authorize(entry)
            if decision.verdict is not Verdict.ALLOW:
                raise NotAuthorizedError(decision.reason)
        self._store.append(entry)
        if self._authorizer is not None:
            self._authorizer.accepted(entry)
        return entry

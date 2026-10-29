from collections.abc import Callable, Iterable
from pathlib import Path

from agentmesh.store.base import Store
from agentmesh.store.ingest import Ingestor, Outcome

SUFFIX = ".entry"


class DirectoryExchange:
    """Swaps entries through a shared folder.

    Each device writes its own entries under a folder named after its key
    and reads every other device's folder. Entries are self-verifying, so
    the folder itself needs no trust: it can be local or synced by any
    file-sync service.

    `signed_here` names other logs this device writes, such as those of its owner's
    security keys, which have no device of their own to export them.
    """

    def __init__(
        self,
        folder: Path,
        store: Store,
        ingestor: Ingestor,
        author: bytes,
        *,
        signed_here: Callable[[], Iterable[bytes]] = tuple,
    ) -> None:
        self._folder = folder
        self._store = store
        self._ingestor = ingestor
        self._author = author
        self._signed_here = signed_here
        self._imported: set[Path] = set()

    def sync(self) -> int:
        self.export()
        return self.import_new()

    def export(self) -> int:
        written = 0
        for author in self._local_authors():
            own = self._folder / author.hex()
            own.mkdir(parents=True, exist_ok=True)
            for entry in self._store.log(author):
                path = own / f"{entry.seq:010d}-{entry.id.hex()}{SUFFIX}"
                if path.exists():
                    continue
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(entry.encode())
                temporary.replace(path)
                written += 1
        return written

    def import_new(self) -> int:
        stored = 0
        if not self._folder.is_dir():
            return stored
        local = {author.hex() for author in self._local_authors()}
        for author_dir in sorted(self._folder.iterdir()):
            if not author_dir.is_dir() or author_dir.name in local:
                continue
            for path in sorted(author_dir.glob(f"*{SUFFIX}")):
                if path in self._imported:
                    continue
                receipt = self._ingestor.receive(path.read_bytes())
                if receipt.outcome is not Outcome.PARKED:
                    self._imported.add(path)
                stored += receipt.outcome is Outcome.STORED
                stored += len(receipt.released)
        return stored

    def _local_authors(self) -> list[bytes]:
        return [self._author, *self._signed_here()]

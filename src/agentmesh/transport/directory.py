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
    """

    def __init__(self, folder: Path, store: Store, ingestor: Ingestor, author: bytes) -> None:
        self._folder = folder
        self._store = store
        self._ingestor = ingestor
        self._author = author
        self._imported: set[Path] = set()

    def sync(self) -> int:
        self.export()
        return self.import_new()

    def export(self) -> int:
        own = self._folder / self._author.hex()
        own.mkdir(parents=True, exist_ok=True)
        written = 0
        for entry in self._store.log(self._author):
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
        for author_dir in sorted(self._folder.iterdir()):
            if not author_dir.is_dir() or author_dir.name == self._author.hex():
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

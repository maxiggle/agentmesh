import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import ENTRY_ID_SIZE, TEAM_ID_SIZE, Kind
from agentmesh.log.verify import EntryVerifier
from agentmesh.roster.book import RosterBook
from agentmesh.store.ingest import Ingestor
from agentmesh.store.sqlite import SqliteStore
from agentmesh.sync.session import SyncNode

CONFIG_FILE = "relay.json"
_KEY_LABEL = "relay"
_HOME_MODE = 0o700


class RelayError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RelayConfig:
    team: bytes
    genesis: bytes

    def __post_init__(self) -> None:
        if len(self.team) != TEAM_ID_SIZE or len(self.genesis) != ENTRY_ID_SIZE:
            raise RelayError("relay config has a malformed team or genesis id")

    def save(self, home: Path) -> None:
        data = {"team": self.team.hex(), "genesis": self.genesis.hex()}
        (home / CONFIG_FILE).write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, home: Path) -> "RelayConfig":
        try:
            data = json.loads((home / CONFIG_FILE).read_text())
            return cls(bytes.fromhex(data["team"]), bytes.fromhex(data["genesis"]))
        except FileNotFoundError as exc:
            raise RelayError(f"no relay at {home}; run `agentmesh relay init` first") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise RelayError(f"invalid {home / CONFIG_FILE}: {exc}") from exc


class Relay:
    """Stores a team's entries and serves them to its devices. It never writes entries.

    It accepts only what the roster authorizes, serves full logs only to roster
    members, and can read nothing: bodies arrive already encrypted.
    """

    def __init__(self, home: Path, config: RelayConfig, passphrase: bytes) -> None:
        signer = SoftwareSigner(home / "keys", passphrase)
        key = KeyHandle(SoftwareSigner.backend, _KEY_LABEL)
        self._store = SqliteStore(home / "relay.db")
        self._book = RosterBook(config.genesis)
        self._book.load(
            [
                *(arrival.entry for arrival in self._store.arrivals(kinds=[Kind.ROSTER])),
                *(e for e in self._store.all_parked() if e.content.kind is Kind.ROSTER),
            ]
        )
        ingestor = Ingestor(self._store, EntryVerifier(config.team), self._book)
        self._node = SyncNode(
            self._store,
            ingestor,
            signer,
            key,
            config.team,
            is_member=lambda peer: self._book.member_for_key(peer) is not None,
        )

    @classmethod
    def create(cls, home: Path, team: bytes, genesis: bytes, passphrase: bytes) -> "Relay":
        if (home / CONFIG_FILE).exists():
            raise RelayError(f"a relay already exists at {home}")
        config = RelayConfig(team, genesis)
        home.mkdir(parents=True, exist_ok=True, mode=_HOME_MODE)
        SoftwareSigner(home / "keys", passphrase).create_key(_KEY_LABEL)
        config.save(home)
        return cls(home, config, passphrase)

    @classmethod
    def open(cls, home: Path, passphrase: bytes) -> "Relay":
        return cls(home, RelayConfig.load(home), passphrase)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def key(self) -> bytes:
        return self._node.author

    @property
    def roster(self) -> RosterBook:
        return self._book

    @property
    def node(self) -> SyncNode:
        return self._node

    def frontier(self) -> dict[bytes, int]:
        return self._store.frontier()

    async def start(self, host: str, port: int) -> asyncio.Server:
        return await self._node.serve(host, port)

    async def serve(self, host: str, port: int, stop: asyncio.Event) -> None:
        server = await self.start(host, port)
        try:
            await stop.wait()
        finally:
            server.close()
            await server.wait_closed()

    def close(self) -> None:
        self._store.close()

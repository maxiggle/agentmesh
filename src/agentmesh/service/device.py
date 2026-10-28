import secrets
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import TEAM_ID_SIZE, Kind
from agentmesh.log.names import CONTROL_CHANNEL
from agentmesh.log.roster import ALL_CHANNELS, Member, Roster, RosterDevice, RosterError, Trust
from agentmesh.log.verify import EntryVerifier
from agentmesh.roster.book import RosterBook
from agentmesh.service.api import ToolApi
from agentmesh.service.config import CONFIG_FILE, ConfigError, DeviceConfig
from agentmesh.service.directory import RosterDirectory
from agentmesh.service.invite import Invite
from agentmesh.service.messenger import Messenger
from agentmesh.service.roster_admin import AdminApi, RosterAdmin
from agentmesh.store.ingest import Ingestor
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore
from agentmesh.transport.directory import DirectoryExchange

_HOME_MODE = 0o700
_DB_FILE = "agentmesh.db"
_KEY_DIR = "keys"


class Device:
    """One device's keys, log, roster, store and exchange, wired together from its home folder."""

    def __init__(self, home: Path, config: DeviceConfig, passphrase: bytes) -> None:
        self._config = config
        signer = SoftwareSigner(home / _KEY_DIR, passphrase)
        key = KeyHandle(SoftwareSigner.backend, config.key_label)
        self._store = SqliteStore(home / _DB_FILE)
        self._book = RosterBook(config.genesis)
        self._book.load(arrival.entry for arrival in self._store.arrivals(kinds=[Kind.ROSTER]))
        verifier = EntryVerifier(config.team)
        local_log = LocalLog(self._store, signer, key, config.team, verifier, authorizer=self._book)
        self._author = local_log.author
        directory = RosterDirectory(self._book, self._author, config.handle)
        self._api = ToolApi(Messenger(self._store, local_log, directory))
        self._admin = AdminApi(RosterAdmin(self._book, local_log))
        ingestor = Ingestor(self._store, verifier, self._book)
        self._exchange = (
            None
            if config.exchange is None
            else DirectoryExchange(config.exchange, self._store, ingestor, self._author)
        )

    @classmethod
    def create_team(
        cls,
        home: Path,
        handle: str,
        passphrase: bytes,
        *,
        team_name: str = "",
        exchange: Path | None = None,
    ) -> "Device":
        """Start a new team with this device as its first admin."""
        signer = cls._new_key(home, passphrase)
        team = secrets.token_bytes(TEAM_ID_SIZE)
        key = KeyHandle(SoftwareSigner.backend, "device")
        try:
            device = RosterDevice(signer.public_key(key), Trust.SOFTWARE, handle)
            member = Member(handle, (), (ALL_CHANNELS,), (device,))
            genesis = Roster(1, None, team_name, (handle,), (member,))
        except RosterError as exc:
            raise ConfigError(str(exc)) from exc
        with SqliteStore(home / _DB_FILE) as store:
            local_log = LocalLog(store, signer, key, team, EntryVerifier(team))
            entry = local_log.append(CONTROL_CHANNEL, Kind.ROSTER, genesis.encode())
        config = DeviceConfig(handle, team, entry.id, exchange=exchange)
        config.save(home)
        return cls(home, config, passphrase)

    @classmethod
    def join(
        cls,
        home: Path,
        invite: Invite,
        handle: str,
        passphrase: bytes,
        *,
        exchange: Path | None = None,
    ) -> "Device":
        """Set up a device that trusts only the genesis roster named in the invite."""
        config = DeviceConfig(handle, invite.team, invite.genesis, exchange=exchange)
        cls._new_key(home, passphrase)
        config.save(home)
        return cls(home, config, passphrase)

    @classmethod
    def open(cls, home: Path, passphrase: bytes) -> "Device":
        return cls(home, DeviceConfig.load(home), passphrase)

    @staticmethod
    def _new_key(home: Path, passphrase: bytes) -> SoftwareSigner:
        if (home / CONFIG_FILE).exists():
            raise ConfigError(f"a device already exists at {home}")
        home.mkdir(parents=True, exist_ok=True, mode=_HOME_MODE)
        signer = SoftwareSigner(home / _KEY_DIR, passphrase)
        signer.create_key("device")
        return signer

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
    def config(self) -> DeviceConfig:
        return self._config

    @property
    def author(self) -> bytes:
        return self._author

    @property
    def invite(self) -> Invite:
        return Invite(self._config.team, self._config.genesis)

    @property
    def roster(self) -> RosterBook:
        return self._book

    def sync(self) -> int:
        return 0 if self._exchange is None else self._exchange.sync()

    def call(self, method: str, params: Mapping[str, object]) -> str:
        self.sync()
        if method.startswith(AdminApi.PREFIX):
            result = self._admin.call(method, params)
        else:
            result = self._api.call(method, params)
        self.sync()
        return result

    def close(self) -> None:
        self._store.close()

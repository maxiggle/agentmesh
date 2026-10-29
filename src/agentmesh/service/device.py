import secrets
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from agentmesh.crypto.keyfile import EncryptionKeyFile
from agentmesh.identity import KeyHandle, Signer
from agentmesh.identity.backends import Backend, detect_hardware, open_signer
from agentmesh.log.entry import TEAM_ID_SIZE, Kind
from agentmesh.log.key_announce import KeyAnnounce
from agentmesh.log.names import CONTROL_CHANNEL
from agentmesh.log.roster import ALL_CHANNELS, Member, Roster, RosterDevice, RosterError, Trust
from agentmesh.log.verify import EntryVerifier
from agentmesh.roster.book import RosterBook
from agentmesh.service.api import Params, ToolApi
from agentmesh.service.config import CONFIG_FILE, ConfigError, DeviceConfig
from agentmesh.service.directory import RosterDirectory
from agentmesh.service.invite import Invite
from agentmesh.service.messenger import Messenger
from agentmesh.service.roster_admin import AdminApi, RosterAdmin
from agentmesh.service.sealing import KeyDirectory, SealingCodec
from agentmesh.store.ingest import Ingestor
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore
from agentmesh.sync.session import SyncNode
from agentmesh.transport.directory import DirectoryExchange

_HOME_MODE = 0o700
_DB_FILE = "agentmesh.db"
_KEY_DIR = "keys"
_TOOLS_DIR = "bin"
_KEY_LABEL = "device"
_ENCRYPTION_KEY = "encryption.key"
INVITE_METHOD = "invite"


class Device:
    """One device's keys, log, roster, store and exchange, wired together from its home folder."""

    def __init__(self, home: Path, config: DeviceConfig, passphrase: bytes) -> None:
        self._config = config
        signer = _signer(home, config.key_backend, passphrase)
        key = KeyHandle(config.key_backend.value, config.key_label)
        self._store = SqliteStore(home / _DB_FILE)
        self._book = RosterBook(config.genesis)
        self._book.load(
            [
                *(arrival.entry for arrival in self._store.arrivals(kinds=[Kind.ROSTER])),
                *(e for e in self._store.all_parked() if e.content.kind is Kind.ROSTER),
            ]
        )
        verifier = EntryVerifier(config.team)
        local_log = LocalLog(self._store, signer, key, config.team, verifier, authorizer=self._book)
        self._author = local_log.author
        self._local_log = local_log
        self._keys = KeyDirectory(self._store)
        encryption_key = EncryptionKeyFile(home / _KEY_DIR / _ENCRYPTION_KEY, passphrase)
        self._codec = SealingCodec(
            self._book, self._keys, encryption_key.load_or_create(), self._author, config.team
        )
        directory = RosterDirectory(self._book, self._author, config.handle)
        self._api = ToolApi(Messenger(self._store, local_log, directory, self._codec))
        self._admin = AdminApi(RosterAdmin(self._book, local_log))
        ingestor = Ingestor(self._store, verifier, self._book)
        self._sync_node = SyncNode(
            self._store,
            ingestor,
            signer,
            key,
            config.team,
            is_member=self._book.may_receive_logs,
        )
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
        key_backend: Backend | None = Backend.SOFTWARE,
    ) -> "Device":
        """Start a new team with this device as its first admin.

        `key_backend=None` uses this machine's hardware key if it has one.
        """
        signer, key = cls._new_key(home, passphrase, key_backend)
        key_backend = Backend(key.backend)
        team = secrets.token_bytes(TEAM_ID_SIZE)
        try:
            device = RosterDevice(signer.public_key(key), trust_for(key_backend), handle)
            member = Member(handle, (), (ALL_CHANNELS,), (device,))
            genesis = Roster(1, None, team_name, (handle,), (member,), require_encryption=True)
        except RosterError as exc:
            raise ConfigError(str(exc)) from exc
        with SqliteStore(home / _DB_FILE) as store:
            local_log = LocalLog(store, signer, key, team, EntryVerifier(team))
            entry = local_log.append(CONTROL_CHANNEL, Kind.ROSTER, genesis.encode())
        config = DeviceConfig(handle, team, entry.id, exchange=exchange, key_backend=key_backend)
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
        key_backend: Backend | None = Backend.SOFTWARE,
    ) -> "Device":
        """Set up a device that trusts only the genesis roster named in the invite.

        `key_backend=None` uses this machine's hardware key if it has one.
        """
        _, key = cls._new_key(home, passphrase, key_backend)
        config = DeviceConfig(
            handle,
            invite.team,
            invite.genesis,
            exchange=exchange,
            relay=invite.relay,
            key_backend=Backend(key.backend),
        )
        config.save(home)
        return cls(home, config, passphrase)

    @classmethod
    def open(cls, home: Path, passphrase: bytes) -> "Device":
        return cls(home, DeviceConfig.load(home), passphrase)

    @staticmethod
    def _new_key(
        home: Path, passphrase: bytes, backend: Backend | None
    ) -> tuple[Signer, KeyHandle]:
        if (home / CONFIG_FILE).exists():
            raise ConfigError(f"a device already exists at {home}")
        home.mkdir(parents=True, exist_ok=True, mode=_HOME_MODE)
        if backend is None:
            found = detect_hardware(home / _KEY_DIR, tools=home / _TOOLS_DIR)
            backend = Backend.SOFTWARE if found is None else found
        signer = _signer(home, backend, passphrase)
        return signer, signer.create_key(_KEY_LABEL)

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
        relays = self.relay_addresses()
        return Invite(self._config.team, self._config.genesis, relays[0] if relays else None)

    def relay_addresses(self) -> list[str]:
        """The roster's relays, or the invite's relay until the roster names any."""
        roster = self._book.current
        listed = [relay.address for relay in roster.relays] if roster is not None else []
        if listed:
            return listed
        return [self._config.relay] if self._config.relay else []

    @property
    def roster(self) -> RosterBook:
        return self._book

    @property
    def sync_node(self) -> SyncNode:
        return self._sync_node

    def sync(self) -> int:
        self.announce_key()
        return 0 if self._exchange is None else self._exchange.sync()

    def announce_key(self) -> bool:
        """Publish this device's encryption key once it is on the roster, or when it changes."""
        if self._book.member_for_key(self._author) is None:
            return False
        if self._keys.encryption_keys().get(self._author) == self._codec.public_key:
            return False
        body = KeyAnnounce(self._codec.public_key).encode()
        self._local_log.append(CONTROL_CHANNEL, Kind.KEY_ANNOUNCE, body)
        return True

    def call(self, method: str, params: Mapping[str, object]) -> str:
        self.sync()
        if method == INVITE_METHOD:
            Params(params).finish()
            return self.invite.encode()
        if method.startswith(AdminApi.PREFIX):
            result = self._admin.call(method, params)
        else:
            result = self._api.call(method, params)
        self.sync()
        return result

    def close(self) -> None:
        self._store.close()


def trust_for(backend: Backend) -> Trust:
    return Trust.HARDWARE if backend.hardware else Trust.SOFTWARE


def _signer(home: Path, backend: Backend, passphrase: bytes) -> Signer:
    return open_signer(backend, home / _KEY_DIR, passphrase, tools=home / _TOOLS_DIR)

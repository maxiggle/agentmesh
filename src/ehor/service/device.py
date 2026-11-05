import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from ehor.crypto.keyfile import EncryptionKeyFile
from ehor.identity import KeyHandle, Signer
from ehor.identity.backends import Backend, detect_hardware, open_signer
from ehor.identity.security_key import Authenticator, SecurityKeySigner
from ehor.log.endpoint_announce import EndpointAnnounce
from ehor.log.entry import TEAM_ID_SIZE, Kind
from ehor.log.key_announce import KeyAnnounce
from ehor.log.names import CONTROL_CHANNEL, HANDLE, HANDLE_RULE
from ehor.log.roster import (
    ALL_CHANNELS,
    TEAM_NAME_MAX,
    Member,
    Roster,
    RosterDevice,
    RosterError,
    Trust,
)
from ehor.log.verify import EntryVerifier
from ehor.roster.book import RosterBook
from ehor.service.api import Params, ToolApi
from ehor.service.config import CONFIG_FILE, ConfigError, DeviceConfig
from ehor.service.directory import RosterDirectory
from ehor.service.endpoints import EndpointDirectory
from ehor.service.invite import Invite
from ehor.service.iroh_key import endpoint_id_for, load_or_create_secret, stored_endpoint_id
from ehor.service.messenger import Messenger
from ehor.service.roster_admin import AdminApi, RosterAdmin
from ehor.service.sealing import KeyDirectory, SealingCodec
from ehor.store.ingest import Ingestor
from ehor.store.local_log import LocalLog
from ehor.store.sqlite import SqliteStore
from ehor.sync.session import SyncNode
from ehor.transport.directory import DirectoryExchange

_HOME_MODE = 0o700
_DB_FILE = "ehor.db"
_KEY_DIR = "keys"
_TOOLS_DIR = "bin"
_KEY_LABEL = "device"
_ENCRYPTION_KEY = "encryption.key"
_IROH_KEY = "iroh.key"
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
        self._iroh_secret = load_or_create_secret(home / _KEY_DIR / _IROH_KEY)
        self._endpoints = EndpointDirectory(self._store)
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
            else DirectoryExchange(
                config.exchange,
                self._store,
                ingestor,
                self._author,
                signed_here=self._security_keys,
            )
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
        cls.check_setup(home, handle, team_name)
        cls.discard_unfinished_setup(home)
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
        cls.check_setup(home, handle)
        cls.discard_unfinished_setup(home)
        _, key = cls._new_key(home, passphrase, key_backend)
        config = DeviceConfig(
            handle,
            invite.team,
            invite.genesis,
            exchange=exchange,
            relay=invite.relay,
            bootstrap_endpoint=invite.iroh,
            key_backend=Backend(key.backend),
        )
        config.save(home)
        return cls(home, config, passphrase)

    @staticmethod
    def stored_endpoint_id(home: Path) -> bytes | None:
        """The iroh endpoint ID of the device at `home`, readable without its passphrase."""
        return stored_endpoint_id(home / _KEY_DIR / _IROH_KEY)

    @classmethod
    def open(cls, home: Path, passphrase: bytes) -> "Device":
        return cls(home, DeviceConfig.load(home), passphrase)

    @staticmethod
    def check_setup(home: Path, handle: str, team_name: str = "") -> None:
        """Refuse a new device at `home` before any key is made or passphrase asked for."""
        if (home / CONFIG_FILE).exists():
            raise ConfigError(f"a device already exists at {home}; use --home for another one")
        if not HANDLE.fullmatch(handle):
            raise ConfigError(f"invalid handle {handle!r}: {HANDLE_RULE}")
        if len(team_name) > TEAM_NAME_MAX:
            raise ConfigError(f"the team name can be at most {TEAM_NAME_MAX} characters")

    @staticmethod
    def discard_unfinished_setup(home: Path) -> bool:
        """Remove what an earlier init or join left behind before it saved a config.

        Without a config no team ever used these files, and a leftover key would
        otherwise block every later setup in this folder.
        """
        if (home / CONFIG_FILE).exists():
            return False
        keys = home / _KEY_DIR
        leftovers = [
            *(keys.glob(f"{_KEY_LABEL}.*") if keys.is_dir() else ()),
            *(keys / name for name in (_ENCRYPTION_KEY, _IROH_KEY)),
            *(home / f"{_DB_FILE}{suffix}" for suffix in ("", "-wal", "-shm")),
        ]
        removed = False
        for path in leftovers:
            if path.is_file():
                path.unlink()
                removed = True
        return removed

    @staticmethod
    def _new_key(
        home: Path, passphrase: bytes, backend: Backend | None
    ) -> tuple[Signer, KeyHandle]:
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
        return Invite(
            self._config.team,
            self._config.genesis,
            relays[0] if relays else None,
            self.endpoint_id,
        )

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

    def _security_keys(self) -> list[bytes]:
        """This member's security keys, whose roster changes this device stores and shares."""
        member = self._book.member_for_key(self._author)
        if member is None:
            return []
        return [device.key for device in member.devices if device.trust is Trust.SECURITY_KEY]

    def announce_key(self) -> bool:
        """Publish this device's encryption key once it is on the roster, or when it changes."""
        if self._book.member_for_key(self._author) is None:
            return False
        if self._keys.encryption_keys().get(self._author) == self._codec.public_key:
            return False
        body = KeyAnnounce(self._codec.public_key).encode()
        self._local_log.append(CONTROL_CHANNEL, Kind.KEY_ANNOUNCE, body)
        return True

    @property
    def iroh_secret(self) -> bytes:
        return self._iroh_secret

    @property
    def endpoint_id(self) -> bytes | None:
        """This device's iroh endpoint ID, or None when iroh is not installed."""
        return endpoint_id_for(self._iroh_secret)

    def announce_endpoint(self, endpoint_id: bytes) -> bool:
        """Publish this device's iroh endpoint once it is on the roster, or when it changes."""
        if self._book.member_for_key(self._author) is None:
            return False
        if self._endpoints.endpoints().get(self._author) == endpoint_id:
            return False
        body = EndpointAnnounce(endpoint_id).encode()
        self._local_log.append(CONTROL_CHANNEL, Kind.ENDPOINT_ANNOUNCE, body)
        return True

    def direct_peers(self) -> list[tuple[bytes, bytes]]:
        """`(device key, iroh endpoint ID)` for each other device of each current member.

        Until some other device of the roster has announced an endpoint, the endpoint
        named in the invite is listed too. It is not a roster device, so its endpoint ID
        stands in for the device key: nothing keyed on roster devices will match it.
        """
        roster = self._book.current
        announced = self._endpoints.endpoints()
        peers = (
            []
            if roster is None
            else [
                (device.key, announced[device.key])
                for member in roster.members
                for device in member.devices
                if device.key != self._author and device.key in announced
            ]
        )
        bootstrap = self._config.bootstrap_endpoint
        if not peers and bootstrap is not None and bootstrap != self.endpoint_id:
            peers.append((bootstrap, bootstrap))
        return peers

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


def security_key_signer(home: Path, connect: Callable[[], Authenticator]) -> SecurityKeySigner:
    """The security keys enrolled from this device, kept beside its own key."""
    return SecurityKeySigner(home / _KEY_DIR, connect)


def trust_for(backend: Backend) -> Trust:
    return Trust.HARDWARE if backend.hardware else Trust.SOFTWARE


def _signer(home: Path, backend: Backend, passphrase: bytes) -> Signer:
    return open_signer(backend, home / _KEY_DIR, passphrase, tools=home / _TOOLS_DIR)

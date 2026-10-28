from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.verify import EntryVerifier
from agentmesh.service.api import ToolApi
from agentmesh.service.config import CONFIG_FILE, ConfigError, DeviceConfig
from agentmesh.service.messenger import Messenger
from agentmesh.store.ingest import Ingestor
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore
from agentmesh.transport.directory import DirectoryExchange

_HOME_MODE = 0o700


class Device:
    """One device's keys, log, store and exchange, wired together from its home folder."""

    def __init__(self, home: Path, config: DeviceConfig, passphrase: bytes) -> None:
        self._config = config
        signer = SoftwareSigner(home / "keys", passphrase)
        key = KeyHandle(SoftwareSigner.backend, config.key_label)
        self._store = SqliteStore(home / "agentmesh.db")
        verifier = EntryVerifier(config.team)
        local_log = LocalLog(self._store, signer, key, config.team, verifier)
        self._author = local_log.author
        self._api = ToolApi(Messenger(self._store, local_log, config.identity))
        self._exchange = (
            None
            if config.exchange is None
            else DirectoryExchange(
                config.exchange, self._store, Ingestor(self._store, verifier), self._author
            )
        )

    @classmethod
    def create(cls, home: Path, config: DeviceConfig, passphrase: bytes) -> "Device":
        if (home / CONFIG_FILE).exists():
            raise ConfigError(f"a device already exists at {home}")
        home.mkdir(parents=True, exist_ok=True, mode=_HOME_MODE)
        SoftwareSigner(home / "keys", passphrase).create_key(config.key_label)
        config.save(home)
        return cls(home, config, passphrase)

    @classmethod
    def open(cls, home: Path, passphrase: bytes) -> "Device":
        return cls(home, DeviceConfig.load(home), passphrase)

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

    def sync(self) -> int:
        return 0 if self._exchange is None else self._exchange.sync()

    def call(self, method: str, params: Mapping[str, object]) -> str:
        self.sync()
        result = self._api.call(method, params)
        self.sync()
        return result

    def close(self) -> None:
        self._store.close()

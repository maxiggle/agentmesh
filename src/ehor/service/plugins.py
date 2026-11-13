import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

from ehor.log.account_announce import AccountAnnounce
from ehor.service.accounts import AnnouncedAccount
from ehor.service.messenger import Messenger
from ehor.service.roster_admin import AdminApi
from ehor.service.sealing import RecipientCheck

ToolHandler = Callable[[Mapping[str, object]], Awaitable[str]]

_METHOD = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


class PluginError(Exception):
    """A plugin method failed in a way the agent should be told about."""


class BackgroundTask(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...


class PluginHost(Protocol):
    """What `ehor serve` gives a plugin: the messenger, the device's account announcements,
    a say in which teammate keys messages are sealed for, and a place to run background work."""

    @property
    def messenger(self) -> Messenger: ...

    def announce_account(self, announcement: AccountAnnounce) -> bool: ...

    def announced_accounts(self, system: str) -> tuple[AnnouncedAccount, ...]: ...

    def check_recipients(self, check: RecipientCheck) -> None: ...

    def run_in_background(self, task: BackgroundTask) -> None: ...


class ServicePlugin(Protocol):
    def open(self, home: Path, passphrase: bytes, host: PluginHost) -> Mapping[str, ToolHandler]:
        """Return the methods this plugin serves from `ehor serve`, keyed by `<plugin>.<name>`."""
        ...


def check_method_name(name: str) -> None:
    # Namespaced names keep a plugin from shadowing a core tool or a roster command.
    if _METHOD.fullmatch(name) is None or name.startswith(AdminApi.PREFIX):
        raise PluginError(f"plugin method {name!r} must be named <plugin>.<name>")

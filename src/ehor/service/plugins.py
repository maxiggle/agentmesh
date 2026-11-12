import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

from ehor.service.messenger import Messenger
from ehor.service.roster_admin import AdminApi

ToolHandler = Callable[[Mapping[str, object]], Awaitable[str]]

_METHOD = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


class PluginError(Exception):
    """A plugin method failed in a way the agent should be told about."""


class ServicePlugin(Protocol):
    def open(
        self, home: Path, passphrase: bytes, messenger: Messenger
    ) -> Mapping[str, ToolHandler]:
        """Return the methods this plugin serves from `ehor serve`, keyed by `<plugin>.<name>`."""
        ...


def check_method_name(name: str) -> None:
    # Namespaced names keep a plugin from shadowing a core tool or a roster command.
    if _METHOD.fullmatch(name) is None or name.startswith(AdminApi.PREFIX):
        raise PluginError(f"plugin method {name!r} must be named <plugin>.<name>")

import os
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ehor.log.body import SUMMARY_MAX

APP_NAME = "ehor"
SENDER_MAX = 64
_COMMAND_TIMEOUT = 10.0
_WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_TITLE_ENV = "EHOR_NOTIFY_TITLE"
_BODY_ENV = "EHOR_NOTIFY_BODY"


class NotifyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Alert:
    """What a notification shows about a new message: teammate-written, so untrusted."""

    sender: str
    summary: str

    @property
    def title(self) -> str:
        return f"{APP_NAME}: {_plain(self.sender, SENDER_MAX)}"

    @property
    def body(self) -> str:
        return _plain(self.summary, SUMMARY_MAX)


class Notifier(Protocol):
    def notify(self, alert: Alert) -> None: ...


Runner = Callable[[Sequence[str], Mapping[str, str]], None]


def run_command(argv: Sequence[str], env: Mapping[str, str]) -> None:
    """Run a notification command with `env` added to this process's environment."""
    try:
        subprocess.run(  # noqa: S603 - fixed argv; teammate text is passed as data, never parsed
            list(argv),
            env={**os.environ, **env},
            check=True,
            capture_output=True,
            timeout=_COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyError(f"{argv[0]} failed: {exc}") from exc


class MacNotifier:
    """Notification Center through `osascript`.

    The text reaches the script as `argv` of its run handler, so it is never parsed as
    AppleScript. The title goes first and always starts with the app name, which keeps
    `osascript` from reading anything after it as an option.
    """

    _SCRIPT = (
        "on run argv",
        "display notification (item 2 of argv) with title (item 1 of argv)",
        "end run",
    )

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run

    def notify(self, alert: Alert) -> None:
        script = [arg for line in self._SCRIPT for arg in ("-e", line)]
        self._run(["osascript", *script, alert.title, alert.body], {})


class LinuxNotifier:
    """A desktop notification through `notify-send`."""

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run

    def notify(self, alert: Alert) -> None:
        self._run(
            ["notify-send", f"--app-name={APP_NAME}", "--", alert.title, _escape(alert.body)],
            {},
        )


class WindowsNotifier:
    """A toast through PowerShell's Windows Runtime bindings.

    The text travels in environment variables, so it is never part of the script.
    PowerShell's own app ID is used because Windows drops toasts from unregistered apps.
    """

    _SCRIPT = "; ".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$m = [Windows.UI.Notifications.ToastNotificationManager,"
            " Windows.UI.Notifications, ContentType = WindowsRuntime]",
            "$k = [Windows.UI.Notifications.ToastTemplateType]::ToastText02",
            "$t = $m::GetTemplateContent($k)",
            "$n = $t.GetElementsByTagName('text')",
            f"$n.Item(0).AppendChild($t.CreateTextNode($env:{_TITLE_ENV})) > $null",
            f"$n.Item(1).AppendChild($t.CreateTextNode($env:{_BODY_ENV})) > $null",
            f"$m::CreateToastNotifier('{_WINDOWS_APP_ID}')"
            ".Show([Windows.UI.Notifications.ToastNotification]::new($t))",
        )
    )

    def __init__(self, run: Runner = run_command) -> None:
        self._run = run

    def notify(self, alert: Alert) -> None:
        self._run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT],
            {_TITLE_ENV: alert.title, _BODY_ENV: alert.body},
        )


def default_notifier(
    platform: str = sys.platform, which: Callable[[str], str | None] = shutil.which
) -> Notifier | None:
    """This system's notifier, or None when it has no way to show notifications."""
    if platform == "darwin":
        return MacNotifier() if which("osascript") else None
    if platform == "win32":
        return WindowsNotifier() if which("powershell") else None
    if platform.startswith(("linux", "freebsd", "openbsd")):
        return LinuxNotifier() if which("notify-send") else None
    return None


def _plain(text: str, limit: int) -> str:
    """One line of printable text, so a message cannot fake extra lines or controls."""
    cleaned = "".join(
        " " if unicodedata.category(char).startswith(("C", "Z")) else char for char in text
    )
    return " ".join(cleaned.split())[:limit]


def _escape(text: str) -> str:
    """notify-send bodies may be read as markup, which could hide text or add links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

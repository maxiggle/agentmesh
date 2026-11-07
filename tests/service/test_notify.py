import sys
from collections.abc import Mapping, Sequence

import pytest

from ehor.service.notify import (
    Alert,
    LinuxNotifier,
    MacNotifier,
    NotifyError,
    WindowsNotifier,
    default_notifier,
    run_command,
)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        self.calls.append((list(argv), dict(env)))


def test_alert_shows_one_plain_line() -> None:
    alert = Alert("gef\n", "line one\nline two\u202e\x07 end")
    assert alert.title == "ehor: gef"
    assert alert.body == "line one line two end"


def test_alert_caps_its_text() -> None:
    alert = Alert("g" * 200, "s" * 500)
    assert len(alert.body) == 140
    assert len(alert.title) == len("ehor: ") + 64


def test_mac_passes_text_as_arguments_not_script() -> None:
    run = Recorder()
    MacNotifier(run).notify(Alert("gef", '" & do shell script "rm -rf ~'))
    argv, env = run.calls[0]
    assert argv[0] == "osascript"
    assert argv[-2:] == ["ehor: gef", '" & do shell script "rm -rf ~']
    assert all("rm -rf" not in arg for arg in argv[:-1])
    assert env == {}


def test_linux_escapes_markup_after_the_option_end() -> None:
    run = Recorder()
    LinuxNotifier(run).notify(Alert("gef", '<a href="https://x">click</a> & go'))
    argv, _ = run.calls[0]
    assert argv[:3] == ["notify-send", "--app-name=ehor", "--"]
    assert argv[3:] == ["ehor: gef", '&lt;a href="https://x"&gt;click&lt;/a&gt; &amp; go']


def test_windows_passes_text_in_the_environment() -> None:
    run = Recorder()
    WindowsNotifier(run).notify(Alert("gef", "'; Remove-Item C:\\ -Recurse; '"))
    argv, env = run.calls[0]
    assert argv[0] == "powershell"
    assert all("Remove-Item" not in arg for arg in argv)
    assert env == {
        "EHOR_NOTIFY_TITLE": "ehor: gef",
        "EHOR_NOTIFY_BODY": "'; Remove-Item C:\\ -Recurse; '",
    }


@pytest.mark.parametrize(
    ("platform", "kind"),
    [("darwin", MacNotifier), ("linux", LinuxNotifier), ("win32", WindowsNotifier)],
)
def test_default_notifier_matches_the_platform(platform: str, kind: type) -> None:
    assert isinstance(default_notifier(platform, lambda _: "/bin/tool"), kind)


def test_no_notifier_without_its_command() -> None:
    assert default_notifier("linux", lambda _: None) is None
    assert default_notifier("sunos5", lambda _: "/bin/tool") is None


def test_a_failing_command_raises() -> None:
    with pytest.raises(NotifyError):
        run_command([sys.executable, "-c", "raise SystemExit(1)"], {})
    with pytest.raises(NotifyError):
        run_command(["ehor-no-such-command"], {})


def test_command_sees_the_extra_environment() -> None:
    check = "import os, sys; sys.exit(os.environ['EHOR_NOTIFY_BODY'] != 'hi')"
    run_command([sys.executable, "-c", check], {"EHOR_NOTIFY_BODY": "hi"})

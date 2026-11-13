import argparse
import asyncio
import getpass
import io
import json
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from pathlib import Path

import pytest

from ehor import cli as cli_module
from ehor.cli import main
from ehor.identity import Signer
from ehor.identity.backends import Backend
from ehor.identity.software import SoftwareSigner
from ehor.service import device as device_module
from ehor.service.api import ApiError
from ehor.service.device import Device
from ehor.service.invite import Invite
from ehor.service.notify import Alert, NtfyNotifier
from ehor.service.rpc import RpcError
from security_key_emulator import SoftwareAuthenticator


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    # pytest's own tmp_path on macOS is too long for a service socket path.
    with tempfile.TemporaryDirectory(prefix="ehor") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EHOR_PASSPHRASE", "test passphrase")


@pytest.fixture(autouse=True)
def hardware(monkeypatch: pytest.MonkeyPatch) -> list[Backend]:
    """No hardware by default; tests append a backend to pretend this machine has it."""
    found: list[Backend] = []

    class PretendTpm(SoftwareSigner):
        backend = "tpm"

    def open_signer(backend: Backend, keys: Path, passphrase: bytes, *, tools: Path) -> Signer:
        return (PretendTpm if backend is Backend.TPM else SoftwareSigner)(keys, passphrase)

    def detect_hardware(keys: Path, *, tools: Path) -> Backend | None:
        return found[0] if found else None

    monkeypatch.setattr(device_module, "open_signer", open_signer)
    monkeypatch.setattr(device_module, "detect_hardware", detect_hardware)
    return found


def test_init_creates_a_team(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "gef"
    code = main(
        [
            "--home",
            str(home),
            "init",
            "--handle",
            "gef",
            "--team-name",
            "Acme",
            "--exchange",
            str(tmp_path / "shared"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"created team with gef as admin in {home}")
    assert "signing key  software (passphrase-protected file)" in out
    assert "invite  eh1-" in out
    config = json.loads((home / "config.json").read_text())
    assert config["exchange"] == str((tmp_path / "shared").resolve())


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["init", "--handle", "Gef"], "invalid handle"),
        (["digest"], "not running"),
    ],
)
def test_errors_are_short(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], error: str
) -> None:
    assert main(["--home", str(tmp_path), *args]) == 1
    assert error in capsys.readouterr().err


def test_init_refuses_an_existing_device(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--home", str(tmp_path), "init", "--handle", "gef"])
    assert main(["--home", str(tmp_path), "init", "--handle", "gef"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_serve_needs_a_device(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(tmp_path), "serve"]) == 1
    assert "ehor init" in capsys.readouterr().err


def test_invite_then_join(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gef, tobi = str(tmp_path / "gef"), str(tmp_path / "tobi")
    main(["--home", gef, "init", "--handle", "gef"])
    capsys.readouterr()
    assert main(["--home", gef, "invite"]) == 0
    code = capsys.readouterr().out.split()[0]
    assert code.startswith("eh1-")
    assert main(["--home", tobi, "join", code, "--handle", "tobi"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"joined as tobi in {tobi}\nsigning key  software")
    assert "\nask an admin to run:\n" in out
    assert "  ehor roster add tobi 0" in out
    assert "--hardware" not in out
    config = json.loads((tmp_path / "tobi" / "config.json").read_text())
    invite = Invite.decode(code)
    assert (config["team"], config["genesis"]) == (invite.team.hex(), invite.genesis.hex())


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["join", "nonsense", "--handle", "tobi"], "invite codes start with"),
        (["invite"], "ehor init"),
        (["roster"], "not running"),
        (["roster", "add", "tobi", "00"], "not running"),
        (["roster", "remove", "tobi"], "not running"),
    ],
)
def test_team_command_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], error: str
) -> None:
    assert main(["--home", str(tmp_path), *args]) == 1
    assert error in capsys.readouterr().err


def test_relay_init_from_an_invite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--home", str(tmp_path / "gef"), "init", "--handle", "gef"])
    capsys.readouterr()
    main(["--home", str(tmp_path / "gef"), "invite"])
    code = capsys.readouterr().out.split()[0]
    assert main(["--home", str(tmp_path / "relay"), "relay", "init", "--invite", code]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"created relay in {tmp_path / 'relay'}")
    assert "  ehor roster relay-add <public-host>:7447 0" in out
    assert (tmp_path / "relay" / "relay.json").exists()


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["relay", "init", "--invite", "nonsense"], "invite codes start with"),
        (["relay", "serve"], "relay init"),
        (["relay", "serve", "--listen", "nowhere"], "host:port"),
        (["roster", "relay-add", "relay:1", "00"], "not running"),
        (["roster", "relay-remove", "relay:1"], "not running"),
    ],
)
def test_relay_command_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], error: str
) -> None:
    assert main(["--home", str(tmp_path), *args]) == 1
    assert error in capsys.readouterr().err


def test_invite_can_name_a_relay(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef"])
    capsys.readouterr()
    assert main(["--home", home, "invite", "--relay", "relay.example.com:7447"]) == 0
    code = capsys.readouterr().out.split()[0]
    assert Invite.decode(code).relay == "relay.example.com:7447"
    assert main(["--home", home, "invite", "--relay", "nope"]) == 1
    assert "host:port" in capsys.readouterr().err


def test_a_damaged_iroh_key_is_a_short_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef"])
    capsys.readouterr()
    (tmp_path / "keys" / "iroh.key").write_bytes(b"short")
    assert main(["--home", home, "invite"]) == 1
    assert "delete it to get a new address" in capsys.readouterr().err


def test_invite_carries_the_endpoint_of_this_device(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("iroh")
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef"])
    capsys.readouterr()
    expected = Device.stored_endpoint_id(tmp_path)
    assert expected is not None
    assert main(["--home", home, "invite"]) == 0
    assert Invite.decode(capsys.readouterr().out.split()[0]).iroh == expected
    assert main(["--home", home, "invite", "--relay", "relay.example.com:7447"]) == 0
    invite = Invite.decode(capsys.readouterr().out.split()[0])
    assert (invite.iroh, invite.relay) == (expected, "relay.example.com:7447")


def test_join_remembers_the_endpoint_in_the_invite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("iroh")
    gef, tobi = str(tmp_path / "gef"), str(tmp_path / "tobi")
    main(["--home", gef, "init", "--handle", "gef"])
    main(["--home", gef, "invite"])
    code = capsys.readouterr().out.split("invite  ")[1].split()[0]
    assert main(["--home", tobi, "join", code, "--handle", "tobi"]) == 0
    config = json.loads((tmp_path / "tobi" / "config.json").read_text())
    expected = Device.stored_endpoint_id(tmp_path / "gef")
    assert expected is not None
    assert config["bootstrap_endpoint"] == expected.hex()


def test_auto_key_uses_hardware_when_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], hardware: list[Backend]
) -> None:
    hardware.append(Backend.TPM)
    assert main(["--home", str(tmp_path), "init", "--handle", "gef"]) == 0
    assert (
        "signing key  tpm (hardware, cannot be copied off this machine)" in capsys.readouterr().out
    )
    assert json.loads((tmp_path / "config.json").read_text())["key_backend"] == "tpm"


def test_named_key_backend_skips_detection(tmp_path: Path, hardware: list[Backend]) -> None:
    hardware.append(Backend.TPM)
    assert main(["--home", str(tmp_path), "init", "--handle", "gef", "--key", "software"]) == 0
    assert json.loads((tmp_path / "config.json").read_text())["key_backend"] == "software"


def test_join_with_a_named_key_backend(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gef, tobi = str(tmp_path / "gef"), str(tmp_path / "tobi")
    main(["--home", gef, "init", "--handle", "gef"])
    main(["--home", gef, "invite"])
    code = capsys.readouterr().out.split("invite  ")[1].split()[0]
    assert main(["--home", tobi, "join", code, "--handle", "tobi", "--key", "tpm"]) == 0
    out = capsys.readouterr().out
    assert "signing key  tpm (hardware" in out
    assert out.rstrip().endswith(" --hardware")
    assert json.loads((tmp_path / "tobi" / "config.json").read_text())["key_backend"] == "tpm"


def test_unknown_key_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--home", str(tmp_path), "init", "--handle", "gef", "--key", "yubikey"])


TOBI_KEY = "02" + "11" * 32


@pytest.fixture
def security_key(monkeypatch: pytest.MonkeyPatch) -> SoftwareAuthenticator:
    """Stands in for whichever USB key is plugged in; it holds every credential made."""
    key = SoftwareAuthenticator()
    monkeypatch.setattr(cli_module, "_connect_security_key", lambda: key)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n" * 4))
    return key


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Device]:
    """Runs the admin's service in-process behind the CLI's RPC client."""
    with Device.create_team(tmp_path / "gef", "gef", b"test passphrase", team_name="Acme") as gef:

        class InProcessClient:
            def __init__(self, endpoint: object) -> None:
                self.endpoint = endpoint

            async def call(self, method: str, params: Mapping[str, object] | None = None) -> str:
                try:
                    return gef.call(method, dict(params or {}))
                except ApiError as exc:
                    raise RpcError(str(exc)) from exc

        monkeypatch.setattr(cli_module, "RpcClient", InProcessClient)
        yield gef


def _gef(tmp_path: Path, *args: str) -> int:
    return main(["--home", str(tmp_path / "gef"), *args])


def test_enrolled_security_key_signs_roster_changes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    service: Device,
    security_key: SoftwareAuthenticator,
) -> None:
    assert _gef(tmp_path, "roster", "enroll-key", "--label", "yubikey") == 0
    out, err = capsys.readouterr()
    assert out.startswith("enrolled the security key\nAcme · roster v2")
    assert "security-key" in out
    assert out.endswith("roster changes by you now need a touch of 'yubikey'\n")
    assert "touch the new security key" in err
    assert (tmp_path / "gef" / "keys" / "yubikey.fido2").exists()

    assert _gef(tmp_path, "roster", "add", "tobi", TOBI_KEY) == 0
    out, err = capsys.readouterr()
    assert err == "touch your security key 'yubikey' to sign roster v3\n"
    assert out.startswith("signed with the security key\nAcme · roster v3")
    assert "tobi · channels *" in out
    assert security_key.touches == 1

    assert _gef(tmp_path, "roster", "remove", "tobi") == 0
    assert "tobi" not in capsys.readouterr().out
    assert security_key.touches == 2


def test_a_second_key_is_enrolled_by_touching_the_first(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    service: Device,
    security_key: SoftwareAuthenticator,
) -> None:
    assert _gef(tmp_path, "roster", "enroll-key", "--label", "yubikey") == 0
    assert _gef(tmp_path, "roster", "enroll-key", "--label", "backup") == 0
    out, err = capsys.readouterr()
    assert "plug in only your enrolled security key 'yubikey'" in err
    assert out.count("security-key") >= 2
    assert security_key.touches == 1

    assert _gef(tmp_path, "roster", "relay-add", "relay:1", "03" + "22" * 32) == 1
    assert "pick a security key with --security-key: backup, yubikey" in capsys.readouterr().err
    args = ("roster", "relay-add", "relay:1", "03" + "22" * 32, "--security-key", "backup")
    assert _gef(tmp_path, *args) == 0
    assert "relay relay:1" in capsys.readouterr().out


def test_an_unknown_security_key_label_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    service: Device,
    security_key: SoftwareAuthenticator,
) -> None:
    assert _gef(tmp_path, "roster", "remove", "tobi", "--security-key", "nope") == 1
    assert "no security key named 'nope'; enrolled: none" in capsys.readouterr().err


def test_a_failed_enrollment_forgets_the_credential(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], security_key: SoftwareAuthenticator
) -> None:
    Device.create_team(tmp_path / "gef", "gef", b"test passphrase").close()
    assert _gef(tmp_path, "roster", "enroll-key") == 1
    assert "not running" in capsys.readouterr().err
    assert list((tmp_path / "gef" / "keys").glob("*.fido2")) == []


def test_enrolling_without_the_fido2_extra_says_how_to_get_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "ehor.identity.ctap", None)
    Device.create_team(tmp_path / "gef", "gef", b"test passphrase").close()
    assert _gef(tmp_path, "roster", "enroll-key") == 1
    assert "pip install 'ehor[fido2]'" in capsys.readouterr().err


class Keyboard:
    """Types answers into passphrase prompts and records the prompts shown."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def getpass(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def keyboard(monkeypatch: pytest.MonkeyPatch) -> Keyboard:
    keyboard = Keyboard()
    monkeypatch.delenv("EHOR_PASSPHRASE")
    monkeypatch.setattr(getpass, "getpass", keyboard.getpass)
    return keyboard


def test_init_explains_the_passphrase_before_asking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keyboard: Keyboard
) -> None:
    keyboard.answers += ["correct horse", "correct horse"]
    assert main(["--home", str(tmp_path / "gef"), "init", "--handle", "gef"]) == 0
    assert "encrypts this device's keys" in capsys.readouterr().err
    assert keyboard.prompts == ["Choose a passphrase: ", "Type it again to confirm: "]


def test_init_says_what_to_do_when_passphrases_differ(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keyboard: Keyboard
) -> None:
    keyboard.answers += ["correct horse", "battery staple"]
    assert main(["--home", str(tmp_path / "gef"), "init", "--handle", "gef"]) == 1
    assert "didn't match; run the command again" in capsys.readouterr().err


def test_init_refuses_an_empty_passphrase_without_asking_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keyboard: Keyboard
) -> None:
    keyboard.answers.append("")
    assert main(["--home", str(tmp_path / "gef"), "init", "--handle", "gef"]) == 1
    assert "can't be empty" in capsys.readouterr().err
    assert keyboard.prompts == ["Choose a passphrase: "]


def test_relay_init_explains_its_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keyboard: Keyboard
) -> None:
    keyboard.answers += ["team pass", "team pass", "relay pass", "relay pass"]
    main(["--home", str(tmp_path / "gef"), "init", "--handle", "gef"])
    code = capsys.readouterr().out.split("invite  ")[1].split()[0]
    assert main(["--home", str(tmp_path / "relay"), "relay", "init", "--invite", code]) == 0
    assert "encrypts this relay's signing key" in capsys.readouterr().err


def test_init_checks_the_handle_before_asking_for_a_passphrase(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keyboard: Keyboard
) -> None:
    assert main(["--home", str(tmp_path / "gef"), "init", "--handle", "unpaidCTO"]) == 1
    assert "invalid handle 'unpaidCTO': use up to 32 lowercase" in capsys.readouterr().err
    assert keyboard.prompts == []
    assert not (tmp_path / "gef" / "keys").exists()


def test_init_cleans_up_after_a_setup_that_did_not_finish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "gef"
    SoftwareSigner(home / "keys", b"forgotten").create_key("device")
    assert main(["--home", str(home), "init", "--handle", "gef"]) == 0
    captured = capsys.readouterr()
    assert "Removed files left by an earlier setup" in captured.err
    assert "created team with gef" in captured.out


def test_setup_installs_claude_commands_and_says_what_is_next(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "claude", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"installed /claim, /done, /handoff, /inbox, /reply, /team in {tmp_path}")
    assert "claude mcp add --scope user ehor -- ehor mcp" in out
    assert (tmp_path / "inbox.md").is_file()


def test_setup_points_the_agent_at_a_chosen_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "joel"
    assert main(["--home", str(home), "setup", "codex", "--dir", str(tmp_path / "skills")]) == 0
    assert f"codex mcp add ehor -- ehor --home {home} mcp" in capsys.readouterr().out


def test_setup_installs_opencode_commands_and_says_how_to_connect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "opencode", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"installed /claim, /done, /handoff, /inbox, /reply, /team in {tmp_path}")
    assert "`opencode mcp add`" in out
    assert "runs `ehor mcp`" in out
    assert "ehor setup opencode --instructions >> AGENTS.md" in out
    assert (tmp_path / "inbox.md").is_file()


def test_setup_refuses_to_replace_an_edited_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "inbox.md").write_text("my own version")
    assert main(["setup", "claude", "--dir", str(tmp_path)]) == 1
    assert "rerun with --force" in capsys.readouterr().err


def test_setup_prints_instructions_without_installing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert main(["setup", "claude", "--instructions"]) == 0
    assert capsys.readouterr().out.startswith("## Team messages (ehor)")
    assert not (tmp_path / ".claude").exists()


class _Shown:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _set_notifier(monkeypatch: pytest.MonkeyPatch, notifier: _Shown | None) -> None:
    monkeypatch.setattr(cli_module, "default_notifier", lambda: notifier)


def test_notify_turns_notifications_off_and_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_notifier(monkeypatch, _Shown())
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef", "--key", "software"])
    capsys.readouterr()
    assert main(["--home", home, "notify"]) == 0
    assert capsys.readouterr().out == "desktop notifications: on\nphone notifications: off\n"
    assert main(["--home", home, "notify", "off"]) == 0
    assert capsys.readouterr().out.startswith("desktop notifications: off\n")
    assert json.loads((tmp_path / "config.json").read_text())["notifications"] is False
    main(["--home", home, "notify", "on"])
    assert json.loads((tmp_path / "config.json").read_text())["notifications"] is True


def test_notify_says_when_the_system_cannot_show_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_notifier(monkeypatch, None)
    main(["--home", str(tmp_path), "init", "--handle", "gef", "--key", "software"])
    capsys.readouterr()
    main(["--home", str(tmp_path), "notify"])
    assert "install notify-send" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "notify", "test"]) == 1
    assert "install notify-send" in capsys.readouterr().err


def test_notify_test_shows_a_notification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _Shown()
    _set_notifier(monkeypatch, shown)
    main(["--home", str(tmp_path), "init", "--handle", "gef", "--key", "software"])
    assert main(["--home", str(tmp_path), "notify", "test"]) == 0
    assert [alert.title for alert in shown.alerts] == ["ehor: test"]


def test_notify_needs_a_device(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(tmp_path), "notify", "off"]) == 1
    assert "ehor init" in capsys.readouterr().err


def _config(home: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((home / "config.json").read_text())
    return data


def test_notify_phone_makes_a_private_topic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_notifier(monkeypatch, _Shown())
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef", "--key", "software"])
    capsys.readouterr()
    assert main(["--home", home, "notify", "phone"]) == 0
    url = _config(tmp_path)["phone"]
    assert isinstance(url, str)
    assert url.startswith("https://ntfy.sh/ehor-")
    out = capsys.readouterr().out
    assert f"phone notifications: sent to {url}" in out
    assert "ntfy app" in out
    main(["--home", home, "notify", "phone"])
    assert _config(tmp_path)["phone"] == url
    main(["--home", home, "notify", "phone", "off"])
    assert _config(tmp_path)["phone"] is None


def test_notify_phone_takes_a_self_hosted_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef", "--key", "software"])
    assert main(["--home", home, "notify", "phone", "https://ntfy.example.com/team-x"]) == 0
    assert _config(tmp_path)["phone"] == "https://ntfy.example.com/team-x"
    assert main(["--home", home, "notify", "phone", "http://ntfy.example.com/team-x"]) == 1
    assert "https" in capsys.readouterr().err
    assert main(["--home", home, "notify", "on", "https://ntfy.example.com/y"]) == 1


def test_notify_test_reaches_the_phone_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_notifier(monkeypatch, None)
    sent: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        cli_module,
        "NtfyNotifier",
        lambda target: NtfyNotifier(target, lambda u, b: sent.append((u, b))),
    )
    home = str(tmp_path)
    main(["--home", home, "init", "--handle", "gef", "--key", "software"])
    main(["--home", home, "notify", "phone", "https://ntfy.example.com/team-x"])
    assert main(["--home", home, "notify", "test"]) == 0
    assert [url for url, _ in sent] == ["https://ntfy.example.com"]
    assert json.loads(sent[0][1])["message"] == "notifications work"


class _PluginEntry:
    def __init__(self, name: str, register: Callable[[cli_module.Commands], None]) -> None:
        self.name = name
        self._register = register

    def load(self) -> Callable[[cli_module.Commands], None]:
        return self._register


def _hello_plugin(commands: cli_module.Commands) -> None:
    def run(args: argparse.Namespace) -> int:
        if args.fail:
            raise cli_module.CliError("plugin failed")
        sys.stdout.write(f"hello from {cli_module.device_home(args)}\n")
        return 0

    hello = commands.add_parser("hello", help="say hello")
    hello.add_argument("--fail", action="store_true")
    hello.set_defaults(run=run)


@pytest.fixture
def plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    def installed(*, group: str) -> list[_PluginEntry]:
        assert group == cli_module.COMMANDS_GROUP
        return [_PluginEntry("hello", _hello_plugin)]

    monkeypatch.setattr(cli_module, "entry_points", installed)


@pytest.mark.usefixtures("plugins")
def test_installed_plugins_add_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(tmp_path), "hello"]) == 0
    assert capsys.readouterr().out == f"hello from {tmp_path}\n"


@pytest.mark.usefixtures("plugins")
def test_a_plugin_command_reports_its_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hello", "--fail"]) == 1
    assert capsys.readouterr().err == "error: plugin failed\n"


def test_device_passphrase_reads_the_environment() -> None:
    assert cli_module.device_passphrase() == b"test passphrase"


class _EchoTools:
    def __init__(self, name: str) -> None:
        self._name = name

    def add_tools(self, server: object, relay: object) -> None:
        pass

    def open(
        self, home: Path, passphrase: bytes, messenger: object
    ) -> dict[str, Callable[[Mapping[str, object]], Awaitable[str]]]:
        async def handler(params: Mapping[str, object]) -> str:
            return f"{self._name} {home.name} {passphrase.decode()}"

        return {"demo.echo": handler}


class _ToolEntry:
    def __init__(self, name: str) -> None:
        self.name = name

    def load(self) -> _EchoTools:
        return _EchoTools(self.name)


def _installed_tools(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    def installed(*, group: str) -> list[_ToolEntry]:
        assert group == cli_module.TOOLS_GROUP
        return [_ToolEntry(name) for name in names]

    monkeypatch.setattr(cli_module, "entry_points", installed)


def test_tool_plugins_serve_their_methods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed_tools(monkeypatch, ["one"])
    with Device.create_team(tmp_path / "gef", "gef", b"pw") as gef:
        methods = cli_module._plugin_methods(tmp_path / "gef", b"pw", gef)

        async def call() -> str:
            return await methods["demo.echo"]({})

        assert asyncio.run(call()) == "one gef pw"


def test_two_tool_plugins_cannot_serve_one_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed_tools(monkeypatch, ["one", "two"])
    with (
        Device.create_team(tmp_path, "gef", b"pw") as gef,
        pytest.raises(cli_module.CliError, match=r"demo\.echo"),
    ):
        cli_module._plugin_methods(tmp_path, b"pw", gef)


def test_serve_with_the_wrong_passphrase_says_so_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "gef"
    assert main(["--home", str(home), "init", "--handle", "gef"]) == 0
    capsys.readouterr()
    unlocked = device_module._signer(home, Backend.SOFTWARE, b"test passphrase")
    monkeypatch.setattr(device_module, "_signer", lambda *args: unlocked)
    monkeypatch.setenv("EHOR_PASSPHRASE", "not the passphrase")
    assert main(["--home", str(home), "serve"]) == 1
    assert capsys.readouterr().err.endswith(
        "error: cannot decrypt the encryption key; check the device passphrase\n"
    )
    (home / "ehor.db").rename(home / "moved.db")

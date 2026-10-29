import io
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agentmesh import cli as cli_module
from agentmesh.cli import main
from agentmesh.identity import Signer
from agentmesh.identity.backends import Backend
from agentmesh.identity.software import SoftwareSigner
from agentmesh.service import device as device_module
from agentmesh.service.api import ApiError
from agentmesh.service.device import Device
from agentmesh.service.invite import Invite
from agentmesh.service.rpc import RpcError
from security_key_emulator import SoftwareAuthenticator


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTMESH_PASSPHRASE", "test passphrase")


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
    assert "invite  am1-" in out
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
    assert "agentmesh init" in capsys.readouterr().err


def test_invite_then_join(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gef, tobi = str(tmp_path / "gef"), str(tmp_path / "tobi")
    main(["--home", gef, "init", "--handle", "gef"])
    capsys.readouterr()
    assert main(["--home", gef, "invite"]) == 0
    code = capsys.readouterr().out.split()[0]
    assert code.startswith("am1-")
    assert main(["--home", tobi, "join", code, "--handle", "tobi"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"joined as tobi in {tobi}\nsigning key  software")
    assert "\nask an admin to run:\n" in out
    assert "  agentmesh roster add tobi 0" in out
    assert "--hardware" not in out
    config = json.loads((tmp_path / "tobi" / "config.json").read_text())
    invite = Invite.decode(code)
    assert (config["team"], config["genesis"]) == (invite.team.hex(), invite.genesis.hex())


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["join", "nonsense", "--handle", "tobi"], "invite codes start with"),
        (["invite"], "agentmesh init"),
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
    assert "  agentmesh roster relay-add <public-host>:7447 0" in out
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
    monkeypatch.setitem(sys.modules, "agentmesh.identity.ctap", None)
    Device.create_team(tmp_path / "gef", "gef", b"test passphrase").close()
    assert _gef(tmp_path, "roster", "enroll-key") == 1
    assert "pip install 'agentmesh[fido2]'" in capsys.readouterr().err

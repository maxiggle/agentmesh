import asyncio
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentmesh.identity import Signer
from agentmesh.identity.backends import Backend
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.roster import Trust
from agentmesh.service import device as device_module
from agentmesh.service.api import ApiError
from agentmesh.service.config import ConfigError, DeviceConfig, default_home
from agentmesh.service.daemon import Daemon
from agentmesh.service.device import Device
from agentmesh.service.invite import Invite
from agentmesh.service.rpc import RpcClient, TcpEndpoint

PASSPHRASE = b"test passphrase"
TEAM = b"\x09" * 16
GENESIS = b"\x07" * 32


@pytest.fixture
def short_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="am") as directory:
        yield Path(directory)


def _post(device: Device, to: str, summary: str, kind: str = "fyi") -> str:
    return device.call("post", {"to": [to], "type": kind, "summary": summary})


def _add(by: Device, member: Device, **extra: object) -> str:
    params = {"handle": member.config.handle, "key": member.author.hex(), **extra}
    return by.call("roster.add", params)


def test_config_round_trip(tmp_path: Path) -> None:
    config = DeviceConfig("gef", TEAM, GENESIS, exchange=tmp_path / "x")
    config.save(tmp_path)
    assert DeviceConfig.load(tmp_path) == config


def test_config_keeps_the_key_backend(tmp_path: Path) -> None:
    config = DeviceConfig("gef", TEAM, GENESIS, key_backend=Backend.TPM)
    config.save(tmp_path)
    assert DeviceConfig.load(tmp_path).key_backend is Backend.TPM


def test_config_without_a_key_backend_uses_software(tmp_path: Path) -> None:
    DeviceConfig("gef", TEAM, GENESIS).save(tmp_path)
    data = json.loads((tmp_path / "config.json").read_text())
    del data["key_backend"]
    (tmp_path / "config.json").write_text(json.dumps(data))
    assert DeviceConfig.load(tmp_path).key_backend is Backend.SOFTWARE


def test_config_rejects_an_unknown_key_backend(tmp_path: Path) -> None:
    DeviceConfig("gef", TEAM, GENESIS).save(tmp_path)
    data = json.loads((tmp_path / "config.json").read_text())
    data["key_backend"] = "yubikey"
    (tmp_path / "config.json").write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="yubikey"):
        DeviceConfig.load(tmp_path)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"handle": "Gef"}, "handle"),
        ({"team": b"\x00"}, "team id"),
        ({"genesis": b"\x00"}, "genesis"),
    ],
)
def test_config_rejects_bad_values(kwargs: dict[str, object], error: str) -> None:
    fields: dict[str, object] = {"handle": "gef", "team": TEAM, "genesis": GENESIS}
    fields.update(kwargs)
    with pytest.raises(ConfigError, match=error):
        DeviceConfig(**fields)  # type: ignore[arg-type]  # built from a dict on purpose


def test_load_reports_missing_and_broken_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="agentmesh init"):
        DeviceConfig.load(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"handle": "gef"}))
    with pytest.raises(ConfigError, match="invalid"):
        DeviceConfig.load(tmp_path)


def test_home_can_be_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTMESH_HOME", str(tmp_path))
    assert default_home() == tmp_path
    monkeypatch.delenv("AGENTMESH_HOME")
    assert default_home().name == ".agentmesh"


def test_create_team_then_reopen(tmp_path: Path) -> None:
    with Device.create_team(tmp_path, "gef", PASSPHRASE, team_name="Acme") as device:
        invite = device.invite
        assert device.call("roster.show", {}).startswith("Acme · roster v1\ngef (admin)")
    with Device.open(tmp_path, PASSPHRASE) as device:
        assert device.invite == invite
        assert device.roster.current is not None
        assert _post(device, "#backend", "hi").startswith("posted")


class PretendTpm(SoftwareSigner):
    backend = "tpm"


@pytest.fixture
def pretend_tpm(monkeypatch: pytest.MonkeyPatch) -> list[Backend]:
    opened: list[Backend] = []

    def open_signer(backend: Backend, keys: Path, passphrase: bytes, *, tools: Path) -> Signer:
        opened.append(backend)
        kind = PretendTpm if backend is Backend.TPM else SoftwareSigner
        return kind(keys, passphrase)

    monkeypatch.setattr(device_module, "open_signer", open_signer)
    return opened


def test_hardware_founder_is_marked_hardware_in_genesis(
    tmp_path: Path, pretend_tpm: list[Backend]
) -> None:
    with Device.create_team(tmp_path, "gef", PASSPHRASE, key_backend=Backend.TPM) as device:
        assert device.config.key_backend is Backend.TPM
        roster = device.roster.current
        assert roster is not None
        assert [d.trust for d in roster.members[0].devices] == [Trust.HARDWARE]
    with Device.open(tmp_path, PASSPHRASE) as device:
        assert _post(device, "#backend", "hi").startswith("posted")
    assert set(pretend_tpm) == {Backend.TPM}


@pytest.mark.parametrize(
    ("found", "expected"), [(Backend.TPM, Backend.TPM), (None, Backend.SOFTWARE)]
)
def test_setup_can_detect_hardware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pretend_tpm: list[Backend],
    found: Backend | None,
    expected: Backend,
) -> None:
    probed: list[Path] = []

    def detect_hardware(keys: Path, *, tools: Path) -> Backend | None:
        probed.append(keys)
        return found

    monkeypatch.setattr(device_module, "detect_hardware", detect_hardware)
    with Device.create_team(tmp_path, "gef", PASSPHRASE, key_backend=None) as device:
        assert device.config.key_backend is expected
    assert probed == [tmp_path / "keys"]


def test_joining_device_keeps_its_backend(tmp_path: Path, pretend_tpm: list[Backend]) -> None:
    with Device.create_team(tmp_path / "gef", "gef", PASSPHRASE) as gef:
        invite = gef.invite
        roster = gef.roster.current
        assert roster is not None
        assert [d.trust for d in roster.members[0].devices] == [Trust.SOFTWARE]
    with Device.join(tmp_path / "tobi", invite, "tobi", PASSPHRASE, key_backend=Backend.TPM):
        pass
    with Device.open(tmp_path / "tobi", PASSPHRASE) as tobi:
        assert tobi.config.key_backend is Backend.TPM
    assert pretend_tpm[-1] is Backend.TPM


def test_setup_refuses_an_existing_device(tmp_path: Path) -> None:
    Device.create_team(tmp_path, "gef", PASSPHRASE).close()
    with pytest.raises(ConfigError, match="already exists"):
        Device.create_team(tmp_path, "gef", PASSPHRASE)


def test_joining_member_waits_for_an_admin(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared) as gef,
        Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared) as tobi,
    ):
        gef.sync()
        with pytest.raises(ApiError, match="cannot post"):
            _post(tobi, "gef", "let me in")
        assert "roster v1" in tobi.call("roster.show", {})
        _add(gef, tobi, roles=["@mobile"])
        assert _post(tobi, "gef", "thanks").startswith("posted")
        inbox = gef.call("inbox", {})
        assert '· tobi · fyi · "thanks"' in inbox
        assert tobi.call("team", {}).startswith("me tobi · roles @mobile · channels *")


def test_only_admins_change_the_roster(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared) as gef,
        Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared) as tobi,
        Device.join(tmp_path / "lee", gef.invite, "lee", PASSPHRASE, exchange=shared) as lee,
    ):
        gef.sync()
        _add(gef, tobi)
        tobi.sync()
        with pytest.raises(ApiError, match="only admins"):
            _add(tobi, lee)
        _add(gef, lee, admin=True)
        lee.sync()
        assert lee.call("roster.remove", {"handle": "tobi"}).startswith("removed tobi")


def test_a_rival_team_with_the_same_id_is_ignored(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared) as gef,
        Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared) as tobi,
    ):
        gef.sync()
        rival_config = DeviceConfig("mallory", gef.config.team, b"\x01" * 32, exchange=shared)
        rival_home = tmp_path / "mallory"
        Device.create_team(rival_home, "mallory", PASSPHRASE).close()
        rival_config.save(rival_home)
        with Device.open(rival_home, PASSPHRASE) as mallory:
            mallory.sync()
        tobi.sync()
        assert tobi.roster.current is not None
        assert [m.handle for m in tobi.roster.current.members] == ["gef"]


def test_removed_members_are_held_back(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared) as gef,
        Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared) as tobi,
    ):
        gef.sync()
        _add(gef, tobi)
        tobi.sync()
        gef.call("roster.remove", {"handle": "tobi"})
        tobi.sync()
        with pytest.raises(ApiError, match="cannot post"):
            _post(tobi, "gef", "still here?")
        with pytest.raises(ApiError, match="last admin"):
            gef.call("roster.remove", {"handle": "gef"})


async def _daemons_talk(root: Path) -> tuple[str, str]:
    shared = root / "shared"
    gef = Device.create_team(root / "gef", "gef", PASSPHRASE, exchange=shared)
    tobi = Device.join(root / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared)
    _add(gef, tobi)
    tobi.sync()
    gef.sync()
    gef_endpoint = TcpEndpoint(root / "gef.json")
    tobi_endpoint = TcpEndpoint(root / "tobi.json")
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(Daemon(gef, gef_endpoint, interval=0.05).run(stop)),
        asyncio.create_task(Daemon(tobi, tobi_endpoint, interval=0.05).run(stop)),
    ]
    while not (root / "gef.json").exists() or not (root / "tobi.json").exists():
        await asyncio.sleep(0.01)
    posted = await RpcClient(gef_endpoint).call(
        "post", {"to": ["tobi"], "type": "shipped", "summary": "Sync queue done"}
    )
    digest = await RpcClient(tobi_endpoint).call("digest")
    stop.set()
    await asyncio.gather(*tasks)
    gef.close()
    tobi.close()
    return posted, digest


def test_daemons_serve_tool_calls(short_dir: Path) -> None:
    posted, digest = asyncio.run(_daemons_talk(short_dir))
    assert posted.startswith("posted ")
    assert digest == "1 new · 1 shipped"


def test_joining_remembers_the_invite_relay_until_the_roster_names_one(tmp_path: Path) -> None:
    with Device.create_team(tmp_path / "gef", "gef", PASSPHRASE) as gef:
        invite = Invite(gef.invite.team, gef.invite.genesis, "bootstrap.example.com:7447")
    with Device.join(tmp_path / "tobi", invite, "tobi", PASSPHRASE) as tobi:
        assert tobi.config.relay == "bootstrap.example.com:7447"
        assert tobi.relay_addresses() == ["bootstrap.example.com:7447"]
    assert DeviceConfig.load(tmp_path / "tobi").relay == "bootstrap.example.com:7447"
    with pytest.raises(ConfigError, match="host:port"):
        DeviceConfig("gef", TEAM, GENESIS, relay="nope")


def test_invite_method_includes_the_roster_relay(tmp_path: Path) -> None:
    with Device.create_team(tmp_path, "gef", PASSPHRASE) as gef:
        relay_key = "03" + "44" * 32
        gef.call("roster.relay_add", {"address": "relay.example.com:7447", "key": relay_key})
        assert Invite.decode(gef.call("invite", {})).relay == "relay.example.com:7447"
        with pytest.raises(ApiError, match="unknown params"):
            gef.call("invite", {"x": 1})

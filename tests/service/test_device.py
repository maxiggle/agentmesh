import asyncio
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentmesh.service.config import ConfigError, DeviceConfig, default_home
from agentmesh.service.daemon import Daemon
from agentmesh.service.device import Device
from agentmesh.service.rpc import RpcClient, TcpEndpoint

TEAM = b"\x09" * 16
PASSPHRASE = b"test passphrase"


@pytest.fixture
def short_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="am") as directory:
        yield Path(directory)


def _config(handle: str, exchange: Path | None = None) -> DeviceConfig:
    return DeviceConfig(handle=handle, team=TEAM, exchange=exchange)


def test_config_round_trip(tmp_path: Path) -> None:
    config = DeviceConfig(
        "gef", TEAM, roles=("@backend",), channels=("#backend",), exchange=tmp_path / "x"
    )
    config.save(tmp_path)
    assert DeviceConfig.load(tmp_path) == config


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"handle": "Gef"}, "handle"),
        ({"team": b"\x00"}, "team id"),
        ({"roles": ("backend",)}, "role"),
        ({"channels": ("backend",)}, "channel"),
    ],
)
def test_config_rejects_bad_values(kwargs: dict[str, object], error: str) -> None:
    fields: dict[str, object] = {"handle": "gef", "team": TEAM}
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


def test_create_then_open(tmp_path: Path) -> None:
    home = tmp_path / "gef"
    with Device.create(home, _config("gef"), PASSPHRASE) as device:
        author = device.author
        assert device.call("post", {"to": ["tobi"], "type": "fyi", "summary": "hi"}).startswith(
            "posted"
        )
    with Device.open(home, PASSPHRASE) as device:
        assert device.author == author
        assert device.config.handle == "gef"
        assert device.call("team", {}).startswith("me gef")
        assert device.call("post", {"to": ["tobi"], "type": "fyi", "summary": "again"}).startswith(
            "posted"
        )


def test_create_refuses_to_overwrite(tmp_path: Path) -> None:
    Device.create(tmp_path, _config("gef"), PASSPHRASE).close()
    with pytest.raises(ConfigError, match="already exists"):
        Device.create(tmp_path, _config("gef"), PASSPHRASE)


def test_devices_message_each_other_through_a_folder(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create(tmp_path / "gef", _config("gef", shared), PASSPHRASE) as gef,
        Device.create(tmp_path / "tobi", _config("tobi", shared), PASSPHRASE) as tobi,
    ):
        gef.call("post", {"to": ["tobi"], "type": "question", "summary": "Sync done?"})
        inbox = tobi.call("inbox", {})
        assert '· question · "Sync done?"' in inbox
        message_id = inbox.splitlines()[1].split()[0]
        tobi.call("reply", {"id": message_id, "summary": "Yes"})
        assert f'"Yes" · re {message_id}' in gef.call("inbox", {})


async def _daemons_talk(root: Path) -> tuple[str, str]:
    shared = root / "shared"
    gef = Device.create(root / "gef", _config("gef", shared), PASSPHRASE)
    tobi = Device.create(root / "tobi", _config("tobi", shared), PASSPHRASE)
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

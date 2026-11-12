import asyncio
import tempfile
from collections.abc import Awaitable, Iterator, Mapping
from pathlib import Path

import pytest

from ehor.mcp.server import build_server
from ehor.service.api import ApiError
from ehor.service.device import Device, key_dir
from ehor_near.bounties import BountyError
from ehor_near.config import NearConfig
from ehor_near.limited_key import LIMITED_KEY_FILE, LimitedKeyFile
from ehor_near.tools import CLAIM, DONE, LIST, POST, NearTools

PASSPHRASE = b"test passphrase"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(self, method: str, params: Mapping[str, object] | None = None) -> str:
        self.calls.append((method, dict(params or {})))
        return "ok"


@pytest.fixture
def home() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ehor") as directory:
        path = Path(directory) / "gef"
        Device.create_team(path, "gef", PASSPHRASE).close()
        yield path


def test_agents_get_four_bounty_tools_and_none_that_pays() -> None:
    tools = asyncio.run(build_server(FakeClient(), [NearTools()]).list_tools())
    bounty_tools = {tool.name: tool for tool in tools if "bount" in tool.name}
    assert sorted(bounty_tools) == ["bounties", "bounty_claim", "bounty_done", "bounty_post"]
    assert not [tool.name for tool in tools if "pay" in tool.name or "release" in tool.name]
    for name in ("bounty_post", "bounty_claim", "bounty_done"):
        assert "approve" in (bounty_tools[name].description or "")
    assert all(len(tool.description or "") <= 160 for tool in bounty_tools.values())


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        (
            "bounty_post",
            {"id": "a1b2", "near": "0.1"},
            (POST, {"id": "a1b2", "near": "0.1", "hours": 24}),
        ),
        ("bounty_claim", {"id": "a1b2", "bounty": 4}, (CLAIM, {"id": "a1b2", "bounty": 4})),
        ("bounty_done", {"id": "a1b2", "bounty": 4}, (DONE, {"id": "a1b2", "bounty": 4})),
        ("bounties", {}, (LIST, {})),
    ],
)
def test_tools_relay_to_ehor_serve(
    tool: str, arguments: dict[str, object], expected: tuple[str, dict[str, object]]
) -> None:
    client = FakeClient()
    asyncio.run(build_server(client, [NearTools()]).call_tool(tool, arguments))
    assert client.calls == [expected]


def _run(call: Awaitable[str]) -> str:
    async def wait() -> str:
        return await call

    return asyncio.run(wait())


def test_without_setup_every_tool_says_how_to_set_up(home: Path) -> None:
    with Device.open(home, PASSPHRASE) as device:
        handlers = NearTools().open(home, PASSPHRASE, device)
        assert set(handlers) == {POST, CLAIM, DONE, LIST}
        with pytest.raises(BountyError, match="ehor near setup"):
            _run(handlers[LIST]({}))


def test_handlers_check_their_params(home: Path) -> None:
    NearConfig("gef.testnet", "ehor-escrow.testnet").save(home)
    LimitedKeyFile(key_dir(home) / LIMITED_KEY_FILE, PASSPHRASE).create()
    with Device.open(home, PASSPHRASE) as device:
        handlers = NearTools().open(home, PASSPHRASE, device)
        with pytest.raises(ApiError, match="unknown params"):
            _run(handlers[LIST]({"extra": 1}))
        with pytest.raises(ApiError, match="id must be a string"):
            _run(handlers[POST]({"near": "0.1"}))
        with pytest.raises(BountyError, match="bounty must be a bounty number"):
            _run(handlers[CLAIM]({"id": "a1b2", "bounty": 0}))
        with pytest.raises(BountyError, match="no message"):
            _run(handlers[POST]({"id": "ffffffff", "near": "0.1"}))

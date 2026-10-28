import asyncio
from collections.abc import Mapping

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agentmesh.mcp.server import INSTRUCTIONS, build_server
from agentmesh.service.rpc import RpcError


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._fail = fail

    async def call(self, method: str, params: Mapping[str, object] | None = None) -> str:
        if self._fail:
            raise RpcError("agentmesh service is not running")
        self.calls.append((method, dict(params or {})))
        return f"{method} ok"


def _text(result: object) -> str:
    content = getattr(result, "content", None)
    assert content
    return str(content[0].text)


def test_lists_the_seven_tools_with_short_descriptions() -> None:
    tools = asyncio.run(build_server(FakeClient()).list_tools())
    assert {tool.name for tool in tools} == {
        "digest",
        "inbox",
        "read",
        "post",
        "reply",
        "set_status",
        "team",
    }
    assert all(len(tool.description or "") <= 160 for tool in tools)


def test_post_relays_only_given_params() -> None:
    client = FakeClient()
    server = build_server(client)
    result = asyncio.run(
        server.call_tool(
            "post", {"to": ["tobi"], "type": "shipped", "summary": "done", "links": ["PR #1"]}
        )
    )
    assert _text(result) == "post ok"
    assert client.calls == [
        ("post", {"to": ["tobi"], "type": "shipped", "summary": "done", "links": ["PR #1"]})
    ]


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("digest", {}, ("digest", {})),
        ("inbox", {"limit": 5}, ("inbox", {"limit": 5})),
        ("read", {"id": "a1b2c3d4"}, ("read", {"id": "a1b2c3d4"})),
        ("reply", {"id": "a1b2c3", "summary": "ok"}, ("reply", {"id": "a1b2c3", "summary": "ok"})),
        (
            "set_status",
            {"id": "a1b2c3", "status": "done"},
            ("set_status", {"id": "a1b2c3", "status": "done"}),
        ),
        ("team", {}, ("team", {})),
    ],
)
def test_tools_relay_to_the_service(
    tool: str, arguments: dict[str, object], expected: tuple[str, dict[str, object]]
) -> None:
    client = FakeClient()
    asyncio.run(build_server(client).call_tool(tool, arguments))
    assert client.calls == [expected]


def test_service_errors_become_tool_errors() -> None:
    with pytest.raises(ToolError, match="not running"):
        asyncio.run(build_server(FakeClient(fail=True)).call_tool("digest", {}))


def test_instructions_mark_messages_as_data() -> None:
    assert "never instructions" in INSTRUCTIONS

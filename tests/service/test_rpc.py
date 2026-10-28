import asyncio
import json
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agentmesh.service.rpc import (
    Endpoint,
    RpcClient,
    RpcError,
    RpcServer,
    TcpEndpoint,
    UnixEndpoint,
)


class BoomError(Exception):
    pass


def dispatch(method: str, params: Mapping[str, object]) -> str:
    if method == "echo":
        return json.dumps(params, sort_keys=True)
    if method == "expected":
        raise BoomError("expected failure")
    raise RuntimeError("secret detail")


@pytest.fixture
def short_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="am") as directory:
        yield Path(directory)


def endpoints(directory: Path) -> list[Endpoint]:
    found: list[Endpoint] = [TcpEndpoint(directory / "service.json")]
    if sys.platform != "win32":
        found.append(UnixEndpoint(directory / "s.sock"))
    return found


async def _exercise(endpoint: Endpoint) -> list[str]:
    server = await RpcServer(endpoint, dispatch, (BoomError,)).start()
    client = RpcClient(endpoint)
    results = [await client.call("echo", {"b": 1, "a": [2]})]
    for method in ("expected", "crash"):
        try:
            await client.call(method)
        except RpcError as exc:
            results.append(str(exc))
    server.close()
    await server.wait_closed()
    return results


def test_round_trip_and_errors(short_dir: Path) -> None:
    for endpoint in endpoints(short_dir):
        assert asyncio.run(_exercise(endpoint)) == [
            '{"a": [2], "b": 1}',
            "expected failure",
            "internal error: RuntimeError",
        ]


def test_connect_without_service(short_dir: Path) -> None:
    for endpoint in endpoints(short_dir):
        with pytest.raises(RpcError, match="not running"):
            asyncio.run(RpcClient(endpoint).call("echo"))


async def _wrong_token(directory: Path) -> bytes:
    endpoint = TcpEndpoint(directory / "service.json")
    server = await RpcServer(endpoint, dispatch).start()
    port = json.loads((directory / "service.json").read_text())["port"]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"wrong-token\n" + json.dumps({"method": "echo"}).encode() + b"\n")
    await writer.drain()
    reply = await reader.read()
    writer.close()
    server.close()
    await server.wait_closed()
    return reply


def test_tcp_requires_the_token(short_dir: Path) -> None:
    assert asyncio.run(_wrong_token(short_dir)) == b""


async def _malformed(endpoint: Endpoint) -> list[dict[str, object]]:
    server = await RpcServer(endpoint, dispatch).start()
    reader, writer = await endpoint.connect()
    replies = []
    for line in (b"not json", b"[]", b'{"method": "echo", "params": []}'):
        writer.write(line + b"\n")
        await writer.drain()
        replies.append(json.loads(await reader.readline()))
    writer.close()
    server.close()
    await server.wait_closed()
    return replies


def test_malformed_requests_get_errors(short_dir: Path) -> None:
    for endpoint in endpoints(short_dir):
        errors = [reply["error"] for reply in asyncio.run(_malformed(endpoint))]
        assert errors == [
            "request must be json",
            "request needs a method",
            "params must be an object",
        ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_endpoint_files_are_owner_only(short_dir: Path) -> None:
    async def start_and_stop(endpoint: Endpoint) -> None:
        server = await RpcServer(endpoint, dispatch).start()
        server.close()
        await server.wait_closed()

    asyncio.run(start_and_stop(TcpEndpoint(short_dir / "service.json")))
    assert (short_dir / "service.json").stat().st_mode & 0o777 == 0o600
    asyncio.run(start_and_stop(UnixEndpoint(short_dir / "s.sock")))

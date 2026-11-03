import asyncio
import builtins
import importlib
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest

from agentmesh.transport import iroh as iroh_transport
from agentmesh.transport.iroh import (
    IrohMode,
    IrohReader,
    IrohTransport,
    IrohTransportError,
    IrohUnavailableError,
    IrohWriter,
)

T = TypeVar("T")
TIMEOUT = 30.0


def _run(scenario: Callable[[], Awaitable[T]]) -> T:
    async def bounded() -> T:
        return await asyncio.wait_for(scenario(), TIMEOUT)

    return asyncio.run(bounded())


def _transport(seed: bytes | None = None) -> IrohTransport:
    return IrohTransport(seed or os.urandom(32), IrohMode.LOCAL)


async def _pair() -> tuple[
    IrohTransport,
    IrohTransport,
    tuple[IrohReader, IrohWriter],
    tuple[bytes, IrohReader, IrohWriter],
]:
    server = _transport()
    client = _transport()
    await server.start()
    await client.start()
    accepted = asyncio.ensure_future(server.accept())
    dialed = await client.dial(server.endpoint_id, server.local_addresses())
    result = await accepted
    assert result is not None
    return server, client, dialed, result


def test_round_trip_in_both_directions() -> None:
    async def scenario() -> None:
        server, client, (c_reader, c_writer), (_, s_reader, s_writer) = await _pair()
        c_writer.write(b"hel")
        c_writer.write(b"lo")
        await c_writer.drain()
        assert await s_reader.readexactly(5) == b"hello"
        s_writer.write(b"reply-one")
        await s_writer.drain()
        assert await c_reader.readexactly(9) == b"reply-one"
        s_writer.write(b"tail")
        s_writer.close()
        assert await c_reader.read() == b"tail"
        c_writer.close()
        await asyncio.gather(c_writer.wait_closed(), s_writer.wait_closed())
        await client.close()
        await server.close()

    _run(scenario)


def test_data_written_just_before_close_is_delivered() -> None:
    payload = os.urandom(300_000)

    async def scenario() -> None:
        server, client, (_, c_writer), (_, s_reader, s_writer) = await _pair()
        c_writer.write(payload)
        c_writer.close()
        assert await s_reader.read() == payload
        s_writer.close()
        await asyncio.gather(c_writer.wait_closed(), s_writer.wait_closed())
        await client.close()
        await server.close()

    _run(scenario)


def test_accept_reports_the_dialers_endpoint_id() -> None:
    async def scenario() -> None:
        server, client, _, (remote, _, _) = await _pair()
        assert remote == client.endpoint_id
        assert remote != server.endpoint_id
        await client.close()
        await server.close()

    _run(scenario)


def test_end_of_stream_mid_read_is_an_incomplete_read() -> None:
    async def scenario() -> None:
        server, client, (_, c_writer), (_, s_reader, s_writer) = await _pair()
        c_writer.write(b"abc")
        c_writer.close()
        with pytest.raises(asyncio.IncompleteReadError) as caught:
            await s_reader.readexactly(10)
        assert caught.value.partial == b"abc"
        assert caught.value.expected == 10
        assert await s_reader.read() == b""
        s_writer.close()
        await client.close()
        await server.close()

    _run(scenario)


def test_dialing_an_unknown_endpoint_fails_with_a_transport_error() -> None:
    async def scenario() -> None:
        gone = _transport()
        await gone.start()
        gone_id, gone_addresses = gone.endpoint_id, gone.local_addresses()
        await gone.close()
        client = _transport()
        await client.start()
        with pytest.raises(IrohTransportError):
            await client.dial(gone_id, gone_addresses)
        await client.close()

    iroh_transport.DIAL_TIMEOUT, saved = 3.0, iroh_transport.DIAL_TIMEOUT
    try:
        _run(scenario)
    finally:
        iroh_transport.DIAL_TIMEOUT = saved


def test_transport_errors_are_connection_errors() -> None:
    assert issubclass(IrohTransportError, ConnectionError)


def test_the_same_secret_key_gives_the_same_endpoint_id() -> None:
    async def scenario() -> tuple[bytes, bytes, bytes]:
        key = os.urandom(32)
        async with _transport(key) as first:
            first_id = first.endpoint_id
        async with _transport(key) as second:
            second_id = second.endpoint_id
        async with _transport() as other:
            other_id = other.endpoint_id
        return first_id, second_id, other_id

    first_id, second_id, other_id = _run(scenario)
    assert len(first_id) == 32
    assert first_id == second_id
    assert first_id != other_id


def test_accept_returns_none_once_closed() -> None:
    async def scenario() -> None:
        server = _transport()
        await server.start()
        waiting = asyncio.ensure_future(server.accept())
        await asyncio.sleep(0.2)
        await server.close()
        assert await waiting is None

    _run(scenario)


def test_a_stalled_handshake_does_not_block_other_dialers() -> None:
    async def scenario() -> None:
        iroh = importlib.import_module("iroh")
        server = _transport()
        await server.start()
        stalled = await iroh.Endpoint.bind(
            iroh.EndpointOptions(
                preset=iroh.preset_minimal(),
                relay_mode=iroh.RelayMode.disabled(),
                alpns=[iroh_transport.ALPN],
                bind_addr="127.0.0.1:0",
            )
        )
        address = iroh.EndpointAddr(
            iroh.EndpointId.from_bytes(server.endpoint_id), None, server.local_addresses()
        )
        connection = await stalled.connect(address, iroh_transport.ALPN)
        await connection.open_bi()
        await asyncio.sleep(0.3)
        client = _transport()
        await client.start()
        waiting = asyncio.ensure_future(server.accept())
        started = asyncio.get_running_loop().time()
        dialed = await client.dial(server.endpoint_id, server.local_addresses())
        result = await asyncio.wait_for(waiting, 5)
        assert result is not None
        assert result[0] == client.endpoint_id
        assert dialed
        assert asyncio.get_running_loop().time() - started < iroh_transport.HANDSHAKE_TIMEOUT / 2
        await client.close()
        await server.close()
        await stalled.close()

    _run(scenario)


def test_missing_iroh_gives_an_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import: Any = builtins.__import__
    real_import_module = importlib.import_module

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "iroh":
            raise ImportError("No module named 'iroh'")
        return real_import(name, *args, **kwargs)

    def refuse_module(name: str, package: str | None = None) -> Any:
        if name == "iroh":
            raise ImportError("No module named 'iroh'")
        return real_import_module(name, package)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.setattr(importlib, "import_module", refuse_module)
    with pytest.raises(IrohUnavailableError, match=r"agentmesh\[p2p\]"):
        asyncio.run(_transport().start())

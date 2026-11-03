import asyncio
import contextlib
import enum
import importlib
import logging
from collections.abc import Sequence
from types import ModuleType, TracebackType
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from iroh import Connection, Endpoint, Incoming, RecvStream, SendStream

ALPN = b"agentmesh/sync/0"
READ_LIMIT = 16 * 1024 * 1024
DIAL_TIMEOUT = 20.0
HANDSHAKE_TIMEOUT = 10.0
MAX_PENDING_HANDSHAKES = 32
TEARDOWN_TIMEOUT = 5.0
_PREAMBLE = b"\x00"
_DONE = b"\x01"
_CLOSE_OK = 0
_STREAM_ENDED = "FinishedEarly"
_LOOPBACK = "127.0.0.1"
_log = logging.getLogger(__name__)


class IrohTransportError(ConnectionError):
    pass


class IrohUnavailableError(IrohTransportError):
    pass


class IrohMode(enum.Enum):
    INTERNET = "internet"
    LOCAL = "local"


def _load_iroh() -> ModuleType:
    try:
        return importlib.import_module("iroh")
    except ImportError as exc:
        raise IrohUnavailableError(
            "direct connections need the iroh package: pip install 'agentmesh[p2p]'"
        ) from exc


def _is_end_of_stream(exc: Exception) -> bool:
    return _STREAM_ENDED in str(exc)


class IrohReader:
    def __init__(self, stream: "RecvStream") -> None:
        self._stream = stream
        self._eof = False

    async def readexactly(self, n: int) -> bytes:
        chunks: list[bytes] = []
        remaining = n
        while remaining > 0 and not self._eof:
            chunk = await self._read_chunk(remaining)
            if not chunk:
                self._eof = True
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining > 0:
            raise asyncio.IncompleteReadError(b"".join(chunks), n)
        return b"".join(chunks)

    async def read(self) -> bytes:
        if self._eof:
            return b""
        try:
            data: bytes = await self._stream.read_to_end(READ_LIMIT)
        except Exception as exc:
            if _is_end_of_stream(exc):
                self._eof = True
                return b""
            raise IrohTransportError(f"read failed: {exc}") from exc
        self._eof = True
        return data

    async def _read_chunk(self, limit: int) -> bytes:
        try:
            chunk: bytes = await self._stream.read(limit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise IrohTransportError(f"read failed: {exc}") from exc
        return chunk


class IrohWriter:
    """Writer half of a connection.

    `close()` is the point where the application is done with the connection. The send
    side is finished, a one-byte "done" is sent on a separate stream, and the connection is
    closed only after the peer's own "done" arrives. Closing a QUIC connection discards
    data the peer has not yet read, so neither side may close before the other has
    consumed everything.
    """

    def __init__(
        self, connection: "Connection", send: "SendStream", tasks: set["asyncio.Task[None]"]
    ) -> None:
        self._connection = connection
        self._send = send
        self._tasks = tasks
        self._buffer = bytearray()
        self._closing: asyncio.Task[None] | None = None

    def write(self, data: bytes) -> None:
        self._buffer += data

    async def drain(self) -> None:
        if not self._buffer:
            return
        data = bytes(self._buffer)
        self._buffer.clear()
        try:
            await self._send.write_all(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise IrohTransportError(f"write failed: {exc}") from exc

    def close(self) -> None:
        if self._closing is not None:
            return
        data = bytes(self._buffer)
        self._buffer.clear()
        task = asyncio.get_running_loop().create_task(self._shutdown(data))
        self._closing = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_closed(self) -> None:
        if self._closing is not None:
            await asyncio.shield(self._closing)

    async def _shutdown(self, pending: bytes) -> None:
        try:
            if pending:
                await self._send.write_all(pending)
            await self._send.finish()
            await self._signal_done()
            await asyncio.wait_for(self._await_peer_done(), TEARDOWN_TIMEOUT)
        except Exception as exc:
            _log.debug("iroh connection closed before the peer finished: %s", exc)
        finally:
            self._connection.close(_CLOSE_OK, b"")

    async def _signal_done(self) -> None:
        done = await self._connection.open_uni()
        await done.write_all(_DONE)
        await done.finish()

    async def _await_peer_done(self) -> None:
        stream = await self._connection.accept_uni()
        await stream.read_exact(len(_DONE))


class IrohTransport:
    def __init__(self, secret_key: bytes, mode: IrohMode = IrohMode.INTERNET) -> None:
        self._secret_key = secret_key
        self._mode = mode
        self._endpoint: Endpoint | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._handshakes: set[asyncio.Task[None]] = set()
        self._accepted: asyncio.Queue[tuple[bytes, IrohReader, IrohWriter] | None] = asyncio.Queue()
        self._acceptor: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._endpoint is not None:
            return
        iroh = _load_iroh()
        if self._mode is IrohMode.LOCAL:
            options = iroh.EndpointOptions(
                preset=iroh.preset_minimal(),
                relay_mode=iroh.RelayMode.disabled(),
                alpns=[ALPN],
                bind_addr=f"{_LOOPBACK}:0",
                secret_key=self._secret_key,
            )
        else:
            options = iroh.EndpointOptions(
                preset=iroh.preset_n0(), alpns=[ALPN], secret_key=self._secret_key
            )
        try:
            self._endpoint = await iroh.Endpoint.bind(options)
        except Exception as exc:
            raise IrohTransportError(f"cannot start iroh endpoint: {exc}") from exc
        self._acceptor = asyncio.get_running_loop().create_task(self._accept_loop(self._endpoint))

    @property
    def endpoint_id(self) -> bytes:
        endpoint: bytes = self._require_endpoint().id().to_bytes()
        return endpoint

    def local_addresses(self) -> list[str]:
        sockets: list[str] = self._require_endpoint().bound_sockets()
        return [s for s in sockets if s.startswith(f"{_LOOPBACK}:")]

    async def dial(
        self, endpoint_id: bytes, direct: Sequence[str] = ()
    ) -> tuple[IrohReader, IrohWriter]:
        endpoint = self._require_endpoint()
        iroh = _load_iroh()
        try:
            address = iroh.EndpointAddr(iroh.EndpointId.from_bytes(endpoint_id), None, list(direct))
            connection = await asyncio.wait_for(endpoint.connect(address, ALPN), DIAL_TIMEOUT)
        except TimeoutError as exc:
            raise IrohTransportError("timed out connecting to the device") from exc
        except Exception as exc:
            raise IrohTransportError(f"cannot connect to the device: {exc}") from exc
        try:
            bi = await connection.open_bi()
            send = bi.send()
            await send.write_all(_PREAMBLE)
        except Exception as exc:
            connection.close(_CLOSE_OK, b"")
            raise IrohTransportError(f"cannot open a stream: {exc}") from exc
        return IrohReader(bi.recv()), IrohWriter(connection, send, self._tasks)

    async def accept(self) -> tuple[bytes, IrohReader, IrohWriter] | None:
        self._require_endpoint()
        item = await self._accepted.get()
        if item is None:
            self._accepted.put_nowait(None)
        return item

    async def close(self) -> None:
        endpoint = self._endpoint
        if endpoint is None:
            return
        self._endpoint = None
        background = [t for t in (self._acceptor, *self._handshakes) if t is not None]
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        while not self._accepted.empty():
            item = self._accepted.get_nowait()
            if item is not None:
                item[2].close()
        self._accepted.put_nowait(None)
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=TEARDOWN_TIMEOUT + 1)
        with contextlib.suppress(Exception):
            await endpoint.close()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _accept_loop(self, endpoint: "Endpoint") -> None:
        while True:
            try:
                incoming = await endpoint.accept_next()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not endpoint.is_closed():
                    _log.debug("accept failed: %s", exc)
                break
            if incoming is None:
                break
            if len(self._handshakes) >= MAX_PENDING_HANDSHAKES:
                _log.debug("too many pending handshakes; ignoring an incoming connection")
                continue
            task = asyncio.get_running_loop().create_task(self._admit(incoming))
            self._handshakes.add(task)
            task.add_done_callback(self._handshakes.discard)
        self._accepted.put_nowait(None)

    async def _admit(self, incoming: "Incoming") -> None:
        try:
            accepted = await asyncio.wait_for(self._handshake(incoming), HANDSHAKE_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.debug("dropped an incoming iroh connection: %s", exc)
            return
        self._accepted.put_nowait(accepted)

    async def _handshake(self, incoming: "Incoming") -> tuple[bytes, IrohReader, IrohWriter]:
        accepting = await incoming.accept()
        connection = await accepting.connect()
        try:
            bi = await connection.accept_bi()
            receive = bi.recv()
            await receive.read_exact(len(_PREAMBLE))
            remote: bytes = connection.remote_id().to_bytes()
        except BaseException:
            connection.close(_CLOSE_OK, b"")
            raise
        return remote, IrohReader(receive), IrohWriter(connection, bi.send(), self._tasks)

    def _require_endpoint(self) -> "Endpoint":
        if self._endpoint is None:
            raise IrohTransportError("the iroh transport is not started")
        return self._endpoint

import asyncio
import hmac
import json
import logging
import os
import secrets
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

MAX_MESSAGE_SIZE = 1024 * 1024
_OWNER_ONLY = 0o600
_log = logging.getLogger(__name__)

Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
Dispatch = Callable[[str, Mapping[str, object]], str]


class RpcError(Exception):
    pass


class Endpoint(Protocol):
    async def serve(self, handler: Handler) -> asyncio.Server: ...

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]: ...


class UnixEndpoint:
    """A socket file only the owner can open."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def serve(self, handler: Handler) -> asyncio.Server:
        self._path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(handler, path=self._path, limit=MAX_MESSAGE_SIZE)
        self._path.chmod(_OWNER_ONLY)
        return server

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.open_unix_connection(self._path, limit=MAX_MESSAGE_SIZE)
        except OSError as exc:
            raise RpcError("agentmesh service is not running") from exc


class TcpEndpoint:
    """Loopback TCP for platforms without Unix sockets in asyncio.

    Any local process can reach a loopback port, so each connection must
    first present a random token kept in an owner-only file.
    """

    def __init__(self, token_file: Path) -> None:
        self._token_file = token_file

    async def serve(self, handler: Handler) -> asyncio.Server:
        token = secrets.token_hex(32)

        async def guarded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            presented = (await reader.readline()).decode().strip()
            if not hmac.compare_digest(presented, token):
                writer.close()
                return
            await handler(reader, writer)

        server = await asyncio.start_server(guarded, "127.0.0.1", 0, limit=MAX_MESSAGE_SIZE)
        port = server.sockets[0].getsockname()[1]
        self._token_file.unlink(missing_ok=True)
        fd = os.open(self._token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _OWNER_ONLY)
        with os.fdopen(fd, "w") as file:
            json.dump({"port": port, "token": token}, file)
        return server

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            details = json.loads(self._token_file.read_text())
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", details["port"], limit=MAX_MESSAGE_SIZE
            )
        except (OSError, ValueError, KeyError) as exc:
            raise RpcError("agentmesh service is not running") from exc
        writer.write(f"{details['token']}\n".encode())
        await writer.drain()
        return reader, writer


def default_endpoint(home: Path) -> Endpoint:
    if sys.platform == "win32":
        return TcpEndpoint(home / "service.json")
    return UnixEndpoint(home / "service.sock")


class RpcServer:
    """Serves newline-delimited JSON requests: {"method", "params"} -> {"ok", "text" | "error"}."""

    def __init__(
        self,
        endpoint: Endpoint,
        dispatch: Dispatch,
        expected: tuple[type[Exception], ...] = (),
    ) -> None:
        self._endpoint = endpoint
        self._dispatch = dispatch
        self._expected = expected

    async def start(self) -> asyncio.Server:
        return await self._endpoint.serve(self._handle)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                writer.write(json.dumps(self._respond(line)).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            writer.close()

    def _respond(self, line: bytes) -> dict[str, object]:
        try:
            request = json.loads(line)
        except ValueError:
            return {"ok": False, "error": "request must be json"}
        if not isinstance(request, dict) or not isinstance(request.get("method"), str):
            return {"ok": False, "error": "request needs a method"}
        params = request.get("params", {})
        if not isinstance(params, dict):
            return {"ok": False, "error": "params must be an object"}
        try:
            return {"ok": True, "text": self._dispatch(request["method"], params)}
        except self._expected as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            _log.exception("%s failed", request["method"])
            return {"ok": False, "error": f"internal error: {type(exc).__name__}"}


class RpcClient:
    def __init__(self, endpoint: Endpoint) -> None:
        self._endpoint = endpoint

    async def call(self, method: str, params: Mapping[str, object] | None = None) -> str:
        reader, writer = await self._endpoint.connect()
        try:
            request = {"method": method, "params": dict(params or {})}
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
        if not line:
            raise RpcError("agentmesh service closed the connection")
        response = json.loads(line)
        if not response.get("ok"):
            raise RpcError(str(response.get("error", "unknown error")))
        return str(response["text"])

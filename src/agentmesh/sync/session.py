import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from agentmesh.identity import KeyHandle, Signer, es256
from agentmesh.log.entry import Kind
from agentmesh.store.base import Store
from agentmesh.store.ingest import Ingestor, Outcome
from agentmesh.sync.wire import (
    MAX_BATCH,
    NONCE_SIZE,
    Auth,
    Done,
    Entries,
    Frontier,
    Hello,
    Message,
    WireError,
    auth_input,
    read_message,
    write_message,
)

DEFAULT_TIMEOUT = 15.0
_M = TypeVar("_M", bound=Message)
_log = logging.getLogger(__name__)


class SyncError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SyncResult:
    peer: bytes
    peer_is_member: bool
    sent: int
    received: int
    stored: int


class SyncNode:
    """Runs protocol §8 sync sessions for one device, as initiator or responder."""

    def __init__(
        self,
        store: Store,
        ingestor: Ingestor,
        signer: Signer,
        handle: KeyHandle,
        team: bytes,
        *,
        is_member: Callable[[bytes], bool],
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._store = store
        self._ingestor = ingestor
        self._signer = signer
        self._handle = handle
        self._team = team
        self._is_member = is_member
        self._timeout = timeout
        self._author = signer.public_key(handle)

    @property
    def author(self) -> bytes:
        return self._author

    async def serve(self, host: str, port: int = 0) -> asyncio.Server:
        return await asyncio.start_server(self._accept, host, port)

    async def sync_with(self, host: str, port: int) -> SyncResult:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), self._timeout
            )
        except (OSError, TimeoutError) as exc:
            raise SyncError(f"cannot reach {host}:{port}: {exc}") from exc
        try:
            result = await self._session(reader, writer, initiator=True)
            await self._await_peer_close(reader)
            return result
        finally:
            writer.close()

    async def _await_peer_close(self, reader: asyncio.StreamReader) -> None:
        """The responder closes only after storing everything it received, so waiting for
        that close means a returned result really is the state on both sides."""
        try:
            await asyncio.wait_for(reader.read(), self._timeout)
        except TimeoutError as exc:
            raise SyncError("peer did not finish the session") from exc

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            result = await self._session(reader, writer, initiator=False)
            _log.debug("synced with %s: %s", result.peer[1:4].hex(), result)
        except SyncError as exc:
            _log.info("sync from %s refused: %s", writer.get_extra_info("peername"), exc)
        finally:
            writer.close()

    async def _session(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, initiator: bool
    ) -> SyncResult:
        try:
            return await asyncio.wait_for(self._exchange(reader, writer, initiator), self._timeout)
        except TimeoutError as exc:
            raise SyncError("sync timed out") from exc
        except (WireError, ConnectionError) as exc:
            raise SyncError(str(exc)) from exc

    async def _exchange(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, initiator: bool
    ) -> SyncResult:
        peer = await self._handshake(reader, writer, initiator)
        member = self._is_member(peer)
        await write_message(writer, Frontier(self._store.frontier()))
        theirs = await _expect(reader, Frontier)
        sent, (received, stored) = await asyncio.gather(
            self._send(writer, theirs.heads, member=member), self._receive(reader)
        )
        return SyncResult(peer, member, sent, received, stored)

    async def _handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, initiator: bool
    ) -> bytes:
        nonce = secrets.token_bytes(NONCE_SIZE)
        await write_message(writer, Hello(self._team, nonce))
        hello = await _expect(reader, Hello)
        if hello.team != self._team:
            raise SyncError("peer belongs to another team")
        nonces = (nonce, hello.nonce) if initiator else (hello.nonce, nonce)
        mine = auth_input(
            self._team, initiator=initiator, initiator_nonce=nonces[0], responder_nonce=nonces[1]
        )
        await write_message(writer, Auth(self._author, self._signer.sign(self._handle, mine)))
        auth = await _expect(reader, Auth)
        theirs = auth_input(
            self._team,
            initiator=not initiator,
            initiator_nonce=nonces[0],
            responder_nonce=nonces[1],
        )
        if not es256.verify(auth.key, auth.sig, theirs):
            raise SyncError("peer failed the handshake")
        if auth.key == self._author:
            raise SyncError("connected to itself")
        return auth.key

    async def _send(
        self, writer: asyncio.StreamWriter, heads: dict[bytes, int], *, member: bool
    ) -> int:
        batch: list[bytes] = []
        sent = 0
        for author, seq in self._store.frontier().items():
            if seq <= heads.get(author, 0):
                continue
            for entry in self._store.entries_after(author, heads.get(author, 0)):
                if not member and entry.content.kind is not Kind.ROSTER:
                    continue
                batch.append(entry.encode())
                if len(batch) == MAX_BATCH:
                    await write_message(writer, Entries(tuple(batch)))
                    sent += len(batch)
                    batch.clear()
        if batch:
            await write_message(writer, Entries(tuple(batch)))
            sent += len(batch)
        await write_message(writer, Done())
        return sent

    async def _receive(self, reader: asyncio.StreamReader) -> tuple[int, int]:
        received = stored = 0
        while True:
            message = await read_message(reader)
            if isinstance(message, Done):
                return received, stored
            if not isinstance(message, Entries):
                raise SyncError(f"unexpected {type(message).__name__} while receiving entries")
            for raw in message.items:
                receipt = self._ingestor.receive(raw)
                received += 1
                stored += (receipt.outcome is Outcome.STORED) + len(receipt.released)


async def _expect(reader: asyncio.StreamReader, kind: type[_M]) -> _M:
    message = await read_message(reader)
    if not isinstance(message, kind):
        raise SyncError(f"expected {kind.__name__}, got {type(message).__name__}")
    return message

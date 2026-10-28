import asyncio
from dataclasses import dataclass

from agentmesh.identity.es256 import PUBLIC_KEY_SIZE, SIGNATURE_SIZE
from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode
from agentmesh.log.entry import PROTOCOL_VERSION, TEAM_ID_SIZE
from agentmesh.log.verify import MAX_ENTRY_SIZE

MAX_FRAME = 1024 * 1024
MAX_BATCH = 48
MAX_HEADS = 10_000
NONCE_SIZE = 32
_LENGTH_SIZE = 4
_AUTH_TAG = b"agentmesh/auth/v0\x00"


class WireError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Hello:
    team: bytes
    nonce: bytes


@dataclass(frozen=True, slots=True)
class Auth:
    key: bytes
    sig: bytes


@dataclass(frozen=True, slots=True)
class Frontier:
    heads: dict[bytes, int]


@dataclass(frozen=True, slots=True)
class Entries:
    items: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class Done:
    pass


Message = Hello | Auth | Frontier | Entries | Done


def auth_input(
    team: bytes, *, initiator: bool, initiator_nonce: bytes, responder_nonce: bytes
) -> bytes:
    """What each side signs. The role byte stops a signature being reflected back at its signer."""
    role = b"I" if initiator else b"R"
    return _AUTH_TAG + team + role + initiator_nonce + responder_nonce


def encode_message(message: Message) -> bytes:
    fields: dict[str, CborValue]
    match message:
        case Hello(team, nonce):
            fields = {"t": "hello", "v": PROTOCOL_VERSION, "team": team, "nonce": nonce}
        case Auth(key, sig):
            fields = {"t": "auth", "key": key, "sig": sig}
        case Frontier(heads):
            pairs: list[CborValue] = [[author, seq] for author, seq in sorted(heads.items())]
            fields = {"t": "frontier", "heads": pairs}
        case Entries(items):
            fields = {"t": "entries", "items": list(items)}
        case Done():
            fields = {"t": "done"}
    return encode(fields)


def decode_message(raw: bytes) -> Message:
    try:
        value = decode(raw)
    except CanonicalError as exc:
        raise WireError(f"bad frame: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("t"), str):
        raise WireError("frame must be a map with a type")
    kind = value["t"]
    if kind == "hello":
        _keys(value, {"t", "v", "team", "nonce"})
        if value["v"] != PROTOCOL_VERSION:
            raise WireError(f"unsupported protocol version {value['v']!r}")
        return Hello(_bytes(value["team"], TEAM_ID_SIZE), _bytes(value["nonce"], NONCE_SIZE))
    if kind == "auth":
        _keys(value, {"t", "key", "sig"})
        return Auth(_bytes(value["key"], PUBLIC_KEY_SIZE), _bytes(value["sig"], SIGNATURE_SIZE))
    if kind == "frontier":
        _keys(value, {"t", "heads"})
        return Frontier(_heads(value["heads"]))
    if kind == "entries":
        _keys(value, {"t", "items"})
        items = value["items"]
        if not isinstance(items, list) or len(items) > MAX_BATCH:
            raise WireError(f"entries must be a list of at most {MAX_BATCH}")
        return Entries(tuple(_bytes(item, None, MAX_ENTRY_SIZE) for item in items))
    if kind == "done":
        _keys(value, {"t"})
        return Done()
    raise WireError(f"unknown frame type {kind!r}")


async def write_message(writer: asyncio.StreamWriter, message: Message) -> None:
    body = encode_message(message)
    writer.write(len(body).to_bytes(_LENGTH_SIZE, "big") + body)
    await writer.drain()


async def read_message(reader: asyncio.StreamReader) -> Message:
    try:
        length = int.from_bytes(await reader.readexactly(_LENGTH_SIZE), "big")
        if length > MAX_FRAME:
            raise WireError(f"frame of {length} bytes is over the {MAX_FRAME} limit")
        return decode_message(await reader.readexactly(length))
    except asyncio.IncompleteReadError as exc:
        raise WireError("peer closed the connection") from exc


def _keys(value: dict[str, CborValue], expected: set[str]) -> None:
    if value.keys() != expected:
        raise WireError(f"frame fields must be exactly {sorted(expected)}")


def _bytes(value: CborValue, size: int | None, limit: int | None = None) -> bytes:
    if not isinstance(value, bytes):
        raise WireError("expected bytes")
    if size is not None and len(value) != size:
        raise WireError(f"expected {size} bytes")
    if limit is not None and len(value) > limit:
        raise WireError(f"item over {limit} bytes")
    return value


def _heads(value: CborValue) -> dict[bytes, int]:
    if not isinstance(value, list) or len(value) > MAX_HEADS:
        raise WireError(f"heads must be a list of at most {MAX_HEADS}")
    heads: dict[bytes, int] = {}
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:  # noqa: PLR2004 - [author, seq]
            raise WireError("each head is [author, seq]")
        author, seq = pair
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise WireError("seq must be a positive integer")
        heads[_bytes(author, PUBLIC_KEY_SIZE)] = seq
    return heads

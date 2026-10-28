import asyncio

import pytest

from agentmesh.log.canonical import encode
from agentmesh.sync.wire import (
    MAX_BATCH,
    MAX_FRAME,
    Auth,
    Done,
    Entries,
    Frontier,
    Hello,
    Message,
    WireError,
    auth_input,
    decode_message,
    encode_message,
    read_message,
)

KEY = b"\x02" + b"\x01" * 32


@pytest.mark.parametrize(
    "message",
    [
        Hello(b"\x01" * 16, b"\x02" * 32),
        Auth(KEY, b"\x03" * 64),
        Frontier({KEY: 3, b"\x03" + b"\x00" * 32: 1}),
        Frontier({}),
        Entries((b"one", b"two")),
        Done(),
    ],
)
def test_round_trip(message: Message) -> None:
    assert decode_message(encode_message(message)) == message


def test_auth_input_binds_role_and_both_nonces() -> None:
    team, a, b = b"\x01" * 16, b"\x02" * 32, b"\x03" * 32
    mine = auth_input(team, initiator=True, initiator_nonce=a, responder_nonce=b)
    theirs = auth_input(team, initiator=False, initiator_nonce=a, responder_nonce=b)
    assert mine.startswith(b"agentmesh/auth/v0\x00")
    assert mine != theirs
    assert mine != auth_input(team, initiator=True, initiator_nonce=b, responder_nonce=a)


@pytest.mark.parametrize(
    "fields",
    [
        {"t": "hello", "v": 1, "team": b"\x01" * 16, "nonce": b"\x02" * 32},
        {"t": "hello", "v": 0, "team": b"\x01" * 15, "nonce": b"\x02" * 32},
        {"t": "auth", "key": KEY, "sig": b"\x00" * 63},
        {"t": "auth", "key": KEY},
        {"t": "frontier", "heads": [[KEY, 0]]},
        {"t": "frontier", "heads": [[KEY]]},
        {"t": "frontier", "heads": [[b"\x02" * 32, 1]]},
        {"t": "entries", "items": [b"x"] * (MAX_BATCH + 1)},
        {"t": "entries", "items": ["text"]},
        {"t": "entries", "items": [b"x" * (16 * 1024 + 1)]},
        {"t": "done", "extra": 1},
        {"t": "bye"},
        {"no": "type"},
    ],
)
def test_rejects_malformed_frames(fields: dict[str, object]) -> None:
    with pytest.raises(WireError):
        decode_message(encode(fields))  # type: ignore[arg-type]  # malformed on purpose


def test_rejects_non_canonical_frames() -> None:
    with pytest.raises(WireError, match="bad frame"):
        decode_message(b"\xff")


async def _read(data: bytes) -> Message:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await read_message(reader)


def test_read_rejects_oversize_and_truncated_frames() -> None:
    with pytest.raises(WireError, match="limit"):
        asyncio.run(_read((MAX_FRAME + 1).to_bytes(4, "big")))
    body = encode_message(Done())
    with pytest.raises(WireError, match="closed"):
        asyncio.run(_read(len(body).to_bytes(4, "big") + body[:-1]))
    assert asyncio.run(_read(len(body).to_bytes(4, "big") + body)) == Done()

import pytest

from agentmesh.log.body import (
    SUMMARY_MAX,
    BodyError,
    MessageBody,
    MessageType,
    Status,
    StatusBody,
)
from agentmesh.log.canonical import encode


def test_message_round_trip() -> None:
    body = MessageBody(
        MessageType.FEATURE_REQUEST,
        ("tobi", "@mobile", "#backend"),
        "Add retries to the sync queue",
        detail="Retry on 5xx",
        acceptance="Retries three times",
        links=("PR #42",),
    )
    assert MessageBody.decode(body.encode()) == body


def test_minimal_message_omits_optional_fields() -> None:
    body = MessageBody(MessageType.FYI, ("#backend",), "hi")
    assert MessageBody.decode(body.encode()) == body
    assert b"detail" not in body.encode()


def test_summary_at_the_limit_is_allowed() -> None:
    MessageBody(MessageType.FYI, ("#backend",), "x" * SUMMARY_MAX)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"summary": "x" * (SUMMARY_MAX + 1)}, "summary is 141 chars, max 140"),
        ({"summary": "  "}, "summary must not be empty"),
        ({"summary": "café"}, "NFC"),
        ({"to": ()}, "to needs"),
        ({"to": tuple(f"m{i}" for i in range(9))}, "to needs"),
        ({"to": ("Tobi",)}, "invalid target"),
        ({"to": ("dm:a+b",)}, "invalid target"),
        ({"detail": "x" * 2001}, "detail"),
        ({"acceptance": "done"}, "only allowed on feature requests"),
        ({"links": tuple("l" for _ in range(11))}, "links"),
        ({"links": ("x" * 201,)}, "link"),
    ],
)
def test_message_limits(changes: dict[str, object], reason: str) -> None:
    fields: dict[str, object] = {"type": MessageType.FYI, "to": ("#backend",), "summary": "hi"}
    fields.update(changes)
    with pytest.raises(BodyError, match=reason):
        MessageBody(**fields)  # type: ignore[arg-type]  # built from a dict on purpose


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "fyi", "to": ["#backend"]},
        {"type": "rant", "to": ["#backend"], "summary": "hi"},
        {"type": "fyi", "to": "#backend", "summary": "hi"},
        {"type": "fyi", "to": ["#backend"], "summary": "hi", "detail": None},
        {"type": "fyi", "to": ["#backend"], "summary": "hi", "from": "gef"},
    ],
)
def test_message_decode_rejects_malformed(fields: dict[str, object]) -> None:
    with pytest.raises(BodyError):
        MessageBody.decode(encode(fields))  # type: ignore[arg-type]  # malformed on purpose


def test_status_round_trip() -> None:
    body = StatusBody(b"\x01" * 32, Status.CLAIMED, "on it")
    assert StatusBody.decode(body.encode()) == body


@pytest.mark.parametrize(
    "fields",
    [
        {"target": b"\x01" * 31, "status": "done"},
        {"target": "id", "status": "done"},
        {"target": b"\x01" * 32, "status": "finished"},
        {"target": b"\x01" * 32, "status": "done", "note": "x" * 141},
    ],
)
def test_status_decode_rejects_malformed(fields: dict[str, object]) -> None:
    with pytest.raises(BodyError):
        StatusBody.decode(encode(fields))  # type: ignore[arg-type]  # malformed on purpose

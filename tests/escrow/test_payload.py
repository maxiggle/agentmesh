import json
from pathlib import Path
from typing import Any

import pytest

from ehor.escrow.payload import (
    PayloadError,
    format_near,
    parse_near,
    release_digest,
    release_payload,
)

VECTOR = Path(__file__).resolve().parents[2] / "docs" / "vectors" / "release-v1.json"


@pytest.fixture
def vector() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(VECTOR.read_text(encoding="utf-8"))
    return loaded


def build(vector: dict[str, Any], **overrides: Any) -> str:
    fields = {
        "chain_id": vector["chain_id"],
        "contract": vector["contract"],
        "bounty_id": vector["bounty_id"],
        "claimant": vector["claimant"],
        "amount_yocto": int(vector["amount_yocto"]),
    }
    fields.update(overrides)
    return release_payload(**fields)


def test_payload_matches_shared_vector(vector: dict[str, Any]) -> None:
    assert build(vector) == vector["payload"]


def test_digest_matches_shared_vector(vector: dict[str, Any]) -> None:
    assert release_digest(vector["payload"]).hex() == vector["sha256"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", "mainnet"),
        ("contract", "other-escrow.testnet"),
        ("bounty_id", 8),
        ("claimant", "mallory.testnet"),
        ("amount_yocto", 1),
    ],
)
def test_every_field_changes_the_digest(vector: dict[str, Any], field: str, value: Any) -> None:
    assert release_digest(build(vector, **{field: value})).hex() != vector["sha256"]


@pytest.mark.parametrize("field", ["chain_id", "contract", "claimant"])
def test_separator_in_field_is_rejected(vector: dict[str, Any], field: str) -> None:
    with pytest.raises(PayloadError, match=field):
        build(vector, **{field: "a|b"})


@pytest.mark.parametrize("field", ["chain_id", "contract", "claimant"])
def test_empty_field_is_rejected(vector: dict[str, Any], field: str) -> None:
    with pytest.raises(PayloadError, match=field):
        build(vector, **{field: ""})


@pytest.mark.parametrize("field", ["bounty_id", "amount_yocto"])
@pytest.mark.parametrize("value", [-1, True])
def test_bad_number_is_rejected(vector: dict[str, Any], field: str, value: object) -> None:
    with pytest.raises(PayloadError, match=field):
        build(vector, **{field: value})


def test_zero_amount_is_rejected(vector: dict[str, Any]) -> None:
    with pytest.raises(PayloadError, match="amount_yocto"):
        build(vector, amount_yocto=0)


@pytest.mark.parametrize(
    ("yocto", "text"),
    [
        (1, "0.000000000000000000000001"),
        (10**24, "1"),
        (25 * 10**23, "2.5"),
        (10**30, "1000000"),
        (123456789012345678901234567890123, "123456789.012345678901234567890123"),
    ],
)
def test_format_near_is_exact(yocto: int, text: str) -> None:
    assert format_near(yocto) == text


@pytest.mark.parametrize(
    ("text", "yocto"),
    [
        ("1", 10**24),
        ("0.5", 5 * 10**23),
        (" 2.25 ", 225 * 10**22),
        ("0.000000000000000000000001", 1),
        ("123456789.012345678901234567890123", 123456789012345678901234567890123),
    ],
)
def test_parse_near_is_exact_and_inverts_format(text: str, yocto: int) -> None:
    assert parse_near(text) == yocto
    assert parse_near(format_near(yocto)) == yocto


@pytest.mark.parametrize(
    "text", ["", "0", "0.0", "-1", "1e3", ".5", "1.", "1,5", "0.0000000000000000000000001", "x"]
)
def test_parse_near_refuses_bad_amounts(text: str) -> None:
    with pytest.raises(PayloadError):
        parse_near(text)

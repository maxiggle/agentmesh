import json
from pathlib import Path
from typing import Any

import pytest

from ehor.escrow.payload import PayloadError, release_digest, release_payload

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

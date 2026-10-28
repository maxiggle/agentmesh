import pytest

from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        2**64 - 1,
        True,
        None,
        b"\x00\xff",
        "héllo",
        [1, [2, b"x"]],
        {"b": 1, "a": {"c": [None]}},
    ],
)
def test_round_trips(value: CborValue) -> None:
    assert decode(encode(value)) == value


def test_map_key_order_does_not_change_encoding() -> None:
    assert encode({"bb": 1, "a": 2, "c": 3}) == encode({"c": 3, "a": 2, "bb": 1})


def test_map_keys_sort_by_encoded_bytes() -> None:
    assert encode({"bb": 1, "c": 2, "a": 3}) == bytes.fromhex("a3616103616302626262" + "01")


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        2**64,
        -(2**64) - 1,
        {1: "int key"},
        {b"k": 1},
        (1, 2),
        "café",
        [[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]],
    ],
)
def test_encode_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(CanonicalError):
        encode(value)  # type: ignore[arg-type]  # deliberately invalid input


@pytest.mark.parametrize(
    ("hex_bytes", "why"),
    [
        ("1817", "integer not in shortest form"),
        ("9f01ff", "indefinite-length array"),
        ("5f4161ff", "indefinite-length bytes"),
        ("a2616201616101", "map keys out of order"),
        ("0102", "trailing bytes"),
        ("c11a00000001", "tagged value"),
        ("f93c00", "float"),
        ("a1016161", "integer map key"),
        ("", "empty input"),
        ("1a", "truncated input"),
        ("6365cc81", "text not nfc normalized"),
    ],
)
def test_decode_rejects_non_canonical_input(hex_bytes: str, why: str) -> None:
    with pytest.raises(CanonicalError):
        decode(bytes.fromhex(hex_bytes))

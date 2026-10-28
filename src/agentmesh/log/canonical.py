import unicodedata
from typing import TypeAlias

import cbor2

CborValue: TypeAlias = int | bool | bytes | str | list["CborValue"] | dict[str, "CborValue"] | None

_MAX_DEPTH = 16
_MIN_INT = -(2**64)
_MAX_INT = 2**64 - 1


class CanonicalError(ValueError):
    pass


def encode(value: CborValue) -> bytes:
    """Encode with RFC 8949 core deterministic encoding."""
    _check(value, 0)
    return cbor2.dumps(value, canonical=True)


def decode(data: bytes) -> CborValue:
    """Decode bytes that must already be in core deterministic encoding."""
    try:
        value: CborValue = cbor2.loads(data)
    except cbor2.CBORDecodeError as exc:
        raise CanonicalError(f"invalid cbor: {exc}") from exc
    _check(value, 0)
    if encode(value) != data:
        raise CanonicalError("not deterministically encoded")
    return value


def _check(value: object, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise CanonicalError("nested too deeply")
    match value:
        case None | bool() | bytes():
            return
        case int():
            if not _MIN_INT <= value <= _MAX_INT:
                raise CanonicalError("integer out of range")
        case str():
            if unicodedata.normalize("NFC", value) != value:
                raise CanonicalError("text is not NFC normalized")
        case list():
            for item in value:
                _check(item, depth + 1)
        case dict():
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalError("map keys must be text")
                _check(key, depth + 1)
                _check(item, depth + 1)
        case _:
            raise CanonicalError(f"unsupported type {type(value).__name__}")

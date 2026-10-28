import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode
from agentmesh.log.entry import ENTRY_ID_SIZE
from agentmesh.log.names import HANDLE, ROLE, TEAM_CHANNEL

SUMMARY_MAX = 140
DETAIL_MAX = 2000
ACCEPTANCE_MAX = 280
NOTE_MAX = 140
LINK_MAX = 200
LINKS_MAX = 10
TARGETS_MAX = 8

_E = TypeVar("_E", bound=StrEnum)


class BodyError(ValueError):
    pass


class MessageType(StrEnum):
    FEATURE_REQUEST = "feature_request"
    SHIPPED = "shipped"
    BLOCKED = "blocked"
    QUESTION = "question"
    FYI = "fyi"


class Status(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    DONE = "done"
    WONTFIX = "wontfix"


def is_valid_target(target: str) -> bool:
    return any(p.fullmatch(target) for p in (HANDLE, ROLE, TEAM_CHANNEL))


@dataclass(frozen=True, slots=True)
class MessageBody:
    type: MessageType
    to: tuple[str, ...]
    summary: str
    detail: str | None = None
    acceptance: str | None = None
    links: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= len(self.to) <= TARGETS_MAX:
            raise BodyError(f"to needs 1-{TARGETS_MAX} targets")
        for target in self.to:
            if not is_valid_target(target):
                raise BodyError(f"invalid target {target!r}")
        _check_text("summary", self.summary, SUMMARY_MAX, required=True)
        _check_text("detail", self.detail, DETAIL_MAX)
        _check_text("acceptance", self.acceptance, ACCEPTANCE_MAX)
        if self.acceptance is not None and self.type is not MessageType.FEATURE_REQUEST:
            raise BodyError("acceptance is only allowed on feature requests")
        if len(self.links) > LINKS_MAX:
            raise BodyError(f"at most {LINKS_MAX} links")
        for link in self.links:
            _check_text("link", link, LINK_MAX, required=True)

    def encode(self) -> bytes:
        fields: dict[str, CborValue] = {
            "type": self.type.value,
            "to": list(self.to),
            "summary": self.summary,
        }
        if self.detail is not None:
            fields["detail"] = self.detail
        if self.acceptance is not None:
            fields["acceptance"] = self.acceptance
        if self.links:
            fields["links"] = list(self.links)
        return encode(fields)

    @classmethod
    def decode(cls, raw: bytes) -> "MessageBody":
        fields = _decode_map(
            raw, required={"type", "to", "summary"}, optional={"detail", "acceptance", "links"}
        )
        return cls(
            type=_enum(MessageType, fields["type"], "type"),
            to=_text_list(fields["to"], "to"),
            summary=_text(fields["summary"], "summary"),
            detail=_optional_text(fields, "detail"),
            acceptance=_optional_text(fields, "acceptance"),
            links=_text_list(fields["links"], "links") if "links" in fields else (),
        )


@dataclass(frozen=True, slots=True)
class StatusBody:
    target: bytes
    status: Status
    note: str | None = None

    def __post_init__(self) -> None:
        if len(self.target) != ENTRY_ID_SIZE:
            raise BodyError(f"target must be a {ENTRY_ID_SIZE}-byte entry id")
        _check_text("note", self.note, NOTE_MAX)

    def encode(self) -> bytes:
        fields: dict[str, CborValue] = {"target": self.target, "status": self.status.value}
        if self.note is not None:
            fields["note"] = self.note
        return encode(fields)

    @classmethod
    def decode(cls, raw: bytes) -> "StatusBody":
        fields = _decode_map(raw, required={"target", "status"}, optional={"note"})
        target = fields["target"]
        if not isinstance(target, bytes):
            raise BodyError("target must be bytes")
        return cls(
            target=target,
            status=_enum(Status, fields["status"], "status"),
            note=_optional_text(fields, "note"),
        )


def _check_text(name: str, value: str | None, limit: int, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise BodyError(f"{name} is required")
        return
    if unicodedata.normalize("NFC", value) != value:
        raise BodyError(f"{name} must be NFC normalized")
    if required and not value.strip():
        raise BodyError(f"{name} must not be empty")
    if len(value) > limit:
        raise BodyError(f"{name} is {len(value)} chars, max {limit}")


def _decode_map(raw: bytes, *, required: set[str], optional: set[str]) -> dict[str, CborValue]:
    try:
        value = decode(raw)
    except CanonicalError as exc:
        raise BodyError(str(exc)) from exc
    if not isinstance(value, dict):
        raise BodyError("body must be a map")
    if not required <= value.keys() or not value.keys() <= required | optional:
        raise BodyError(f"body fields must be {sorted(required)} plus optional {sorted(optional)}")
    return value


def _text(value: CborValue, name: str) -> str:
    if not isinstance(value, str):
        raise BodyError(f"{name} must be text")
    return value


def _optional_text(fields: dict[str, CborValue], name: str) -> str | None:
    return _text(fields[name], name) if name in fields else None


def _text_list(value: CborValue, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BodyError(f"{name} must be an array")
    return tuple(_text(item, f"{name}[]") for item in value)


def _enum(kind: type[_E], value: CborValue, name: str) -> _E:
    try:
        return kind(_text(value, name))
    except ValueError as exc:
        raise BodyError(f"unknown {name} {value!r}") from exc

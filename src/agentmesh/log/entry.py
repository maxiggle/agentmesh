import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from agentmesh.identity import KeyHandle, Signer
from agentmesh.identity.assertion import Assertion
from agentmesh.identity.es256 import PUBLIC_KEY_SIZE
from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode

PROTOCOL_VERSION = 0
ENTRY_ID_SIZE = 32
TEAM_ID_SIZE = 16
SIG_ALG_ES256 = "es256"
SIG_ALG_FIDO2 = "es256-fido2"

_ENTRY_TAG = b"agentmesh/entry/v0\x00"
_SIG_TAG = b"agentmesh/sig/v0\x00"
_CONTENT_FIELDS = frozenset(
    {"v", "author", "seq", "prev", "team", "channel", "kind", "refs", "ts", "body"}
)
_SIG_FIELDS = frozenset({"alg", "value"})
_FIDO2_SIG_FIELDS = _SIG_FIELDS | {"auth_data"}


class EntryFormatError(ValueError):
    pass


class Kind(StrEnum):
    MESSAGE = "message"
    STATUS = "status"
    ROSTER = "roster"
    KEY_ANNOUNCE = "key_announce"


@dataclass(frozen=True, slots=True)
class Signature:
    alg: str
    value: bytes
    auth_data: bytes | None = None

    @classmethod
    def from_assertion(cls, assertion: Assertion) -> "Signature":
        return cls(SIG_ALG_FIDO2, assertion.signature, assertion.auth_data)

    def to_cbor(self) -> dict[str, CborValue]:
        fields: dict[str, CborValue] = {"alg": self.alg, "value": self.value}
        if self.auth_data is not None:
            fields["auth_data"] = self.auth_data
        return fields

    @classmethod
    def from_cbor(cls, value: CborValue) -> "Signature":
        if not isinstance(value, dict) or value.keys() not in (_SIG_FIELDS, _FIDO2_SIG_FIELDS):
            raise EntryFormatError(
                f"sig fields must be {sorted(_SIG_FIELDS)} or {sorted(_FIDO2_SIG_FIELDS)}"
            )
        auth_data = value.get("auth_data")
        return cls(
            _as_text(value["alg"], "sig.alg"),
            _as_bytes(value["value"], "sig.value"),
            None if auth_data is None else _as_bytes(auth_data, "sig.auth_data"),
        )


@dataclass(frozen=True, slots=True)
class EntryContent:
    author: bytes
    seq: int
    prev: bytes | None
    team: bytes
    channel: str
    kind: Kind
    refs: tuple[bytes, ...]
    ts: int
    body: bytes
    v: int = PROTOCOL_VERSION
    _signed_bytes: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_signed_bytes", encode(self.to_cbor()))

    @property
    def signed_bytes(self) -> bytes:
        return self._signed_bytes

    @property
    def signing_input(self) -> bytes:
        return _SIG_TAG + self._signed_bytes

    @property
    def entry_id(self) -> bytes:
        """SHA-256 over the signed content only; see protocol §3.1 for why `sig` is excluded."""
        return hashlib.sha256(_ENTRY_TAG + self._signed_bytes).digest()

    def to_cbor(self) -> dict[str, CborValue]:
        return {
            "v": self.v,
            "author": self.author,
            "seq": self.seq,
            "prev": self.prev,
            "team": self.team,
            "channel": self.channel,
            "kind": self.kind.value,
            "refs": list(self.refs),
            "ts": self.ts,
            "body": self.body,
        }

    def sign(self, signer: Signer, handle: KeyHandle) -> "Entry":
        value = signer.sign(handle, self.signing_input)
        return Entry(self, Signature(SIG_ALG_ES256, value))

    def with_assertion(self, assertion: Assertion) -> "Entry":
        """Attach a security key's assertion over `signing_input` (protocol §3.2)."""
        return Entry(self, Signature.from_assertion(assertion))

    @classmethod
    def decode(cls, signed_bytes: bytes) -> "EntryContent":
        """Rebuild content from its `signed_bytes`, e.g. to sign it somewhere else."""
        return _content_from_cbor(_as_map(_decode(signed_bytes), "entry", _CONTENT_FIELDS))


@dataclass(frozen=True, slots=True)
class Entry:
    content: EntryContent
    sig: Signature

    @property
    def id(self) -> bytes:
        return self.content.entry_id

    @property
    def author(self) -> bytes:
        return self.content.author

    @property
    def seq(self) -> int:
        return self.content.seq

    def encode(self) -> bytes:
        return encode({**self.content.to_cbor(), "sig": self.sig.to_cbor()})

    @classmethod
    def decode(cls, raw: bytes) -> "Entry":
        fields = _as_map(_decode(raw), "entry", _CONTENT_FIELDS | {"sig"})
        return cls(_content_from_cbor(fields), Signature.from_cbor(fields["sig"]))


def _decode(raw: bytes) -> CborValue:
    try:
        return decode(raw)
    except CanonicalError as exc:
        raise EntryFormatError(str(exc)) from exc


def _content_from_cbor(fields: dict[str, CborValue]) -> EntryContent:
    prev = fields["prev"]
    return EntryContent(
        v=_as_uint(fields["v"], "v"),
        author=_as_bytes(fields["author"], "author", PUBLIC_KEY_SIZE),
        seq=_as_uint(fields["seq"], "seq"),
        prev=None if prev is None else _as_bytes(prev, "prev", ENTRY_ID_SIZE),
        team=_as_bytes(fields["team"], "team", TEAM_ID_SIZE),
        channel=_as_text(fields["channel"], "channel"),
        kind=_as_kind(fields["kind"]),
        refs=_as_refs(fields["refs"]),
        ts=_as_uint(fields["ts"], "ts"),
        body=_as_bytes(fields["body"], "body"),
    )


def _as_map(value: CborValue, name: str, expected: frozenset[str]) -> dict[str, CborValue]:
    if not isinstance(value, dict):
        raise EntryFormatError(f"{name} must be a map")
    if value.keys() != expected:
        raise EntryFormatError(f"{name} fields must be exactly {sorted(expected)}")
    return value


def _as_uint(value: CborValue, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EntryFormatError(f"{name} must be an unsigned integer")
    return value


def _as_bytes(value: CborValue, name: str, size: int | None = None) -> bytes:
    if not isinstance(value, bytes):
        raise EntryFormatError(f"{name} must be bytes")
    if size is not None and len(value) != size:
        raise EntryFormatError(f"{name} must be {size} bytes")
    return value


def _as_text(value: CborValue, name: str) -> str:
    if not isinstance(value, str):
        raise EntryFormatError(f"{name} must be text")
    return value


def _as_kind(value: CborValue) -> Kind:
    try:
        return Kind(_as_text(value, "kind"))
    except ValueError as exc:
        raise EntryFormatError(f"unknown kind {value!r}") from exc


def _as_refs(value: CborValue) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise EntryFormatError("refs must be an array")
    return tuple(_as_bytes(ref, "refs[]", ENTRY_ID_SIZE) for ref in value)

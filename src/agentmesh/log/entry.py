import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from agentmesh.identity import KeyHandle, Signer
from agentmesh.identity.es256 import PUBLIC_KEY_SIZE
from agentmesh.log.canonical import CanonicalError, CborValue, decode, encode

PROTOCOL_VERSION = 0
ENTRY_ID_SIZE = 32
TEAM_ID_SIZE = 16
SIG_ALG_ES256 = "es256"

_ENTRY_TAG = b"agentmesh/entry/v0\x00"
_SIG_TAG = b"agentmesh/sig/v0\x00"
_CONTENT_FIELDS = frozenset(
    {"v", "author", "seq", "prev", "team", "channel", "kind", "refs", "ts", "body"}
)
_SIG_FIELDS = frozenset({"alg", "value"})


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
        return encode(
            {**self.content.to_cbor(), "sig": {"alg": self.sig.alg, "value": self.sig.value}}
        )

    @classmethod
    def decode(cls, raw: bytes) -> "Entry":
        try:
            value = decode(raw)
        except CanonicalError as exc:
            raise EntryFormatError(str(exc)) from exc
        fields = _as_map(value, "entry", _CONTENT_FIELDS | {"sig"})
        sig = _as_map(fields["sig"], "sig", _SIG_FIELDS)
        prev = fields["prev"]
        content = EntryContent(
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
        signature = Signature(_as_text(sig["alg"], "sig.alg"), _as_bytes(sig["value"], "sig.value"))
        return cls(content, signature)


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

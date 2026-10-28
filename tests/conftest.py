from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.body import MessageBody, MessageType
from agentmesh.log.entry import Entry, EntryContent, Kind
from agentmesh.log.envelope import Envelope

TEAM = bytes(range(16))

ContentFactory = Callable[..., EntryContent]


def plain_message(summary: str = "hello") -> bytes:
    body = MessageBody(MessageType.FYI, ("#backend",), summary)
    return Envelope.plain(body.encode()).encode()


@pytest.fixture
def message() -> Callable[[str], bytes]:
    return plain_message


@pytest.fixture(scope="session")
def signer(tmp_path_factory: pytest.TempPathFactory) -> SoftwareSigner:
    return SoftwareSigner(tmp_path_factory.mktemp("keys"), b"test passphrase")


@pytest.fixture(scope="session")
def handle(signer: SoftwareSigner) -> KeyHandle:
    return signer.create_key("device")


@pytest.fixture(scope="session")
def other_handle(signer: SoftwareSigner) -> KeyHandle:
    return signer.create_key("other-device")


@pytest.fixture
def team() -> bytes:
    return TEAM


@pytest.fixture
def make_content(signer: SoftwareSigner, handle: KeyHandle) -> ContentFactory:
    author = signer.public_key(handle)

    def factory(**overrides: Any) -> EntryContent:
        base = EntryContent(
            author=author,
            seq=1,
            prev=None,
            team=TEAM,
            channel="#backend",
            kind=Kind.MESSAGE,
            refs=(),
            ts=1_700_000_000_000,
            body=plain_message(),
        )
        return replace(base, **overrides)

    return factory


@pytest.fixture
def make_entry(
    make_content: ContentFactory, signer: SoftwareSigner, handle: KeyHandle
) -> Callable[..., Entry]:
    def factory(**overrides: Any) -> Entry:
        return make_content(**overrides).sign(signer, handle)

    return factory


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "agentmesh.db"

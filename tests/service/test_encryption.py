from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from agentmesh.log.authorize import Verdict
from agentmesh.log.entry import Entry, EntryContent, Kind, Signature
from agentmesh.service.device import Device

PASSPHRASE = b"secret passphrase"


class Team:
    def __init__(self, root: Path, stack: ExitStack) -> None:
        self.shared = root / "shared"
        self.gef = stack.enter_context(
            Device.create_team(root / "gef", "gef", PASSPHRASE, exchange=self.shared)
        )
        self.tobi = stack.enter_context(
            Device.join(root / "tobi", self.gef.invite, "tobi", PASSPHRASE, exchange=self.shared)
        )
        self.lee = stack.enter_context(
            Device.join(root / "lee", self.gef.invite, "lee", PASSPHRASE, exchange=self.shared)
        )
        self.all = (self.gef, self.tobi, self.lee)
        self.sync()
        self.gef.call("roster.add", {"handle": "tobi", "key": self.tobi.author.hex()})
        self.gef.call(
            "roster.add",
            {"handle": "lee", "key": self.lee.author.hex(), "channels": ["#mobile"]},
        )
        self.sync()

    def sync(self) -> None:
        for _ in range(3):
            for device in self.all:
                device.sync()


@pytest.fixture
def team(tmp_path: Path) -> Iterator[Team]:
    with ExitStack() as stack:
        yield Team(tmp_path, stack)


def _post(device: Device, to: str, summary: str) -> str:
    return device.call("post", {"to": [to], "type": "fyi", "summary": summary}).split()[1]


def test_nothing_readable_is_written_to_the_shared_folder(team: Team) -> None:
    _post(team.tobi, "#general", "launch codes are 1234")
    team.sync()
    files = list(team.shared.rglob("*.entry"))
    assert files
    assert not any(b"launch codes" in path.read_bytes() for path in files)
    assert "launch codes are 1234" in team.gef.call("inbox", {})


def test_you_can_read_your_own_messages(team: Team) -> None:
    message_id = _post(team.tobi, "#general", "note to self")
    assert '"note to self"' in team.tobi.call("read", {"id": message_id})


def test_members_cannot_read_channels_they_are_not_in(team: Team) -> None:
    _post(team.tobi, "#backend", "backend only")
    mobile = _post(team.tobi, "#mobile", "mobile news")
    team.sync()
    inbox = team.lee.call("inbox", {})
    assert "backend only" not in inbox
    assert mobile in inbox


def test_removed_member_cannot_read_later_messages(team: Team) -> None:
    before = _post(team.gef, "tobi", "before removal")
    team.sync()
    assert before in team.tobi.call("inbox", {})
    team.gef.call("roster.remove", {"handle": "tobi"})
    team.sync()
    after = _post(team.gef, "#general", "after removal")
    team.sync()
    assert after not in team.tobi.call("inbox", {"since": 0})
    assert '"before removal"' in team.tobi.call("read", {"id": before})


def test_plaintext_is_denied_when_encryption_is_required(
    team: Team, make_content: Callable[..., EntryContent], message: Callable[[str], bytes]
) -> None:
    assert team.gef.roster.current is not None
    assert team.gef.roster.current.require_encryption
    content = make_content(author=team.tobi.author, kind=Kind.MESSAGE, body=message("hi"))
    entry = Entry(content, Signature("es256", bytes(64)))
    decision = team.gef.roster.authorize(entry)
    assert decision.verdict is Verdict.DENY
    assert "encrypted" in decision.reason

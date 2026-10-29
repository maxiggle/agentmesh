from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import EntryContent, Kind
from agentmesh.service.api import ApiError
from agentmesh.service.device import Device
from agentmesh.service.invite import Invite
from security_key_emulator import SoftwareAuthenticator

PASSPHRASE = b"team passphrase"


class Team:
    def __init__(self, root: Path, stack: ExitStack) -> None:
        self.root = root
        self.shared = root / "shared"
        self.stack = stack
        self.admin = stack.enter_context(
            Device.create_team(root / "gef", "gef", PASSPHRASE, exchange=self.shared)
        )
        self.devices = {"gef": self.admin}

    def join(self, handle: str, invite: Invite | None = None) -> Device:
        device = self.stack.enter_context(
            Device.join(
                self.root / handle,
                invite or self.admin.invite,
                handle,
                PASSPHRASE,
                exchange=self.shared,
            )
        )
        self.devices[handle] = device
        return device

    def add(self, handle: str) -> None:
        device = self.devices[handle]
        self.admin.call("roster.add", {"handle": handle, "key": device.author.hex()})

    def sync(self, *handles: str) -> None:
        for _ in range(2):
            for handle in handles or tuple(self.devices):
                self.devices[handle].sync()


@pytest.fixture
def team(tmp_path: Path) -> Iterator[Team]:
    with ExitStack() as stack:
        team = Team(tmp_path, stack)
        for handle in ("tobi", "lee", "ana"):
            team.join(handle)
        team.sync()
        for handle in ("tobi", "lee", "ana"):
            team.add(handle)
        team.sync()
        yield team


def _post(device: Device, to: str, summary: str) -> str:
    return device.call("post", {"to": [to], "type": "fyi", "summary": summary}).split()[1]


def test_team_of_four_sees_each_other_by_name(team: Team) -> None:
    roster = team.devices["ana"].call("roster.show", {})
    assert roster.splitlines()[0].endswith("roster v4")
    message_id = _post(team.devices["tobi"], "#general", "standup in 5")
    team.sync()
    for handle in ("gef", "lee", "ana"):
        inbox = team.devices[handle].call("inbox", {})
        assert f'{message_id} · tobi · fyi · "standup in 5"' in inbox


def test_offline_device_catches_up(team: Team) -> None:
    ids = [_post(team.devices["tobi"], "ana", f"note {i}") for i in range(3)]
    team.sync("gef", "tobi", "lee")
    assert team.devices["ana"].call("digest", {}) == "3 new"
    inbox = team.devices["ana"].call("inbox", {})
    assert [line.split()[0] for line in inbox.splitlines()[1:-1]] == ids


def test_strangers_are_never_shown(team: Team, message: Callable[[str], bytes]) -> None:
    stranger = team.join("mallory")
    with pytest.raises(ApiError, match="cannot post"):
        _post(stranger, "#general", "hello")
    signer = SoftwareSigner(team.root / "mallory" / "keys", PASSPHRASE)
    key = KeyHandle(SoftwareSigner.backend, "device")
    content = EntryContent(
        author=stranger.author,
        seq=1,
        prev=None,
        team=team.admin.invite.team,
        channel="#general",
        kind=Kind.MESSAGE,
        refs=(),
        ts=1,
        body=message("click this link"),
    )
    forged = content.sign(signer, key)
    folder = team.shared / stranger.author.hex()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"0000000001-{forged.id.hex()}.entry").write_bytes(forged.encode())
    genuine = _post(team.devices["tobi"], "#general", "real news")
    team.sync()
    for handle in ("gef", "lee", "ana"):
        inbox = team.devices[handle].call("inbox", {})
        assert genuine in inbox
        assert "click this link" not in inbox


def test_removed_member_is_cut_off(team: Team) -> None:
    team.admin.call("roster.remove", {"handle": "lee"})
    team.sync()
    with pytest.raises(ApiError, match="cannot post"):
        _post(team.devices["lee"], "#general", "am I still here?")
    assert "lee" not in team.devices["ana"].call("roster.show", {})


def test_teammates_accept_a_roster_signed_with_a_security_key(team: Team) -> None:
    key = SoftwareAuthenticator()
    team.admin.call("roster.enroll", {"key": key.public_key.hex(), "label": "yubikey"})
    draft = team.admin.call(
        "roster.remove", {"handle": "ana", "security_key": key.public_key.hex()}
    )
    assertion = key.sign(EntryContent.decode(bytes.fromhex(draft)).signing_input)
    team.admin.call(
        "roster.submit",
        {
            "content": draft,
            "auth_data": assertion.auth_data.hex(),
            "signature": assertion.signature.hex(),
        },
    )
    team.sync()
    for handle in ("tobi", "lee", "ana"):
        roster = team.devices[handle].roster.current
        assert roster is not None
        assert roster.version == 6, handle
        assert [m.handle for m in roster.members] == ["gef", "lee", "tobi"]

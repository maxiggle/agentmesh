from collections.abc import Callable

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.authorize import Verdict
from agentmesh.log.entry import Entry, EntryContent, Kind
from agentmesh.log.roster import Member, Roster, RosterDevice, Trust
from agentmesh.roster.book import RosterBook


class Team:
    def __init__(
        self,
        signer: SoftwareSigner,
        make_content: Callable[..., EntryContent],
        handles: dict[str, KeyHandle],
    ) -> None:
        self.signer = signer
        self.make_content = make_content
        self.handles = handles
        self.keys = {name: signer.public_key(handle) for name, handle in handles.items()}

    def member(self, name: str, channels: tuple[str, ...] = ("*",)) -> Member:
        device = RosterDevice(self.keys[name], Trust.SOFTWARE, name)
        return Member(name, (), channels, (device,))

    def roster_entry(self, by: str, roster: Roster, seq: int = 1) -> Entry:
        content = self.make_content(
            author=self.keys[by],
            seq=seq,
            prev=None if seq == 1 else b"\x00" * 32,
            kind=Kind.ROSTER,
            channel="_team",
            body=roster.encode(),
        )
        return content.sign(self.signer, self.handles[by])

    def message(self, by: str, channel: str = "#backend") -> Entry:
        content = self.make_content(author=self.keys[by], channel=channel)
        return content.sign(self.signer, self.handles[by])


@pytest.fixture
def team(
    signer: SoftwareSigner,
    make_content: Callable[..., EntryContent],
    handle: KeyHandle,
    other_handle: KeyHandle,
    third_handle: KeyHandle,
) -> Team:
    return Team(signer, make_content, {"gef": handle, "tobi": other_handle, "lee": third_handle})


@pytest.fixture
def genesis(team: Team) -> tuple[Entry, Roster]:
    roster = Roster(1, None, "Acme", ("gef",), (team.member("gef"),))
    return team.roster_entry("gef", roster), roster


def _add(
    team: Team, entry: Entry, roster: Roster, *names: str, by: str = "gef"
) -> tuple[Entry, Roster]:
    members = (*roster.members, *(team.member(name) for name in names))
    nxt = roster.successor(entry.id, members=members)
    return team.roster_entry(by, nxt, seq=nxt.version), nxt


def test_no_roster_means_wait(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    assert book.current is None
    assert book.authorize(team.message("gef")).verdict is Verdict.WAIT


def test_genesis_must_be_pinned(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(b"\x00" * 32)
    decision = book.authorize(genesis[0])
    assert decision.verdict is Verdict.DENY
    assert "pinned" in decision.reason
    assert not book.stored(genesis[0])


def test_genesis_must_be_signed_by_its_admin(team: Team) -> None:
    roster = Roster(1, None, "Acme", ("gef",), (team.member("gef"),))
    entry = team.roster_entry("tobi", roster)
    assert RosterBook(entry.id).authorize(entry).verdict is Verdict.DENY


def test_members_are_authorized(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    assert book.stored(genesis[0])
    assert book.current == genesis[1]
    assert book.authorize(team.message("gef")).verdict is Verdict.ALLOW
    assert book.authorize(team.message("tobi")).verdict is Verdict.WAIT
    second, roster = _add(team, *genesis, "tobi")
    assert book.authorize(second).verdict is Verdict.ALLOW
    assert book.stored(second)
    assert book.current == roster
    assert book.authorize(team.message("tobi")).verdict is Verdict.ALLOW
    assert book.member_for_key(team.keys["tobi"]) == team.member("tobi")


def test_only_admins_change_the_roster(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    book.stored(genesis[0])
    second, roster = _add(team, *genesis, "tobi")
    book.stored(second)
    by_member = team.roster_entry(
        "tobi", roster.successor(second.id, members=(*roster.members, team.member("lee"))), 3
    )
    decision = book.authorize(by_member)
    assert decision.verdict is Verdict.DENY
    assert "admin" in decision.reason


def test_out_of_order_rosters_wait(team: Team, genesis: tuple[Entry, Roster]) -> None:
    second, _ = _add(team, *genesis, "tobi")
    book = RosterBook(genesis[0].id)
    assert book.authorize(second).verdict is Verdict.WAIT
    book.load([second, genesis[0]])
    assert book.current is not None
    assert book.current.version == 2


def test_version_must_follow(team: Team, genesis: tuple[Entry, Roster]) -> None:
    entry, roster = genesis
    skipped = Roster(3, entry.id, "Acme", ("gef",), roster.members)
    book = RosterBook(entry.id)
    book.stored(entry)
    assert book.authorize(team.roster_entry("gef", skipped, 3)).verdict is Verdict.DENY


def test_fork_freezes_until_resolved(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    book.stored(genesis[0])
    with_tobi, tobi_roster = _add(team, *genesis, "tobi")
    with_lee, _ = _add(team, *genesis, "lee")
    book.stored(with_tobi)
    assert book.current == tobi_roster
    assert book.stored(with_lee)
    assert book.forked
    assert book.current == genesis[1]
    assert book.authorize(team.message("tobi")).verdict is Verdict.WAIT
    resolved, roster = _add(team, with_tobi, tobi_roster, "lee")
    assert book.stored(resolved)
    assert not book.forked
    assert book.current == roster


@pytest.mark.parametrize(
    ("channels", "channel", "verdict"),
    [
        (("*",), "#backend", Verdict.ALLOW),
        (("#mobile",), "#mobile", Verdict.ALLOW),
        (("#mobile",), "#backend", Verdict.WAIT),
        (("#mobile",), "dm:gef+tobi", Verdict.ALLOW),
        (("#mobile",), "dm:gef+lee", Verdict.WAIT),
        (("#mobile",), "_team", Verdict.ALLOW),
    ],
)
def test_channel_permissions(
    team: Team,
    genesis: tuple[Entry, Roster],
    channels: tuple[str, ...],
    channel: str,
    verdict: Verdict,
) -> None:
    entry, roster = genesis
    nxt = roster.successor(entry.id, members=(*roster.members, team.member("tobi", channels)))
    book = RosterBook(entry.id)
    book.load([entry, team.roster_entry("gef", nxt, 2)])
    content = team.make_content(
        author=team.keys["tobi"],
        channel=channel,
        kind=Kind.KEY_ANNOUNCE if channel == "_team" else Kind.MESSAGE,
    )
    message = content.sign(team.signer, team.handles["tobi"])
    assert book.authorize(message).verdict is verdict


def test_non_roster_entries_do_not_change_state(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    book.stored(genesis[0])
    assert not book.stored(team.message("gef"))
    assert not book.stored(genesis[0])

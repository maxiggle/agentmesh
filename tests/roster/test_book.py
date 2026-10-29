from collections.abc import Callable
from dataclasses import replace

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.authorize import Verdict
from agentmesh.log.entry import Entry, EntryContent, Kind, Signature
from agentmesh.log.envelope import Encryption, Envelope
from agentmesh.log.roster import Member, RelayInfo, Roster, RosterDevice, Trust
from agentmesh.roster.book import RosterBook
from security_key_emulator import SoftwareAuthenticator


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
    assert not book.accepted(genesis[0])


def test_genesis_must_be_signed_by_its_admin(team: Team) -> None:
    roster = Roster(1, None, "Acme", ("gef",), (team.member("gef"),))
    entry = team.roster_entry("tobi", roster)
    assert RosterBook(entry.id).authorize(entry).verdict is Verdict.DENY


def test_members_are_authorized(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    assert book.accepted(genesis[0])
    assert book.current == genesis[1]
    assert book.authorize(team.message("gef")).verdict is Verdict.ALLOW
    assert book.authorize(team.message("tobi")).verdict is Verdict.WAIT
    second, roster = _add(team, *genesis, "tobi")
    assert book.authorize(second).verdict is Verdict.ALLOW
    assert book.accepted(second)
    assert book.current == roster
    assert book.authorize(team.message("tobi")).verdict is Verdict.ALLOW
    assert book.member_for_key(team.keys["tobi"]) == team.member("tobi")


def test_only_admins_change_the_roster(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    book.accepted(genesis[0])
    second, roster = _add(team, *genesis, "tobi")
    book.accepted(second)
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
    book.accepted(entry)
    assert book.authorize(team.roster_entry("gef", skipped, 3)).verdict is Verdict.DENY


def test_fork_freezes_until_resolved(team: Team, genesis: tuple[Entry, Roster]) -> None:
    book = RosterBook(genesis[0].id)
    book.accepted(genesis[0])
    with_tobi, tobi_roster = _add(team, *genesis, "tobi")
    with_lee, _ = _add(team, *genesis, "lee")
    book.accepted(with_tobi)
    assert book.current == tobi_roster
    assert book.accepted(with_lee)
    assert book.forked
    assert book.current == genesis[1]
    assert book.authorize(team.message("tobi")).verdict is Verdict.WAIT
    resolved, roster = _add(team, with_tobi, tobi_roster, "lee")
    assert book.accepted(resolved)
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
    book.accepted(genesis[0])
    assert not book.accepted(team.message("gef"))
    assert not book.accepted(genesis[0])


def test_plaintext_is_denied_when_the_roster_requires_encryption(team: Team) -> None:
    roster = Roster(1, None, "Acme", ("gef",), (team.member("gef"),), require_encryption=True)
    entry = team.roster_entry("gef", roster)
    book = RosterBook(entry.id)
    book.accepted(entry)
    decision = book.authorize(team.message("gef"))
    assert decision.verdict is Verdict.DENY
    assert "encrypted" in decision.reason
    sealed = Envelope(
        Encryption.SEALED,
        1,
        b"cipher",
        ephemeral=b"\x05" * 32,
        keys=((b"\x01" * 8, b"\x02" * 48),),
        nonce=b"\x00" * 12,
    )
    content = team.make_content(author=team.keys["gef"], body=sealed.encode())
    encrypted = content.sign(team.signer, team.handles["gef"])
    assert book.authorize(encrypted).verdict is Verdict.ALLOW


def test_members_and_relays_may_receive_logs(team: Team, genesis: tuple[Entry, Roster]) -> None:
    entry, roster = genesis
    relay_key = team.keys["lee"]
    book = RosterBook(entry.id)
    assert not book.may_receive_logs(team.keys["gef"])
    book.accepted(entry)
    assert book.may_receive_logs(team.keys["gef"])
    assert not book.may_receive_logs(relay_key)
    with_relay = roster.successor(entry.id, relays=(RelayInfo(relay_key, "relay:7447"),))
    book.accepted(team.roster_entry("gef", with_relay, 2))
    assert book.may_receive_logs(relay_key)
    assert not book.may_receive_logs(team.keys["tobi"])


def _enroll(
    team: Team,
    entry: Entry,
    roster: Roster,
    key: SoftwareAuthenticator,
    *,
    handle: str = "gef",
    trust: Trust = Trust.SECURITY_KEY,
) -> tuple[Entry, Roster]:
    member = roster.member(handle)
    assert member is not None
    device = RosterDevice(key.public_key, trust, "yubikey")
    devices = tuple(sorted((*member.devices, device), key=lambda d: d.key))
    members = (*(m for m in roster.members if m.handle != handle), replace(member, devices=devices))
    nxt = roster.successor(entry.id, members=members)
    return team.roster_entry("gef", nxt, seq=nxt.version), nxt


def _signed_by(team: Team, key: SoftwareAuthenticator, roster: Roster, seq: int = 1) -> Entry:
    content = team.make_content(
        author=key.public_key,
        seq=seq,
        prev=None if seq == 1 else b"\x00" * 32,
        kind=Kind.ROSTER,
        channel="_team",
        body=roster.encode(),
    )
    return content.with_assertion(key.sign(content.signing_input))


def _book(*entries: Entry) -> RosterBook:
    book = RosterBook(entries[0].id)
    for entry in entries:
        assert book.accepted(entry)
    return book


def test_admin_signs_rosters_with_an_enrolled_security_key(
    team: Team, genesis: tuple[Entry, Roster]
) -> None:
    key = SoftwareAuthenticator()
    enrolled, roster = _enroll(team, *genesis, key)
    book = _book(genesis[0], enrolled)
    nxt = roster.successor(enrolled.id, members=(*roster.members, team.member("tobi")))
    by_key = _signed_by(team, key, nxt)
    assert book.authorize(by_key).verdict is Verdict.ALLOW
    assert book.accepted(by_key)
    assert book.current == nxt


def test_admin_with_a_security_key_cannot_use_the_device_key(
    team: Team, genesis: tuple[Entry, Roster]
) -> None:
    enrolled, roster = _enroll(team, *genesis, SoftwareAuthenticator())
    book = _book(genesis[0], enrolled)
    nxt = roster.successor(enrolled.id, members=(*roster.members, team.member("tobi")))
    decision = book.authorize(team.roster_entry("gef", nxt, seq=3))
    assert decision.verdict is Verdict.DENY
    assert decision.reason == "gef must sign roster changes with a security key"


def test_a_security_key_cannot_enroll_itself(team: Team, genesis: tuple[Entry, Roster]) -> None:
    key = SoftwareAuthenticator()
    _, roster = _enroll(team, *genesis, key)
    decision = _book(genesis[0]).authorize(_signed_by(team, key, roster))
    assert decision.verdict is Verdict.DENY
    assert "admin" in decision.reason


def test_a_non_admin_security_key_cannot_change_the_roster(
    team: Team, genesis: tuple[Entry, Roster]
) -> None:
    second, roster = _add(team, *genesis, "tobi")
    key = SoftwareAuthenticator()
    enrolled, roster = _enroll(team, second, roster, key, handle="tobi")
    book = _book(genesis[0], second, enrolled)
    nxt = roster.successor(enrolled.id, members=(*roster.members, team.member("lee")))
    decision = book.authorize(_signed_by(team, key, nxt))
    assert decision.verdict is Verdict.DENY
    assert "admin" in decision.reason


def test_a_security_key_cannot_post_messages(team: Team, genesis: tuple[Entry, Roster]) -> None:
    key = SoftwareAuthenticator()
    enrolled, _ = _enroll(team, *genesis, key)
    content = team.make_content(author=key.public_key)
    message = content.with_assertion(key.sign(content.signing_input))
    decision = _book(genesis[0], enrolled).authorize(message)
    assert decision.verdict is Verdict.DENY
    assert "only sign roster changes" in decision.reason


def test_a_security_key_device_must_use_es256_fido2(
    team: Team, genesis: tuple[Entry, Roster]
) -> None:
    key = SoftwareAuthenticator()
    enrolled, roster = _enroll(team, *genesis, key)
    signed = _signed_by(team, key, roster.successor(enrolled.id))
    plain = replace(signed, sig=Signature("es256", bytes(64)))
    decision = _book(genesis[0], enrolled).authorize(plain)
    assert decision.verdict is Verdict.DENY
    assert "es256-fido2" in decision.reason


def test_es256_fido2_needs_a_device_marked_security_key(
    team: Team, genesis: tuple[Entry, Roster]
) -> None:
    key = SoftwareAuthenticator()
    enrolled, roster = _enroll(team, *genesis, key, trust=Trust.HARDWARE)
    decision = _book(genesis[0], enrolled).authorize(
        _signed_by(team, key, roster.successor(enrolled.id))
    )
    assert decision.verdict is Verdict.DENY
    assert "es256-fido2" in decision.reason


def test_genesis_admin_with_a_security_key_must_sign_with_it(team: Team) -> None:
    key = SoftwareAuthenticator()
    device = RosterDevice(key.public_key, Trust.SECURITY_KEY, "yubikey")
    gef = team.member("gef")
    devices = tuple(sorted((*gef.devices, device), key=lambda d: d.key))
    roster = Roster(1, None, "Acme", ("gef",), (replace(gef, devices=devices),))
    by_device = team.roster_entry("gef", roster)
    assert RosterBook(by_device.id).authorize(by_device).verdict is Verdict.DENY
    by_key = _signed_by(team, key, roster)
    assert RosterBook(by_key.id).authorize(by_key).verdict is Verdict.ALLOW

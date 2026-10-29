from collections.abc import Callable

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import EntryContent, Kind
from agentmesh.log.roster import Member, Roster, RosterDevice, Trust
from agentmesh.roster.book import RosterBook
from agentmesh.service.directory import RosterDirectory
from agentmesh.service.messenger import Identity


def test_directory_follows_the_roster(
    signer: SoftwareSigner,
    handle: KeyHandle,
    other_handle: KeyHandle,
    make_content: Callable[..., EntryContent],
) -> None:
    gef_key = signer.public_key(handle)
    tobi_key = signer.public_key(other_handle)
    roster = Roster(
        1,
        None,
        "Acme",
        ("gef",),
        (Member("gef", ("@backend",), ("*",), (RosterDevice(gef_key, Trust.SOFTWARE, "mac"),)),),
    )
    entry = make_content(kind=Kind.ROSTER, channel="_team", body=roster.encode()).sign(
        signer, handle
    )
    book = RosterBook(entry.id)
    tobi = RosterDirectory(book, tobi_key, "tobi")
    assert tobi.me() == Identity("tobi", (), ())
    assert tobi.name(gef_key) is None
    assert tobi.known_authors() == ()

    book.accepted(entry)
    gef = RosterDirectory(book, gef_key, "ignored-handle")
    assert gef.me() == Identity("gef", ("@backend",), ("*",))
    assert tobi.name(gef_key) == "gef"
    assert tobi.name(tobi_key) is None
    assert tobi.known_authors() == (gef_key,)


def test_security_keys_are_not_listed_as_teammates(
    signer: SoftwareSigner, handle: KeyHandle, make_content: Callable[..., EntryContent]
) -> None:
    gef_key = signer.public_key(handle)
    security_key = b"\x02" + b"\x00" * 32
    laptop = b"\x03" + b"\xff" * 32
    lee = Member(
        "lee",
        (),
        ("*",),
        (
            RosterDevice(security_key, Trust.SECURITY_KEY, "yubikey"),
            RosterDevice(laptop, Trust.SOFTWARE, "laptop"),
        ),
    )
    gef = Member("gef", (), ("*",), (RosterDevice(gef_key, Trust.SOFTWARE, "mac"),))
    roster = Roster(1, None, "Acme", ("gef",), (gef, lee))
    entry = make_content(kind=Kind.ROSTER, channel="_team", body=roster.encode()).sign(
        signer, handle
    )
    book = RosterBook(entry.id)
    book.accepted(entry)
    assert RosterDirectory(book, gef_key, "gef").known_authors() == (gef_key, laptop)

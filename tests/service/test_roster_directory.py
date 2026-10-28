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

    book.stored(entry)
    gef = RosterDirectory(book, gef_key, "ignored-handle")
    assert gef.me() == Identity("gef", ("@backend",), ("*",))
    assert tobi.name(gef_key) == "gef"
    assert tobi.name(tobi_key) is None
    assert tobi.known_authors() == (gef_key,)

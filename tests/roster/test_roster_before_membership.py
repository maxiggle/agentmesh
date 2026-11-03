from collections.abc import Callable
from pathlib import Path

from ehor.identity import KeyHandle
from ehor.identity.software import SoftwareSigner
from ehor.log.entry import Entry, Kind
from ehor.log.names import CONTROL_CHANNEL
from ehor.log.roster import Member, Roster, RosterDevice, Trust
from ehor.log.verify import EntryVerifier
from ehor.roster.book import RosterBook
from ehor.store.ingest import Ingestor, Outcome
from ehor.store.local_log import LocalLog
from ehor.store.sqlite import SqliteStore


def _member(handle: str, key: bytes) -> Member:
    return Member(handle, (), ("*",), (RosterDevice(key, Trust.SOFTWARE, handle),))


def test_roster_counts_while_its_log_has_a_gap(
    tmp_path: Path,
    signer: SoftwareSigner,
    handle: KeyHandle,
    other_handle: KeyHandle,
    team: bytes,
    message: Callable[[str], bytes],
) -> None:
    verifier = EntryVerifier(team)
    admin_store = SqliteStore(tmp_path / "admin.db")
    admin_log = LocalLog(admin_store, signer, handle, team, verifier)
    tobi_key = signer.public_key(other_handle)
    genesis_roster = Roster(1, None, "Acme", ("gef",), (_member("gef", admin_log.author),))
    genesis = admin_log.append(CONTROL_CHANNEL, Kind.ROSTER, genesis_roster.encode())
    admin_log.append("#backend", Kind.MESSAGE, message("private to members"))
    added = genesis_roster.successor(
        genesis.id, members=(*genesis_roster.members, _member("tobi", tobi_key))
    )
    second: Entry = admin_log.append(CONTROL_CHANNEL, Kind.ROSTER, added.encode())

    tobi_store = SqliteStore(tmp_path / "tobi.db")
    book = RosterBook(genesis.id)
    ingestor = Ingestor(tobi_store, verifier, book)
    assert ingestor.receive(genesis.encode()).outcome is Outcome.STORED
    assert ingestor.receive(second.encode()).outcome is Outcome.PARKED
    assert book.current == added
    assert book.member_for_key(tobi_key) is not None

    reloaded = RosterBook(genesis.id)
    reloaded.load([genesis, *(e for e in tobi_store.all_parked() if e.content.kind is Kind.ROSTER)])
    assert reloaded.current == added
    admin_store.close()
    tobi_store.close()

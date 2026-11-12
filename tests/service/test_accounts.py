from collections.abc import Callable
from pathlib import Path

from ehor.identity import KeyHandle
from ehor.identity.software import SoftwareSigner
from ehor.log.account_announce import AccountAnnounce
from ehor.log.canonical import encode
from ehor.log.entry import Entry, Kind
from ehor.log.verify import EntryVerifier
from ehor.service.accounts import AccountDirectory
from ehor.store.local_log import LocalLog
from ehor.store.sqlite import SqliteStore


def test_directory_keeps_the_latest_announce_per_system(
    tmp_db: Path, signer: SoftwareSigner, handle: KeyHandle, team: bytes
) -> None:
    store = SqliteStore(tmp_db)
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team))
    first = AccountAnnounce("near-testnet", "old.testnet", b"\x02" * 33)
    second = AccountAnnounce("near-testnet", "new.testnet", b"\x03" * 33)
    other = AccountAnnounce("github", "tobi")
    for announced in (first, other, second):
        local_log.append("_team", Kind.ACCOUNT_ANNOUNCE, announced.encode())
    directory = AccountDirectory(store)
    assert directory.accounts("near-testnet") == {local_log.author: second}
    assert directory.accounts("github") == {local_log.author: other}
    assert directory.accounts("none") == {}
    store.close()


def test_directory_skips_undecodable_bodies(tmp_db: Path, make_entry: Callable[..., Entry]) -> None:
    store = SqliteStore(tmp_db)
    good = make_entry(
        kind=Kind.ACCOUNT_ANNOUNCE,
        channel="_team",
        body=AccountAnnounce("near-testnet", "t.testnet").encode(),
    )
    bad = make_entry(
        seq=2,
        prev=good.id,
        kind=Kind.ACCOUNT_ANNOUNCE,
        channel="_team",
        body=encode({"system": "near-testnet"}),
    )
    store.append(good)
    store.append(bad)
    assert AccountDirectory(store).accounts("near-testnet") == {
        good.author: AccountAnnounce("near-testnet", "t.testnet")
    }
    store.close()

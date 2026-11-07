import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from ehor.identity import KeyHandle
from ehor.identity.software import SoftwareSigner
from ehor.log.verify import EntryVerifier
from ehor.service.alerts import MAX_ALERTS_PER_CHECK, AlertWatcher, MessengerFeed
from ehor.service.messenger import Identity, Messenger, StaticDirectory
from ehor.service.notify import Alert, NotifyError
from ehor.store.ingest import Ingestor
from ehor.store.local_log import LocalLog
from ehor.store.sqlite import SqliteStore

TEAM = b"\x06" * 16


class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.shown: list[Alert] = []
        self._fail = fail

    def notify(self, alert: Alert) -> None:
        self.shown.append(alert)
        if self._fail:
            raise NotifyError("no display")


class Peer:
    def __init__(self, path: Path, signer: SoftwareSigner, key: KeyHandle, me: Identity) -> None:
        self.store = SqliteStore(path)
        verifier = EntryVerifier(TEAM)
        self.log = LocalLog(self.store, signer, key, TEAM, verifier)
        self.messenger = Messenger(self.store, self.log, StaticDirectory(me))
        self._ingestor = Ingestor(self.store, verifier)

    def name(self, other: "Peer", handle: str) -> None:
        directory = StaticDirectory(self.messenger.me, {other.log.author: handle})
        self.messenger = Messenger(self.store, self.log, directory)

    def pull(self, other: "Peer") -> None:
        for arrival in other.store.arrivals():
            self._ingestor.receive(arrival.entry.encode())


@pytest.fixture
def peers(
    tmp_path: Path, signer: SoftwareSigner, handle: KeyHandle, other_handle: KeyHandle
) -> Iterator[tuple[Peer, Peer]]:
    gef = Peer(tmp_path / "gef.db", signer, handle, Identity("gef"))
    tobi = Peer(tmp_path / "tobi.db", signer, other_handle, Identity("tobi"))
    tobi.name(gef, "gef")
    yield gef, tobi
    gef.store.close()
    tobi.store.close()


def _check(watcher: AlertWatcher) -> int:
    return asyncio.run(watcher.check())


def _texts(alerts: Sequence[Alert]) -> list[tuple[str, str]]:
    return [(alert.sender, alert.summary) for alert in alerts]


def test_new_message_notifies_once(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    gef.messenger.post(["tobi"], "question", "Is the sync queue done?", detail="secret plan")
    tobi.pull(gef)
    assert _check(watcher) == 1
    assert _check(watcher) == 0
    assert _texts(notifier.shown) == [("gef", "Is the sync queue done?")]


def test_messages_from_before_the_start_do_not_notify(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    gef.messenger.post(["tobi"], "fyi", "old news")
    tobi.pull(gef)
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    assert _check(watcher) == 0
    assert notifier.shown == []


def test_own_messages_do_not_notify(peers: tuple[Peer, Peer]) -> None:
    _, tobi = peers
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    tobi.messenger.post(["#general"], "fyi", "my own update")
    assert _check(watcher) == 0
    assert notifier.shown == []


def test_messages_already_read_do_not_notify(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    gef.messenger.post(["tobi"], "fyi", "read before the check")
    tobi.pull(gef)
    tobi.messenger.inbox()
    assert _check(watcher) == 0


def test_messages_for_someone_else_do_not_notify(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    gef.messenger.post(["lee"], "fyi", "a dm for lee")
    tobi.pull(gef)
    assert _check(watcher) == 0


def test_checking_leaves_messages_unread(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), FakeNotifier())
    gef.messenger.post(["tobi"], "fyi", "still unread")
    tobi.pull(gef)
    _check(watcher)
    assert len(tobi.messenger.inbox().items) == 1


def test_a_burst_is_summed_up(peers: tuple[Peer, Peer]) -> None:
    gef, tobi = peers
    notifier = FakeNotifier()
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    for i in range(MAX_ALERTS_PER_CHECK + 2):
        gef.messenger.post(["tobi"], "fyi", f"update {i}")
    tobi.pull(gef)
    assert _check(watcher) == MAX_ALERTS_PER_CHECK + 2
    assert _texts(notifier.shown) == [
        *(("gef", f"update {i}") for i in range(MAX_ALERTS_PER_CHECK)),
        ("team", "2 more new messages"),
    ]


def test_a_failing_notifier_does_not_stop_the_watcher(
    peers: tuple[Peer, Peer], caplog: pytest.LogCaptureFixture
) -> None:
    gef, tobi = peers
    notifier = FakeNotifier(fail=True)
    watcher = AlertWatcher(MessengerFeed(tobi.messenger), notifier)
    gef.messenger.post(["tobi"], "fyi", "one")
    gef.messenger.post(["tobi"], "fyi", "two")
    tobi.pull(gef)
    assert _check(watcher) == 2
    assert len(notifier.shown) == 2
    assert "no display" in caplog.text
    assert _check(watcher) == 0

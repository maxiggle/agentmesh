import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from ehor.service.messenger import Messenger
from ehor.service.notify import Alert, Notifier, NotifyError

MAX_ALERTS_PER_CHECK = 3
_log = logging.getLogger(__name__)


class MessageFeed(Protocol):
    def latest_arrival(self) -> int: ...

    def alerts_after(self, position: int) -> tuple[Sequence[Alert], int]:
        """Alerts for messages that arrived after `position`, and the position past them."""
        ...


class MessengerFeed:
    def __init__(self, messenger: Messenger) -> None:
        self._messenger = messenger

    def latest_arrival(self) -> int:
        return self._messenger.latest_arrival()

    def alerts_after(self, position: int) -> tuple[list[Alert], int]:
        messages, cursor = self._messenger.unread_for_me(position)
        alerts = [
            Alert(self._messenger.display_name(message.entry.author), message.body.summary)
            for message in messages
        ]
        return alerts, cursor


class AlertWatcher:
    """Notifies once for each new message for this member that arrives after it starts.

    A burst is shown as a few notifications and one count of the rest, so catching up
    after time offline does not flood the screen.
    """

    def __init__(self, feed: MessageFeed, notifier: Notifier) -> None:
        self._feed = feed
        self._notifier = notifier
        self._cursor = feed.latest_arrival()

    async def check(self) -> int:
        alerts, self._cursor = self._feed.alerts_after(self._cursor)
        shown = list(alerts[:MAX_ALERTS_PER_CHECK])
        extra = len(alerts) - len(shown)
        if extra:
            shown.append(Alert("team", f"{extra} more new messages"))
        for alert in shown:
            try:
                await asyncio.to_thread(self._notifier.notify, alert)
            except NotifyError as exc:
                _log.warning("could not show a notification: %s", exc)
        return len(alerts)

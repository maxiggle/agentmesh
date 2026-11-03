import logging
from collections.abc import Callable

from ehor.log.roster import Roster, Trust
from ehor.service.direct_sync import RoundReport

_log = logging.getLogger(__name__)


def relay_needed(
    *,
    direct_running: bool,
    last_round: RoundReport | None,
    devices: frozenset[bytes] | None,
    announced: frozenset[bytes],
) -> bool:
    """Whether a round through the team's relays is worth making.

    `devices` are the other devices direct sync should reach, or None while this device
    has no roster yet. The relay is the mailbox for anyone direct sync cannot reach, so it
    is skipped only when every one of those devices has an endpoint and was reached.
    """
    if not direct_running or devices is None or last_round is None:
        return True
    return bool(devices - announced) or bool(devices & last_round.unreached)


def devices_to_reach(roster: Roster | None, own: bytes) -> frozenset[bytes] | None:
    """The other devices of the current members that sync with the team, or None without a roster.

    Security keys only sign roster changes; they never announce an endpoint or sync.
    """
    if roster is None:
        return None
    return frozenset(
        device.key
        for member in roster.members
        for device in member.devices
        if device.key != own and device.trust is not Trust.SECURITY_KEY
    )


class RelayGate:
    """Decides each relay round from live data, and logs when the decision changes."""

    def __init__(
        self,
        direct_running: Callable[[], bool],
        last_round: Callable[[], RoundReport | None],
        devices: Callable[[], frozenset[bytes] | None],
        announced: Callable[[], frozenset[bytes]],
    ) -> None:
        self._direct_running = direct_running
        self._last_round = last_round
        self._devices = devices
        self._announced = announced
        self._previous: bool | None = None

    def __call__(self) -> bool:
        needed = relay_needed(
            direct_running=self._direct_running(),
            last_round=self._last_round(),
            devices=self._devices(),
            announced=self._announced(),
        )
        if needed != self._previous:
            _log.info(
                "using the team relay: a teammate is out of direct reach"
                if needed
                else "skipping the team relay: every teammate is reached directly"
            )
            self._previous = needed
        return needed

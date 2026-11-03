import logging

import pytest

from ehor.log.roster import Member, Roster, RosterDevice, Trust
from ehor.service.direct_sync import RoundReport
from ehor.service.relay_policy import RelayGate, devices_to_reach, relay_needed

A = b"\x02" + b"a" * 32
B = b"\x02" + b"b" * 32
REACHED = RoundReport(frozenset({A, B}), frozenset())


def _needed(
    *,
    running: bool = True,
    last_round: RoundReport | None = REACHED,
    devices: frozenset[bytes] | None = frozenset({A, B}),
    announced: frozenset[bytes] = frozenset({A, B}),
) -> bool:
    return relay_needed(
        direct_running=running, last_round=last_round, devices=devices, announced=announced
    )


def test_a_fully_reached_team_skips_the_relay() -> None:
    assert not _needed()


def test_a_team_with_no_other_devices_skips_the_relay() -> None:
    assert not _needed(devices=frozenset(), announced=frozenset(), last_round=RoundReport())


def test_direct_not_running_needs_the_relay() -> None:
    assert _needed(running=False)


def test_no_roster_yet_needs_the_relay() -> None:
    assert _needed(devices=None)


def test_no_completed_direct_round_needs_the_relay() -> None:
    assert _needed(last_round=None)


def test_a_device_without_an_endpoint_needs_the_relay() -> None:
    assert _needed(announced=frozenset({A}))


def test_an_unreached_device_needs_the_relay() -> None:
    assert _needed(last_round=RoundReport(frozenset({A}), frozenset({B})))


def test_the_gate_reads_live_data_and_logs_only_when_the_decision_changes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reports: list[RoundReport | None] = [None]
    gate = RelayGate(
        lambda: True, lambda: reports[0], lambda: frozenset({A}), lambda: frozenset({A})
    )
    with caplog.at_level(logging.INFO):
        assert gate()
        assert gate()
        reports[0] = RoundReport(frozenset({A}), frozenset())
        assert not gate()
        assert not gate()
        reports[0] = RoundReport(frozenset(), frozenset({A}))
        assert gate()
    messages = [record.message for record in caplog.records]
    assert len(messages) == 3
    assert "skipping" in messages[1]


def _device(key: bytes, trust: Trust) -> RosterDevice:
    return RosterDevice(key, trust, "")


def test_devices_to_reach_leave_out_this_device_and_security_keys() -> None:
    own, other, token = (b"\x02" + bytes([i]) * 32 for i in (1, 2, 3))
    devices = (
        _device(own, Trust.SOFTWARE),
        _device(other, Trust.HARDWARE),
        _device(token, Trust.SECURITY_KEY),
    )
    roster = Roster(
        version=1,
        previous=None,
        team_name="",
        admins=("gef",),
        members=(Member("gef", (), ("*",), devices),),
    )
    assert devices_to_reach(roster, own) == frozenset({other})
    assert devices_to_reach(None, own) is None

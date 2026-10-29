from dataclasses import replace
from pathlib import Path

import pytest

from agentmesh.log.entry import EntryContent, Kind
from agentmesh.service.api import ApiError
from agentmesh.service.device import Device
from security_key_emulator import SoftwareAuthenticator

PASSPHRASE = b"test passphrase"
OTHER_KEY = "02" + "11" * 32


@pytest.fixture
def gef(tmp_path: Path) -> Device:
    return Device.create_team(tmp_path, "gef", PASSPHRASE, team_name="Acme")


def test_add_shows_roles_channels_and_admins(gef: Device) -> None:
    text = gef.call(
        "roster.add",
        {
            "handle": "tobi",
            "key": OTHER_KEY,
            "roles": ["@mobile"],
            "channels": ["#mobile"],
            "admin": True,
            "label": "tobi-mac",
        },
    )
    lines = text.splitlines()
    assert lines[0] == "added tobi"
    assert lines[1] == "Acme · roster v2"
    assert lines[2].endswith(" software")
    assert lines[3] == "tobi (admin, @mobile) · channels #mobile · tobi-mac 111111 software"
    gef.close()


def test_add_records_a_hardware_key(gef: Device) -> None:
    text = gef.call("roster.add", {"handle": "tobi", "key": OTHER_KEY, "hardware": True})
    assert text.splitlines()[-1] == "tobi · channels * · tobi 111111 hardware"
    roster = gef.roster.current
    assert roster is not None
    member = roster.member("tobi")
    assert member is not None
    assert [str(d.trust) for d in member.devices] == ["hardware"]
    gef.close()


def test_add_a_second_device_to_a_member(gef: Device) -> None:
    gef.call("roster.add", {"handle": "gef", "key": OTHER_KEY, "label": "gef-phone"})
    roster = gef.roster.current
    assert roster is not None
    member = roster.member("gef")
    assert member is not None
    assert {d.label for d in member.devices} == {"gef", "gef-phone"}
    assert [d.key for d in member.devices] == sorted(d.key for d in member.devices)
    gef.close()


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"handle": "tobi", "key": "zz"}, "hex"),
        ({"handle": "Tobi", "key": OTHER_KEY}, "invalid handle"),
        ({"handle": "tobi", "key": "02" + "11" * 31}, "33 bytes"),
        ({"handle": "tobi", "key": OTHER_KEY, "roles": ["mobile"]}, "invalid role"),
        ({"handle": "tobi", "key": OTHER_KEY, "admin": "yes"}, "true or false"),
        ({"handle": "tobi", "key": OTHER_KEY, "hardware": 1}, "true or false"),
    ],
)
def test_add_rejects_bad_input(gef: Device, params: dict[str, object], error: str) -> None:
    with pytest.raises(ApiError, match=error):
        gef.call("roster.add", params)
    gef.close()


def test_add_rejects_a_key_already_on_the_roster(gef: Device) -> None:
    with pytest.raises(ApiError, match="already on the roster"):
        gef.call("roster.add", {"handle": "tobi", "key": gef.author.hex()})
    gef.close()


def test_remove_unknown_member(gef: Device) -> None:
    with pytest.raises(ApiError, match="not on the roster"):
        gef.call("roster.remove", {"handle": "nobody"})
    gef.close()


def test_unknown_admin_method(gef: Device) -> None:
    with pytest.raises(ApiError, match="unknown method"):
        gef.call("roster.delete", {})
    gef.close()


RELAY_KEY = "03" + "22" * 32


def test_relays_can_be_added_shown_and_removed(gef: Device) -> None:
    text = gef.call("roster.relay_add", {"address": "relay.example.com:7447", "key": RELAY_KEY})
    assert text.splitlines()[0] == "added relay relay.example.com:7447"
    assert text.splitlines()[-1] == "relay relay.example.com:7447 · 222222"
    assert gef.relay_addresses() == ["relay.example.com:7447"]
    assert gef.invite.relay == "relay.example.com:7447"
    with pytest.raises(ApiError, match="already on the roster"):
        gef.call("roster.relay_add", {"address": "other:1", "key": RELAY_KEY})
    removed = gef.call("roster.relay_remove", {"address": "relay.example.com:7447"})
    assert removed.startswith("removed relay")
    assert gef.relay_addresses() == []
    gef.close()


@pytest.mark.parametrize(
    ("method", "params", "error"),
    [
        ("roster.relay_add", {"address": "no-port", "key": RELAY_KEY}, "host:port"),
        ("roster.relay_add", {"address": "relay:1", "key": "zz"}, "hex"),
        ("roster.relay_remove", {"address": "relay:1"}, "no relay"),
    ],
)
def test_relay_command_errors(
    gef: Device, method: str, params: dict[str, object], error: str
) -> None:
    with pytest.raises(ApiError, match=error):
        gef.call(method, params)
    gef.close()


def test_relay_key_cannot_be_a_member_device(gef: Device) -> None:
    with pytest.raises(ApiError, match="cannot also be a member"):
        gef.call("roster.relay_add", {"address": "relay:1", "key": gef.author.hex()})
    gef.close()


def _enroll(
    gef: Device, key: SoftwareAuthenticator, via: SoftwareAuthenticator | None = None
) -> str:
    params: dict[str, object] = {"key": key.public_key.hex(), "label": "yubikey"}
    if via is None:
        return gef.call("roster.enroll", params)
    return _with_key(gef, via, "roster.enroll", params)


def _draft(gef: Device, key: SoftwareAuthenticator, method: str, params: dict[str, object]) -> str:
    return gef.call(method, {**params, "security_key": key.public_key.hex()})


def _submit(gef: Device, key: SoftwareAuthenticator, draft: str) -> str:
    assertion = key.sign(EntryContent.decode(bytes.fromhex(draft)).signing_input)
    return gef.call(
        "roster.submit",
        {
            "content": draft,
            "auth_data": assertion.auth_data.hex(),
            "signature": assertion.signature.hex(),
        },
    )


def _with_key(
    gef: Device, key: SoftwareAuthenticator, method: str, params: dict[str, object]
) -> str:
    return _submit(gef, key, _draft(gef, key, method, params))


def test_enrolling_a_security_key_adds_it_to_the_admin(gef: Device) -> None:
    key = SoftwareAuthenticator()
    text = _enroll(gef, key)
    assert text.splitlines()[0] == "enrolled the security key"
    assert f"yubikey {key.public_key[1:4].hex()} security-key" in text.splitlines()[-1]
    assert key.touches == 0
    gef.close()


def test_after_enrolling_roster_changes_need_the_key(gef: Device) -> None:
    key = SoftwareAuthenticator()
    _enroll(gef, key)
    with pytest.raises(ApiError, match="gef must sign roster changes with a security key"):
        gef.call("roster.add", {"handle": "tobi", "key": OTHER_KEY})
    draft = _draft(gef, key, "roster.add", {"handle": "tobi", "key": OTHER_KEY})
    assert "tobi" not in gef.call("roster.show", {})
    text = _submit(gef, key, draft)
    assert text.splitlines()[:2] == ["signed with the security key", "Acme · roster v3"]
    assert "tobi · channels * · tobi 111111 software" in text
    assert key.touches == 1
    gef.close()


@pytest.mark.parametrize(
    ("method", "params", "shown"),
    [
        ("roster.remove", {"handle": "tobi"}, "roster v4"),
        ("roster.relay_add", {"address": "relay:1", "key": RELAY_KEY}, "relay relay:1"),
    ],
)
def test_every_change_can_be_signed_with_the_key(
    gef: Device, method: str, params: dict[str, object], shown: str
) -> None:
    key = SoftwareAuthenticator()
    gef.call("roster.add", {"handle": "tobi", "key": OTHER_KEY})
    _enroll(gef, key)
    assert shown in _with_key(gef, key, method, params)
    gef.close()


def test_a_second_key_is_enrolled_with_the_first(gef: Device) -> None:
    first, second = SoftwareAuthenticator(), SoftwareAuthenticator()
    _enroll(gef, first)
    with pytest.raises(ApiError, match="must sign roster changes with a security key"):
        _enroll(gef, second)
    _enroll(gef, second, via=first)
    assert "relay:1" in _with_key(
        gef, second, "roster.relay_add", {"address": "relay:1", "key": RELAY_KEY}
    )
    gef.close()


def test_a_key_must_be_enrolled_before_it_signs(gef: Device) -> None:
    with pytest.raises(ApiError, match="enroll it first"):
        _draft(gef, SoftwareAuthenticator(), "roster.add", {"handle": "tobi", "key": OTHER_KEY})
    gef.close()


def test_refuses_an_assertion_without_a_touch(gef: Device) -> None:
    key = SoftwareAuthenticator()
    _enroll(gef, key)
    draft = _draft(gef, key, "roster.add", {"handle": "tobi", "key": OTHER_KEY})
    key.flags = 0
    with pytest.raises(ApiError, match="refused: bad security key signature: user presence"):
        _submit(gef, key, draft)
    assert "tobi" not in gef.call("roster.show", {})
    gef.close()


def test_refuses_a_draft_that_went_stale(gef: Device) -> None:
    key = SoftwareAuthenticator()
    _enroll(gef, key)
    first = _draft(gef, key, "roster.add", {"handle": "tobi", "key": OTHER_KEY})
    stale = _draft(gef, key, "roster.relay_add", {"address": "relay:1", "key": RELAY_KEY})
    _submit(gef, key, first)
    with pytest.raises(ApiError, match="changed while waiting"):
        _submit(gef, key, stale)
    assert "relay:1" not in gef.call("roster.show", {})
    gef.close()


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"content": "zz", "auth_data": "", "signature": ""}, "content must be hex"),
        ({"content": "a0", "auth_data": "", "signature": ""}, "not an entry"),
        ({"content": "", "auth_data": "", "signature": "", "extra": 1}, "unknown params"),
    ],
)
def test_submit_rejects_bad_input(gef: Device, params: dict[str, object], error: str) -> None:
    with pytest.raises(ApiError, match=error):
        gef.call("roster.submit", params)
    gef.close()


def test_only_roster_entries_are_submitted(gef: Device) -> None:
    key = SoftwareAuthenticator()
    _enroll(gef, key)
    draft = _draft(gef, key, "roster.add", {"handle": "tobi", "key": OTHER_KEY})
    content = replace(EntryContent.decode(bytes.fromhex(draft)), kind=Kind.STATUS)
    with pytest.raises(ApiError, match="only roster changes"):
        _submit(gef, key, content.signed_bytes.hex())
    gef.close()


def test_only_admins_enroll_security_keys(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    with (
        Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared) as gef,
        Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared) as tobi,
    ):
        gef.sync()
        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
        tobi.sync()
        with pytest.raises(ApiError, match="only admins enroll"):
            _enroll(tobi, SoftwareAuthenticator())

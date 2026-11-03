import re
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from ehor.service import iroh_key
from ehor.service.device import Device

PASSPHRASE = b"endpoint passphrase"
ENDPOINT = b"\x05" * 32
OTHER = b"\x06" * 32


@pytest.fixture
def pair(tmp_path: Path) -> Iterator[tuple[Device, Device]]:
    shared = tmp_path / "shared"
    with ExitStack() as stack:
        gef = stack.enter_context(
            Device.create_team(tmp_path / "gef", "gef", PASSPHRASE, exchange=shared)
        )
        tobi = stack.enter_context(
            Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared)
        )
        yield gef, tobi


def _add_tobi(gef: Device, tobi: Device) -> None:
    gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
    _sync(gef, tobi)


def _sync(*devices: Device) -> None:
    for device in (*devices, *devices):
        device.sync()


def test_iroh_key_is_created_once_and_reused(tmp_path: Path) -> None:
    with Device.create_team(tmp_path, "gef", PASSPHRASE) as device:
        secret = device.iroh_secret
    assert len(secret) == 32
    assert (tmp_path / "keys" / "iroh.key").read_bytes() == secret
    with Device.open(tmp_path, PASSPHRASE) as device:
        assert device.iroh_secret == secret


@pytest.mark.skipif(sys.platform == "win32", reason="windows has no unix file modes")
def test_iroh_key_is_private_to_the_owner(tmp_path: Path) -> None:
    with Device.create_team(tmp_path, "gef", PASSPHRASE):
        pass
    assert stat.S_IMODE((tmp_path / "keys" / "iroh.key").stat().st_mode) == 0o600


def test_existing_secret_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "iroh.key"
    first = iroh_key.load_or_create_secret(path)
    assert iroh_key.load_or_create_secret(path) == first


@pytest.mark.parametrize("content", [b"", b"\x01" * 31, b"\x01" * 33])
def test_a_malformed_secret_is_reported_not_replaced(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "iroh.key"
    path.write_bytes(content)
    with pytest.raises(iroh_key.IrohKeyError, match=re.escape(str(path))):
        iroh_key.load_or_create_secret(path)
    assert path.read_bytes() == content


def test_a_leftover_temp_file_does_not_block_creation(tmp_path: Path) -> None:
    path = tmp_path / "iroh.key"
    (tmp_path / "iroh.key.crashed.tmp").write_bytes(b"\x01")
    assert len(iroh_key.load_or_create_secret(path)) == 32
    assert sorted(p.name for p in tmp_path.iterdir()) == ["iroh.key", "iroh.key.crashed.tmp"]


def test_endpoint_id_needs_iroh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "iroh", None)
    assert iroh_key.endpoint_id_for(b"\x01" * 32) is None


def test_endpoint_id_is_stable_for_a_secret() -> None:
    first = iroh_key.endpoint_id_for(b"\x01" * 32)
    assert first is not None
    assert len(first) == 32
    assert iroh_key.endpoint_id_for(b"\x01" * 32) == first
    assert iroh_key.endpoint_id_for(b"\x02" * 32) != first


def test_endpoint_is_announced_once_per_change(pair: tuple[Device, Device]) -> None:
    gef, tobi = pair
    assert not tobi.announce_endpoint(ENDPOINT)
    _add_tobi(gef, tobi)
    assert tobi.announce_endpoint(ENDPOINT)
    assert not tobi.announce_endpoint(ENDPOINT)
    assert tobi.announce_endpoint(OTHER)
    assert not tobi.announce_endpoint(OTHER)


def test_direct_peers_are_the_announced_devices_of_current_members(
    pair: tuple[Device, Device],
) -> None:
    gef, tobi = pair
    assert gef.direct_peers() == []
    _add_tobi(gef, tobi)
    assert gef.announce_endpoint(ENDPOINT)
    assert tobi.announce_endpoint(OTHER)
    _sync(gef, tobi)
    assert gef.direct_peers() == [(tobi.author, OTHER)]
    assert tobi.direct_peers() == [(gef.author, ENDPOINT)]


def test_removed_devices_are_not_direct_peers(pair: tuple[Device, Device]) -> None:
    gef, tobi = pair
    _add_tobi(gef, tobi)
    assert tobi.announce_endpoint(OTHER)
    _sync(gef, tobi)
    assert gef.direct_peers() == [(tobi.author, OTHER)]
    gef.call("roster.remove", {"handle": "tobi"})
    assert gef.direct_peers() == []


def test_invite_names_the_inviters_endpoint(tmp_path: Path) -> None:
    pytest.importorskip("iroh")
    with Device.create_team(tmp_path / "gef", "gef", PASSPHRASE) as gef:
        assert gef.endpoint_id is not None
        assert gef.invite.iroh == gef.endpoint_id
        assert Device.stored_endpoint_id(tmp_path / "gef") == gef.endpoint_id
        with Device.join(tmp_path / "tobi", gef.invite, "tobi", PASSPHRASE) as tobi:
            assert tobi.config.bootstrap_endpoint == gef.endpoint_id
    assert Device.stored_endpoint_id(tmp_path / "missing") is None


def test_invite_has_no_endpoint_without_iroh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "iroh", None)
    with Device.create_team(tmp_path, "gef", PASSPHRASE) as gef:
        assert gef.invite.iroh is None
        assert Device.stored_endpoint_id(tmp_path) is None


def test_bootstrap_endpoint_is_dialled_until_a_roster_device_announces(
    pair: tuple[Device, Device],
) -> None:
    pytest.importorskip("iroh")
    gef, tobi = pair
    assert gef.endpoint_id is not None
    assert tobi.direct_peers() == [(gef.endpoint_id, gef.endpoint_id)]
    _add_tobi(gef, tobi)
    assert tobi.direct_peers() == [(gef.endpoint_id, gef.endpoint_id)]
    assert gef.announce_endpoint(ENDPOINT)
    _sync(gef, tobi)
    assert tobi.direct_peers() == [(gef.author, ENDPOINT)]

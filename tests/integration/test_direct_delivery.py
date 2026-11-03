import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from ehor.service.api import ApiError
from ehor.service.device import Device
from ehor.service.direct_sync import DirectSync
from ehor.service.invite import Invite
from ehor.transport.iroh import IrohMode, IrohTransport

PASSPHRASE = b"direct passphrase"
TIMEOUT = 60.0


async def _until(condition: Callable[[], bool], *syncs: DirectSync) -> None:
    for _ in range(300):
        if condition():
            return
        for sync in syncs:
            sync.poke()
        await asyncio.sleep(0.1)
    raise AssertionError("timed out waiting for the devices to sync")


def _post_when_possible(device: Device, to: str, summary: str) -> str | None:
    try:
        return device.call("post", {"to": [to], "type": "fyi", "summary": summary})
    except ApiError:
        return None


async def _scenario(root: Path) -> None:
    with ExitStack() as stack:
        gef = stack.enter_context(Device.create_team(root / "gef", "gef", PASSPHRASE))
        tobi = stack.enter_context(Device.join(root / "tobi", gef.invite, "tobi", PASSPHRASE))
        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})

        transports = {
            device.author: IrohTransport(device.iroh_secret, IrohMode.LOCAL)
            for device in (gef, tobi)
        }
        by_endpoint = {
            endpoint: transports[device.author]
            for device in (gef, tobi)
            if (endpoint := device.endpoint_id) is not None
        }
        gef_endpoint = gef.endpoint_id
        assert gef_endpoint is not None

        bootstrap = [(gef.author, gef_endpoint)]

        def local(endpoint: bytes) -> list[str]:
            return by_endpoint[endpoint].local_addresses()

        gef_sync = DirectSync(
            gef.sync_node,
            transports[gef.author],
            gef.direct_peers,
            addresses=local,
            announce=gef.announce_endpoint,
            interval=0.2,
        )
        tobi_sync = DirectSync(
            tobi.sync_node,
            transports[tobi.author],
            lambda: [*tobi.direct_peers(), *bootstrap],
            addresses=local,
            announce=tobi.announce_endpoint,
            interval=0.2,
        )
        stop = asyncio.Event()
        running = [asyncio.ensure_future(sync.run(stop)) for sync in (gef_sync, tobi_sync)]
        try:
            await _until(lambda: tobi.roster.member_for_key(tobi.author) is not None, tobi_sync)
            assert tobi.announce_key()

            await _until(lambda: bool(gef.direct_peers()) and bool(tobi.direct_peers()), tobi_sync)
            bootstrap.clear()
            assert [device for device, _ in gef.direct_peers()] == [tobi.author]
            assert [device for device, _ in tobi.direct_peers()] == [gef.author]

            posted: list[str] = []

            def sent() -> bool:
                result = _post_when_possible(gef, "tobi", "straight to you")
                if result is not None:
                    posted.append(result.split()[1])
                return bool(posted)

            await _until(sent, gef_sync, tobi_sync)
            received: list[str] = []

            def delivered() -> bool:
                inbox = tobi.call("inbox", {})
                if posted[0] in inbox:
                    received.append(inbox)
                return bool(received)

            await _until(delivered, gef_sync, tobi_sync)
            assert '"straight to you"' in received[0]
            assert gef_sync.last_round is not None
            assert gef_sync.last_round.reached == {tobi.author}
        finally:
            stop.set()
            await asyncio.gather(*running)


def test_message_reaches_a_teammate_directly(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_scenario(tmp_path), TIMEOUT))


async def _joined_from_a_code(root: Path) -> None:
    with ExitStack() as stack:
        gef = stack.enter_context(Device.create_team(root / "gef", "gef", PASSPHRASE))
        code = gef.invite.encode()
        assert code.startswith("eh1-")
        tobi = stack.enter_context(
            Device.join(root / "tobi", Invite.decode(code), "tobi", PASSPHRASE)
        )
        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})

        transports = {
            device.author: IrohTransport(device.iroh_secret, IrohMode.LOCAL)
            for device in (gef, tobi)
        }
        by_endpoint = {
            endpoint: transports[device.author]
            for device in (gef, tobi)
            if (endpoint := device.endpoint_id) is not None
        }

        def local(endpoint: bytes) -> list[str]:
            return by_endpoint[endpoint].local_addresses()

        gef_sync, tobi_sync = (
            DirectSync(
                device.sync_node,
                transports[device.author],
                device.direct_peers,
                addresses=local,
                announce=device.announce_endpoint,
                interval=0.2,
            )
            for device in (gef, tobi)
        )
        stop = asyncio.Event()
        running = [asyncio.ensure_future(sync.run(stop)) for sync in (gef_sync, tobi_sync)]
        try:
            await _until(lambda: tobi.roster.member_for_key(tobi.author) is not None, tobi_sync)
            assert tobi.announce_key()
            await _until(
                lambda: (
                    [device for device, _ in tobi.direct_peers()] == [gef.author]
                    and bool(gef.direct_peers())
                ),
                tobi_sync,
            )

            posted: list[str] = []

            def sent() -> bool:
                result = _post_when_possible(gef, "tobi", "found you from the code")
                if result is not None:
                    posted.append(result.split()[1])
                return bool(posted)

            await _until(sent, gef_sync, tobi_sync)
            received: list[str] = []

            def delivered() -> bool:
                inbox = tobi.call("inbox", {})
                if posted[0] in inbox:
                    received.append(inbox)
                return bool(received)

            await _until(delivered, gef_sync, tobi_sync)
            assert '"found you from the code"' in received[0]
        finally:
            stop.set()
            await asyncio.gather(*running)


def test_a_device_joined_from_a_code_reaches_the_inviter_directly(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_joined_from_a_code(tmp_path), TIMEOUT))

import asyncio

from ehor.service.pacing import pause


async def _paused(*, stop: bool, wake: bool, interval: float) -> float:
    stop_event, wake_event = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop:
        loop.call_later(0.01, stop_event.set)
    if wake:
        loop.call_later(0.01, wake_event.set)
    started = loop.time()
    await pause(stop_event, wake_event, interval)
    return loop.time() - started


def test_returns_after_the_interval() -> None:
    assert asyncio.run(_paused(stop=False, wake=False, interval=0.05)) >= 0.05


def test_stop_ends_the_pause_early() -> None:
    assert asyncio.run(_paused(stop=True, wake=False, interval=60)) < 5


def test_wake_ends_the_pause_early() -> None:
    assert asyncio.run(_paused(stop=False, wake=True, interval=60)) < 5


def test_a_wake_from_before_the_pause_is_ignored() -> None:
    async def scenario() -> float:
        stop, wake = asyncio.Event(), asyncio.Event()
        wake.set()
        loop = asyncio.get_running_loop()
        started = loop.time()
        await pause(stop, wake, 0.05)
        return loop.time() - started

    assert asyncio.run(scenario()) >= 0.05


def test_leaves_no_pending_tasks() -> None:
    async def scenario() -> set[asyncio.Task[object]]:
        stop, wake = asyncio.Event(), asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, wake.set)
        await pause(stop, wake, 60)
        await asyncio.sleep(0)
        return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}

    assert asyncio.run(scenario()) == set()

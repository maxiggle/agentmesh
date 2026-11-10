import asyncio

from ehor.service.pacing import pause

LONG = 60.0
SETTLE = 0.05
DEADLINE = 5.0


async def _still_paused_after_settling(stop: asyncio.Event, wake: asyncio.Event) -> bool:
    task = asyncio.create_task(pause(stop, wake, LONG))
    await asyncio.sleep(SETTLE)
    paused = not task.done()
    stop.set()
    await asyncio.wait_for(task, DEADLINE)
    return paused


def test_returns_after_the_interval() -> None:
    asyncio.run(asyncio.wait_for(pause(asyncio.Event(), asyncio.Event(), 0.01), DEADLINE))


def test_stays_paused_until_something_happens() -> None:
    assert asyncio.run(_still_paused_after_settling(asyncio.Event(), asyncio.Event()))


def test_stop_ends_the_pause_early() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, stop.set)
        await asyncio.wait_for(pause(stop, asyncio.Event(), LONG), DEADLINE)

    asyncio.run(scenario())


def test_wake_ends_the_pause_early() -> None:
    async def scenario() -> None:
        wake = asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, wake.set)
        await asyncio.wait_for(pause(asyncio.Event(), wake, LONG), DEADLINE)

    asyncio.run(scenario())


def test_a_wake_from_before_the_pause_is_ignored() -> None:
    async def scenario() -> bool:
        wake = asyncio.Event()
        wake.set()
        return await _still_paused_after_settling(asyncio.Event(), wake)

    assert asyncio.run(scenario())


def test_leaves_no_pending_tasks() -> None:
    async def scenario() -> set[asyncio.Task[object]]:
        stop, wake = asyncio.Event(), asyncio.Event()
        asyncio.get_running_loop().call_later(0.01, wake.set)
        await pause(stop, wake, LONG)
        await asyncio.sleep(0)
        return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}

    assert asyncio.run(scenario()) == set()

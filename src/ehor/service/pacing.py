import asyncio


async def pause(stop: asyncio.Event, wake: asyncio.Event, interval: float) -> None:
    """Wait until `stop` or `wake` is set, or `interval` seconds pass, then clear `wake`."""
    wake.clear()
    waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(wake.wait())]
    try:
        await asyncio.wait(waiters, timeout=interval, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()

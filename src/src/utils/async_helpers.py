"""Async/sync bridge utilities for running async code from synchronous contexts."""

import asyncio
import concurrent.futures

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def run_async_from_sync(coro):
    """Run an async coroutine from sync code, safe within an existing event loop.

    When called outside an event loop, uses asyncio.run() directly.
    When called inside a running event loop (e.g. FastAPI), runs the
    coroutine in a separate thread with its own event loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _run_in_new_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    future = _executor.submit(_run_in_new_loop)
    return future.result(timeout=120)

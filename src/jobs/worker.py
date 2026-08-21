"""Standalone durable queue worker entry point: ``python -m src.jobs.worker``."""

from __future__ import annotations

import asyncio
import logging
import signal

from .job_processor import JobProcessor
from .registry import build_default_registry

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    processor = JobProcessor(registry=build_default_registry())
    loop = asyncio.get_running_loop()

    def drain() -> None:
        logger.info("Worker drain requested", extra={"worker_id": processor.worker_id})
        processor.request_drain()

    for signame in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            try:
                loop.add_signal_handler(signum, drain)
            except NotImplementedError:
                signal.signal(signum, lambda *_: drain())
    await processor.start()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

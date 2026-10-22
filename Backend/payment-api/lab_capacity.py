"""Explicit teaching profile: finite simulated payment capacity, no CPU exhaustion."""
import asyncio
from contextlib import asynccontextmanager


class CapacityExceeded(Exception):
    pass


class LabCapacity:
    def __init__(self, enabled=False, slots=2, work_seconds=1.0, queue_seconds=0.05):
        self.enabled = enabled
        self.semaphore = asyncio.Semaphore(slots)
        self.work_seconds = work_seconds
        self.queue_seconds = queue_seconds

    @asynccontextmanager
    async def operation(self):
        if not self.enabled:
            yield
            return
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.queue_seconds)
        except asyncio.TimeoutError as exc:
            raise CapacityExceeded from exc
        try:
            await asyncio.sleep(self.work_seconds)
            yield
        finally:
            self.semaphore.release()

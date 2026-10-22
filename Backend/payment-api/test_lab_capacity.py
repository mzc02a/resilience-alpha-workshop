import asyncio
import unittest

from lab_capacity import CapacityExceeded, LabCapacity


class CapacityTests(unittest.TestCase):
    def test_saturation_then_recovery(self):
        async def scenario():
            capacity = LabCapacity(True, slots=1, work_seconds=0, queue_seconds=0.01)
            async with capacity.operation():
                with self.assertRaises(CapacityExceeded):
                    async with capacity.operation():
                        self.fail("Capacity exceeded")
            async with capacity.operation():
                pass
        asyncio.run(scenario())

    def test_cancelled_request_releases_capacity(self):
        async def scenario():
            capacity = LabCapacity(True, slots=1, work_seconds=10, queue_seconds=0.01)
            async def request():
                async with capacity.operation():
                    pass
            task = asyncio.create_task(request())
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            capacity.work_seconds = 0
            async with capacity.operation():
                pass
        asyncio.run(scenario())

    def test_normal_profile_has_no_simulated_work(self):
        async def scenario():
            capacity = LabCapacity(False, work_seconds=100)
            async with asyncio.timeout(0.1):
                async with capacity.operation():
                    pass
        asyncio.run(scenario())

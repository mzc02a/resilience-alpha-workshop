"""Contract tests with synthetic fixtures, never presented as real incident evidence."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import numpy as np
from fastapi import HTTPException

import main


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.profile = patch.object(main, 'current_profile', AsyncMock(return_value={
            'enabled': True, 'slots': 2, 'simulated_work_seconds': 1.0}))
        self.profile.start()
        self.addCleanup(self.profile.stop)
        self.saved = (main.model, main.model_metadata, main.latest, main.STATE_PATH)
        main.model_metadata = {}
        main.STATE_PATH = None
        self.addCleanup(self.restore_globals)

    def restore_globals(self):
        main.model, main.model_metadata, main.latest, main.STATE_PATH = self.saved

    def test_selected_training_window(self):
        rows = [(i * 15, [0.5 + (i % 3) * 0.01, 0, 1.0]) for i in range(65)]
        collector = AsyncMock(return_value=rows)
        with patch.object(main, "observations", collector), patch.object(main.time, "time", return_value=10000):
            result = asyncio.run(main.train(minutes=16))
        collector.assert_awaited_once_with(9025, 9985)
        self.assertEqual(result["samples"], 65)

    def test_train_rejects_missing_data(self):
        with patch.object(main, "observations", AsyncMock(return_value=[])):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.train())
        self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(main.training)

    def test_training_and_scoring(self):
        rng = np.random.default_rng(42)
        values = rng.normal([10, 0.2, 0.1], [1, 0.02, 0.01], size=(120, 3))
        rows = [(i * 15, v.tolist()) for i, v in enumerate(values)]
        with patch.object(main, "observations", AsyncMock(return_value=rows)):
            result = asyncio.run(main.train())
        self.assertEqual(result["samples"], 120)
        self.assertLess(main.model.decision_function([[1000, 500, 20]])[0], 0)
        self.assertGreater(main.model.decision_function([[10, 0.2, 0.1]])[0], 0)

    def test_collection_failure_is_not_normal(self):
        main.model = object()
        async def run_once():
            with patch.object(main, "observations", AsyncMock(side_effect=ValueError("no telemetry"))):
                with patch.object(main.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)):
                    with self.assertRaises(asyncio.CancelledError):
                        await main.analyze()
        asyncio.run(run_once())
        self.assertEqual(main.latest["state"], "telemetry_unavailable")

    def test_prometheus_alignment_excludes_down_and_nonfinite(self):
        def respond(request):
            query = request.url.params["query"]
            values = [[15, "1"], [30, "0"]] if query.startswith("min(up") else [[15, "2"], [30, "NaN"]]
            return httpx.Response(200, json={"status": "success", "data": {
                "resultType": "matrix", "result": [{"values": values}]}})
        async def fetch():
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch.object(main.httpx, "AsyncClient", return_value=client):
                return await main.observations(0, 30)
        self.assertEqual(asyncio.run(fetch()), [(15.0, [2.0, 2.0, 2.0])])


if __name__ == "__main__":
    unittest.main()

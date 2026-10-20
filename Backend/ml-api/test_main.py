"""Contract tests with synthetic fixtures, never presented as real incident evidence."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import numpy as np
from fastapi import HTTPException

import main


class DetectorTests(unittest.TestCase):
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

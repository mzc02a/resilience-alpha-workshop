"""Offline end-to-end orchestration checks with a simulated clock and HTTP client."""
import asyncio
import contextlib
import importlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Backend/security'))
repair = importlib.import_module('repair_calibration')


class RepairTests(unittest.TestCase):
    def scenario(self, state='normal', model_id='new-model'):
        import httpx
        clock = [0.0]
        posts = []
        async def get(url, **kwargs):
            if url.endswith('/status'):
                sample = {'state': state}
                if state in ('normal', 'anomaly'):
                    sample.update(timestamp=20000 + (int(clock[0]) // 15) * 15 - 15,
                                  model_id=model_id)
                return httpx.Response(200, json=sample)
            return httpx.Response(200, json=[], request=httpx.Request('GET', url))
        async def post(url, **kwargs):
            posts.append((url, kwargs))
            return httpx.Response(200, json={'training': {'model_id': 'new-model'},
                'historical_validation': {'false_alert_fraction': 0}})
        async def advance(seconds):
            clock[0] += seconds
        client = type('Client', (), {'get': staticmethod(get), 'post': staticmethod(post)})()
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dos-calibration.json'
            source = {'state': 'review_required', 'started_at': 100, 'requests': 500,
                      'false_alert_fraction': 1,
                      'training': {'start': 100, 'end': 1060, 'samples': 65},
                      'validation': [{'timestamp': 1100 + i * 15} for i in range(8)]}
            path.write_text(json.dumps(source))
            with patch('sys.argv', ['repair', '--report', str(path)]), \
                 patch.object(repair, 'load_session', return_value={'access_token': 'synthetic', 'recipient_iban': 'TEST'}), \
                 patch.object(repair, 'preflight', AsyncMock()), \
                 patch.object(repair.httpx, 'AsyncClient', return_value=manager), \
                 patch.object(repair.time, 'time', side_effect=lambda: 20000 + clock[0]), \
                 patch.object(repair.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(repair.asyncio, 'sleep', AsyncMock(side_effect=advance)), \
                 contextlib.redirect_stdout(io.StringIO()):
                if state == 'normal' and model_id == 'new-model':
                    asyncio.run(repair.main())
                else:
                    with self.assertRaises(RuntimeError):
                        asyncio.run(repair.main())
            result = json.loads(path.read_text())
            self.assertEqual(len(list(Path(folder).glob('dos-calibration-before-replay-*.json'))), 1)
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][0].endswith('/replay-baseline'))
        self.assertEqual(posts[0][1]['json']['start'], 100)
        self.assertGreaterEqual(clock[0], 120)
        self.assertLess(clock[0], 130)
        return result

    def test_fresh_normal_validation_accepts_without_new_collection(self):
        result = self.scenario()
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['false_alert_fraction'], 0)
        self.assertGreaterEqual(len(result['validation']), 5)
        self.assertEqual(result['source_reference']['false_alert_fraction'], 1)

    def test_anomalous_validation_never_forced_ready(self):
        self.assertEqual(self.scenario('anomaly')['state'], 'review_required')

    def test_missing_telemetry_never_counted_normal(self):
        self.assertEqual(self.scenario('telemetry_unavailable')['state'], 'review_required')

    def test_model_change_during_validation_rejected(self):
        self.assertEqual(self.scenario(model_id='different-model')['state'], 'review_required')


if __name__ == '__main__':
    unittest.main()

"""Synthetic fixtures test behavior, not evidence of performance on the platform."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi import HTTPException
from detector import BaselineDetector
import main


PROFILE = {'enabled': True, 'slots': 2, 'simulated_work_seconds': 1.0}


def baseline():
    rng = np.random.default_rng(42)
    values = rng.normal([0.49, 0, 1.009], [0.015, 0, 0.0005], (65, 3))
    values[0] = [0.422, 0, 0.851]  # Startup transient; fixture, not measured data.
    return [(100 + i * 15, row.tolist()) for i, row in enumerate(values)]


class DetectorCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.saved = (main.model, main.model_metadata, main.latest, main.STATE_PATH)
        main.STATE_PATH = None
        self.profile = patch.object(main, 'current_profile', AsyncMock(return_value=PROFILE))
        self.profile.start()

    def tearDown(self):
        self.profile.stop()
        main.model, main.model_metadata, main.latest, main.STATE_PATH = self.saved

    def test_small_faster_latency_is_normal_but_overload_is_anomalous(self):
        model = BaselineDetector().fit([r[1] for r in baseline()])
        normal = [[0.49, 0, 1.005], [0.51, 0, 1.0048]]
        self.assertTrue((model.decision_function(normal) > 0).all())
        attacks = [[4, 2, 0.5], [3, 0, 1.0], [0.5, 0.5, 1.0]]
        self.assertTrue((model.decision_function(attacks) < 0).all())

    def test_constant_reference_is_invertible_and_detects_new_errors(self):
        model = BaselineDetector().fit([[0.5, 0, 1.0]] * 65)
        self.assertGreater(model.decision_function([[0.5, 0, 1.004]])[0], 0)
        self.assertLess(model.decision_function([[0.5, 0.3, 1.0]])[0], 0)

    def test_invalid_or_short_reference_rejected(self):
        for values in ([[1, 2, 3]] * 59, [[1, 2, float('nan')]] * 65):
            with self.assertRaises(ValueError):
                BaselineDetector().fit(values)

    def test_reference_survives_restart_with_identical_scores(self):
        rows = baseline()
        model = BaselineDetector().fit([r[1] for r in rows])
        with tempfile.TemporaryDirectory() as directory:
            main.STATE_PATH = str(Path(directory) / 'baseline.json')
            state = main.baseline_state(rows, PROFILE, 'synthetic test')
            main.publish(model, state)
            old_id = main.model_metadata['model_id']
            expected = model.decision_function([[0.49, 0, 1.005], [4, 2, 0.5]])
            main.model = None
            main.model_metadata = {}
            asyncio.run(main.restore())
            self.assertEqual(main.model_metadata['model_id'], old_id)
            np.testing.assert_allclose(main.model.decision_function([[0.49, 0, 1.005], [4, 2, 0.5]]), expected)

    def test_wrong_profile_not_restored(self):
        rows = baseline()
        with tempfile.TemporaryDirectory() as directory:
            main.STATE_PATH = str(Path(directory) / 'baseline.json')
            state = main.baseline_state(rows, {**PROFILE, 'enabled': False}, 'test')
            main.persist(state)
            main.model = None
            asyncio.run(main.restore())
            self.assertIsNone(main.model)

    def test_replay_trains_only_on_reference_and_checks_separate_validation(self):
        rows = baseline()
        validation = [(1100 + i * 15, [0.50, 0, 1.005]) for i in range(8)]
        collector = AsyncMock(side_effect=[rows, validation])
        window = main.HistoricalWindow(start=100, end=1060, samples=65,
                                       validation_start=1100, validation_end=1205)
        with patch.object(main, 'observations', collector):
            result = asyncio.run(main.replay_baseline(window))
        self.assertTrue(result['requires_fresh_validation'])
        self.assertEqual(result['training']['samples'], 65)
        self.assertEqual(result['historical_validation']['false_alert_fraction'], 0)
        self.assertEqual(collector.await_args_list[0].args, (100, 1060))
        self.assertEqual(collector.await_args_list[1].args, (1100, 1205))

    def test_replay_failure_does_not_publish_or_force_ready(self):
        rows = baseline()
        bad_validation = [(1100 + i * 15, [4, 2, .5]) for i in range(8)]
        previous = object()
        main.model = previous
        with patch.object(main, 'observations', AsyncMock(side_effect=[rows, bad_validation])):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.replay_baseline(main.HistoricalWindow(
                    start=100, end=1060, samples=65, validation_start=1100, validation_end=1205)))
        self.assertEqual(error.exception.status_code, 409)
        self.assertIs(main.model, previous)


if __name__ == '__main__':
    unittest.main()

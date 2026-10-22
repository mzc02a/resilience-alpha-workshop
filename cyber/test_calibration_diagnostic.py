"""Synthetic offline checks for the read-only diagnostic."""
import importlib.util
import unittest
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location('diagnostic', Path(__file__).resolve().parents[1] / 'Backend/security/diagnose_calibration.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class DiagnosticTests(unittest.TestCase):
    def test_reports_out_of_range_and_sensitivity_without_mutation(self):
        rng = np.random.default_rng(7)
        reference = rng.normal([0.5, 0, 1.002], [0.015, 0, 0.0001], (65, 3))
        validation = np.array([[0.5, 0, 1.005]] * 8)
        saved = validation.copy()
        report = diagnostic.compare(reference, validation)
        self.assertEqual(report['features']['mean_latency_seconds']['validation_outside_training_range'], 8)
        self.assertGreater(report['reconstructed_validation_anomaly_fraction'],
            report['features']['mean_latency_seconds']['sensitivity_anomaly_fraction_if_feature_replaced_by_training_median'])
        np.testing.assert_array_equal(saved, validation)

    def test_missing_reference_rejected(self):
        with self.assertRaises(ValueError):
            diagnostic.compare([[0.5, 0, 1.0]], [[0.5, 0, 1.0]])


if __name__ == '__main__':
    unittest.main()

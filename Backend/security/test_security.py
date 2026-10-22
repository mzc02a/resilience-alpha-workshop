import unittest
import time
from unittest.mock import patch

import gateway
from remediator import Policy


class PolicyTests(unittest.TestCase):
    def test_three_distinct_samples_and_cooldown(self):
        policy = Policy()
        sample = lambda t: {"service": "payment-api", "timestamp": t, "state": "anomaly", "anomaly": True, "features": {"requests_per_second": 2}}
        self.assertFalse(policy.decide(sample(100), 120))
        self.assertFalse(policy.decide(sample(100), 120))
        self.assertFalse(policy.decide(sample(115), 130))
        self.assertTrue(policy.decide(sample(130), 145))
        for t in (145, 160, 175):
            self.assertFalse(policy.decide(sample(t), t + 15))

    def test_stale_missing_normal_or_gap_resets_streak(self):
        policy = Policy()
        self.assertFalse(policy.decide({"state": "telemetry_unavailable"}, 1000))
        self.assertFalse(policy.decide({"service": "payment-api", "state": "anomaly", "anomaly": True, "timestamp": 1}, 1000))
        for t in (100, 150):
            self.assertFalse(policy.decide({"service": "payment-api", "state": "anomaly", "anomaly": True, "timestamp": t, "features": {"requests_per_second": 2}}, t + 15))
        self.assertEqual(policy.streak, 1)

    def test_rate_limit_per_source_and_expiry(self):
        gateway.buckets.clear()
        with patch.object(gateway, "until", 200):
            self.assertTrue(all(gateway.admitted("192.0.2.1", 100) for _ in range(3)))
            self.assertFalse(gateway.admitted("192.0.2.1", 100))
            self.assertTrue(gateway.admitted("192.0.2.2", 100))
            self.assertTrue(gateway.admitted("192.0.2.1", 101))
            self.assertTrue(gateway.admitted("192.0.2.1", 201))

    def test_control_requires_secret(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            gateway.authorize(type("Request", (), {"headers": {}})())


if __name__ == "__main__":
    unittest.main()

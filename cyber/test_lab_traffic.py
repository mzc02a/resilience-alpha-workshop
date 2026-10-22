"""Offline checks: no requests are sent to the PC host."""
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("lab_traffic", Path(__file__).resolve().parent / "lab_traffic_measured.py")
traffic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(traffic)


class Response(io.BytesIO):
    status = 200


class TrafficTests(unittest.TestCase):
    def test_target_restrictions(self):
        self.assertEqual(traffic.validate_url("http://192.168.137.1:8080/"), "http://192.168.137.1:8080")
        for value in ("http://example.com:8080", "http://192.168.137.1:8000",
                      "http://user:password@192.168.137.1:8080", "http://192.168.137.1:8080/?x=1",
                      "http://192.168.137.1:8080/#fragment", "https://192.168.137.1:8080"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                traffic.validate_url(value)

    def test_redirect_not_followed(self):
        self.assertIsNone(traffic.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com"))

    def test_empty_summary(self):
        self.assertIsNone(traffic.summary([])["latency_200_p95_ms"])

    def test_ramp_reports_http_errors_and_no_secrets(self):
        clock = [0.0]
        calls = []

        class FakeOpener:
            def open(self, request, timeout):
                calls.append(request)
                if request.full_url.endswith('/auth/login'):
                    return Response(b'{"access_token":"synthetic-jwt"}')
                if len(calls) == 3:
                    raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, None)
                if len(calls) == 4:
                    raise urllib.error.URLError("synthetic network failure")
                return Response(b'[]')

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(traffic, '__file__', str(Path(temp) / 'Backend/security/lab_traffic.py')), \
                 patch.object(traffic, 'lab_opener', return_value=FakeOpener()), \
                 patch.object(traffic.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(traffic.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                 patch('builtins.input', return_value='test@example.com'), \
                 patch.object(traffic.getpass, 'getpass', return_value='synthetic-password'), \
                 patch('sys.argv', ['lab_traffic.py', '--ramp', '--seconds', '30', '--rps', '3']), \
                 redirect_stdout(io.StringIO()):
                traffic.main()
            folder = Path(temp) / 'Backend/security/results'
            report = json.loads(next(folder.glob('*.json')).read_text())
            self.assertEqual(report['statuses']['429'], 1)
            self.assertEqual(report['statuses']['unavailable'], 1)
            self.assertEqual(set(report['phases']), {'reference', 'charge'})
            self.assertLessEqual(report['requests'], 51)
            for path in folder.iterdir():
                content = path.read_text()
                self.assertNotIn('synthetic-jwt', content)
                self.assertNotIn('synthetic-password', content)
                self.assertNotIn('test@example.com', content)


if __name__ == '__main__':
    unittest.main()

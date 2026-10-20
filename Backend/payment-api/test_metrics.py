import asyncio
import os
import unittest

# SQLite creates no file and no external database connection. Startup is not run.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-only-secret-not-for-use"
import main


class MetricsTests(unittest.TestCase):
    def test_success_and_exception_are_counted(self):
        request = type("Request", (), {"url": type("URL", (), {"path": "/payments"})()})()
        before = main.http_requests.labels("201")._value.get()
        async def success(_):
            return type("Response", (), {"status_code": 201})()
        asyncio.run(main.observe_requests(request, success))
        self.assertEqual(main.http_requests.labels("201")._value.get(), before + 1)
        before = main.http_requests.labels("500")._value.get()
        async def fail(_):
            raise RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            asyncio.run(main.observe_requests(request, fail))
        self.assertEqual(main.http_requests.labels("500")._value.get(), before + 1)

    def test_scraping_does_not_change_business_counters(self):
        request = type("Request", (), {"url": type("URL", (), {"path": "/metrics"})()})()
        before = main.http_requests.labels("200")._value.get()
        async def scrape(_):
            return main.metrics()
        response = asyncio.run(main.observe_requests(request, scrape))
        self.assertEqual(main.http_requests.labels("200")._value.get(), before)
        body = response.body.decode()
        self.assertIn('http_requests_total{status="500"}', body)
        self.assertIn("http_request_duration_seconds_count", body)

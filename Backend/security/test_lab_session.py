"""Contract tests with synthetic sessions only: no real JWT, image or payment."""
import asyncio
import base64
import contextlib
import importlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import lab_session as lab


def synthetic_token(**changes):
    claims = {"exp": time.time() + 1800, "face_verified_until": time.time() + 1800,
              "nbf": time.time() - 1, "auth_flow": "identifier_face_password",
              "face_method": "image_comparison"}
    claims.update(changes)
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return 'synthetic.' + payload + '.not-a-real-signature'


class LabSessionTests(unittest.TestCase):
    def test_expired_pending_and_wrong_flow_rejected(self):
        self.assertGreater(lab.token_lifetime(synthetic_token(), 1260), 1260)
        for claims in ({"exp": time.time() - 1}, {"nbf": time.time() + 30},
                       {"face_verified_until": 0}, {"auth_flow": "password_only"}):
            with self.subTest(claims=claims), self.assertRaises(lab.LabSessionError):
                lab.token_lifetime(synthetic_token(**claims), 60)

    def test_local_session_reload_and_error_do_not_expose_jwt(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.json'
            token = synthetic_token()
            path.write_text(json.dumps({"access_token": token, "recipient_iban": 'FR-TEST'}))
            self.assertEqual(lab.load_session(60, path)['access_token'], token)
            path.write_text('{}')
            with self.assertRaises(lab.LabSessionError) as error:
                lab.load_session(60, path)
            self.assertNotIn(token, str(error.exception))

    def test_transfer_contract_unique_keys_and_no_retry(self):
        calls = []
        def server(request):
            calls.append(request)
            self.assertEqual(request.url.path, '/payments')
            self.assertEqual(json.loads(request.content), {
                'recipient_iban': 'FR-TEST', 'amount': '1.00', 'currency': 'EUR'})
            self.assertTrue(request.headers['idempotency-key'])
            return httpx.Response(503, json={"detail": "synthetic capacity failure"})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
                session = {"access_token": synthetic_token(), "recipient_iban": 'FR-TEST'}
                self.assertEqual((await lab.transfer(client, session)).status_code, 503)
                self.assertEqual((await lab.transfer(client, session)).status_code, 503)
        asyncio.run(run())
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0].headers['idempotency-key'], calls[1].headers['idempotency-key'])

    def test_signature_access_still_checked_by_server(self):
        calls = []
        def server(request):
            calls.append(request)
            return httpx.Response(401, json={"detail": "Invalid signature"})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    await lab.preflight(client, {"access_token": synthetic_token(), "recipient_iban": 'FR-TEST'})
        asyncio.run(run())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].method, 'GET')

    def test_preflight_rejects_self_transfer_and_unknown_recipient(self):
        async def run(recipient):
            def server(request):
                payloads = {'/auth/me': {'id': 'sender'}, '/accounts/me': {'iban': 'SELF', 'balance': '100.00'},
                            '/beneficiaries': [{'iban': 'OTHER'}]}
                return httpx.Response(200, json=payloads[request.url.path])
            async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
                with self.assertRaises(lab.LabSessionError):
                    await lab.preflight(client, {'access_token': synthetic_token(), 'recipient_iban': recipient})
        asyncio.run(run('SELF'))
        asyncio.run(run('UNKNOWN'))

    def test_readback_must_be_successful_same_transfer(self):
        created = {'id': 'payment', 'status': 'SUCCESS', 'recipient_iban': 'OTHER', 'amount': '1.00'}
        self.assertTrue(lab.readback_matches(created, dict(created)))
        self.assertFalse(lab.readback_matches(created, {**created, 'status': 'DECLINED'}))
        self.assertFalse(lab.readback_matches(created, {**created, 'id': 'different'}))

    def test_preparation_failure_replaces_stale_ready_report(self):
        calibration = importlib.import_module('calibrate_dos')
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'report.json'
            report.write_text('{"state":"ready","requests":509}')
            with patch.object(calibration, 'Path', return_value=report), \
                 patch.object(calibration, 'load_session', side_effect=lab.LabSessionError('synthetic missing session')), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(lab.LabSessionError):
                    asyncio.run(calibration.main())
            data = json.loads(report.read_text())
            self.assertEqual(data['state'], 'failed')
            self.assertEqual(data['requests'], 0)


if __name__ == '__main__':
    unittest.main()

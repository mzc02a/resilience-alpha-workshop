"""Tests of the identifier -> face -> password contract using an isolated in-memory database."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-only-face-secret-at-least-32-bytes-long"

import io
import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import face_auth
import main


class FaceAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        main.Account.metadata.create_all(self.engine)
        self.user_id = "00000000-0000-0000-0000-000000000001"
        with Session(self.engine) as db:
            db.add(main.User(id=self.user_id, email="admin@example.com", password_hash="test-hash", role="admin"))
            db.add(main.User(id="00000000-0000-0000-0000-000000000002", email="user@example.com",
                             password_hash="test-hash", role="user"))
            db.commit()
        def database():
            with Session(self.engine) as db:
                yield db
        main.app.dependency_overrides[main.get_db] = database
        self.client = TestClient(main.app)
        self.now = time.time()
        self.patches = [
            patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"}),
            patch.object(main.time, "time", side_effect=lambda: self.now),
            patch.object(main, "models_available", return_value=True),
            patch.object(main, "reference_path", return_value=SimpleNamespace(is_file=lambda: True)),
            patch.object(main, "password_hash", SimpleNamespace(verify=lambda password, hashed: password == "correct-password", hash=lambda password: "test-hash")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()

    def identify(self, email="admin@example.com"):
        return self.client.post("/auth/backoffice/identify", json={"email": email})

    def upload(self, token, matched=True):
        with patch.object(main, "compare_face", return_value=matched):
            return self.client.post("/auth/face/verify",
                data={"challenge_token": token},
                files={"image": ("camera.jpg", b"fixture", "image/jpeg")})

    def login(self, token=None, password="correct-password", email="admin@example.com"):
        return self.client.post("/auth/login", json={
            "email": email, "password": password, "face_challenge": token})

    def recognized(self, email="admin@example.com"):
        token = self.identify(email=email).json()["challenge_token"]
        self.assertEqual(self.upload(token).status_code, 200)
        return token

    def test_identifier_and_face_never_issue_session_token(self):
        result = self.identify()
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("access_token", result.json())
        token = result.json()["challenge_token"]
        response = self.upload(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["led_state"], "green_blink")
        self.assertNotIn("access_token", response.json())
        self.assertEqual(self.client.get("/auth/admin", headers={
            "Authorization": "Bearer " + token}).status_code, 401)

    def test_names_are_required_and_profile_update_cannot_change_account(self):
        self.assertEqual(self.client.post("/auth/register", data={"email": "new@example.com", "password": "correct-password"}, files={"image": ("face.jpg", b"frame", "image/jpeg")}).status_code, 422)
        with Session(self.engine) as db:
            user = db.get(main.User, self.user_id)
            main.create_account(db, user.id)
            db.commit()
            before = db.get(main.Account, user.id).iban
        main.app.dependency_overrides[main.get_current_user] = lambda: user
        result = self.client.patch("/auth/me", json={"first_name": "  Camille ", "last_name": "Martin", "role": "user", "iban": "changed"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["first_name"], "Camille")
        self.assertEqual(result.json()["last_name"], "Martin")
        self.assertEqual(result.json()["role"], "admin")
        self.assertEqual(result.json()["iban"], before)
        self.assertEqual(self.client.patch("/auth/me", json={"first_name": "  ", "last_name": "Martin"}).status_code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.get(main.User, user.id).first_name, "Camille")

    def test_admin_password_alone_cannot_log_in(self):
        self.assertEqual(self.login().status_code, 403)

    def test_password_before_face_is_rejected(self):
        token = self.identify().json()["challenge_token"]
        self.assertEqual(self.login(token).status_code, 403)

    def test_login_requires_correct_password_and_face_then_waits_30_seconds(self):
        token = self.recognized()
        response = self.login(token)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(data["backoffice_ready_at"], self.now + 30)
        claims = jwt.decode(data["access_token"], main.JWT_SECRET, algorithms=["HS256"],
                            issuer=main.JWT_ISSUER, audience=main.JWT_AUDIENCE,
                            options={"verify_nbf": False})
        self.assertEqual(claims["auth_flow"], "identifier_face_password")
        self.assertEqual(claims["nbf"], data["backoffice_ready_at"])
        headers = {"Authorization": "Bearer " + data["access_token"]}
        self.assertEqual(self.client.get("/auth/admin", headers=headers).status_code, 401)
        future = self.now + 32
        class FutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(future, tz=tz)
        with patch("jwt.api_jwt.datetime", FutureDatetime):
            self.assertEqual(self.client.get("/auth/admin", headers=headers).status_code, 200)

    def test_password_failure_consumes_face_proof_and_restarts_identification(self):
        token = self.recognized()
        response = self.login(token, password="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["led_state"], "red_blink")
        self.assertEqual(self.login(token).status_code, 403)
        self.assertEqual(self.identify().status_code, 429)
        self.now += 11
        new_token = self.identify().json()["challenge_token"]
        self.assertEqual(self.login(new_token).status_code, 403)

    def test_face_failure_resets_identifier_and_holds_red_for_30_seconds(self):
        token = self.identify().json()["challenge_token"]
        response = self.upload(token, matched=False)
        detail = response.json()["detail"]
        self.assertEqual(detail["led_state"], "red_solid")
        self.assertTrue(detail["reset_identification"])
        self.assertEqual(detail["remaining_attempts"], 1)
        self.assertGreaterEqual(detail["camera_off_at"], self.now + 30)
        self.assertEqual(self.identify().status_code, 429)
        self.assertEqual(self.upload(token).status_code, 403)

    def test_two_face_failures_block_five_minutes_even_with_new_client(self):
        first = self.identify().json()["challenge_token"]
        self.upload(first, matched=False)
        self.now += 31
        second = self.identify().json()["challenge_token"]
        response = self.upload(second, matched=False)
        detail = response.json()["detail"]
        self.assertEqual(detail["remaining_attempts"], 0)
        self.assertGreaterEqual(detail["retry_at"], self.now + 300)
        self.client = TestClient(main.app)
        self.assertEqual(self.identify().status_code, 429)
        self.now += 301
        third = self.identify().json()["challenge_token"]
        self.assertEqual(self.upload(third).status_code, 200)

    def test_successful_proof_cannot_be_replayed(self):
        token = self.recognized()
        self.assertEqual(self.login(token).status_code, 200)
        self.assertEqual(self.login(token).status_code, 403)

    def test_proof_is_bound_to_the_identified_account(self):
        token = self.recognized()
        with Session(self.engine) as db:
            user = db.scalar(select(main.User).where(main.User.email == "user@example.com"))
            user.role = "admin"
            db.commit()
        self.assertEqual(self.login(token, email="user@example.com").status_code, 403)

    def test_expired_challenge_is_refused(self):
        token = self.identify().json()["challenge_token"]
        self.now += 121
        self.assertEqual(self.upload(token).status_code, 403)

    def test_rotating_identification_invalidates_previous_challenge(self):
        first = self.identify().json()["challenge_token"]
        second = self.identify().json()["challenge_token"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.upload(first).status_code, 403)

    def test_ordinary_user_also_needs_face_and_keeps_user_permissions(self):
        self.assertEqual(self.login(email="user@example.com").status_code, 403)
        token = self.recognized(email="user@example.com")
        response = self.login(token, email="user@example.com")
        self.assertEqual(response.status_code, 200)
        access_token = response.json()["access_token"]
        claims = jwt.decode(access_token, main.JWT_SECRET, algorithms=["HS256"],
                            audience=main.JWT_AUDIENCE, options={"verify_nbf": False})
        self.assertEqual(claims["role"], "user")
        future = self.now + 32
        class FutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromtimestamp(future, tz=tz)
        with patch("jwt.api_jwt.datetime", FutureDatetime):
            headers = {"Authorization": "Bearer " + access_token}
            self.assertEqual(self.client.get("/auth/me", headers=headers).status_code, 200)
            self.assertEqual(self.client.get("/auth/admin", headers=headers).status_code, 403)

    def test_old_password_only_user_token_is_rejected(self):
        with Session(self.engine) as db:
            user = db.scalar(select(main.User).where(main.User.email == "user@example.com"))
            access_token = main.create_token(user)
        self.assertEqual(self.client.get("/auth/me", headers={
            "Authorization": "Bearer " + access_token}).status_code, 401)

    def register(self, email="new@example.com", extra=None):
        return self.client.post("/auth/register",
            data={"first_name": "Camille", "last_name": "Martin", "email": email, "password": "correct-password", **(extra or {})},
            files={"image": ("enrollment.jpg", b"fixture", "image/jpeg")})

    def test_registration_requires_face_and_creates_no_partial_account(self):
        response = self.client.post("/auth/register",
                                   data={"first_name": "Camille", "last_name": "Martin", "email": "new@example.com", "password": "correct-password"})
        self.assertEqual(response.status_code, 422)
        with patch.object(main, "face_feature", side_effect=ValueError("no face")):
            self.assertEqual(self.register().status_code, 422)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(main.User).where(main.User.email == "new@example.com")))

    def test_registration_creates_user_and_reference_atomically_then_allows_face_login(self):
        import json
        import numpy as np
        values = np.arange(128, dtype=np.float32).reshape(1, -1)
        with patch.object(main, "face_feature", return_value=values):
            response = self.register(extra={"role": "admin"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "user")
        self.assertEqual(len(response.json()["iban"]), 27)
        user_id = response.json()["id"]
        with Session(self.engine) as db:
            self.assertEqual(db.get(main.Account, user_id).balance, 500)
            self.assertEqual(db.get(main.Account, user_id).iban, response.json()["iban"])
            stored = db.get(main.FaceReference, user_id)
            self.assertEqual(json.loads(stored.embedding), values.reshape(-1).tolist())
        token = self.identify(email="new@example.com").json()["challenge_token"]
        with patch.object(main, "compare_face", return_value=True) as compare:
            result = self.client.post("/auth/face/verify", data={"challenge_token": token},
                                     files={"image": ("camera.jpg", b"test", "image/jpeg")})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(compare.call_args.args[0], user_id)
        self.assertEqual(compare.call_args.args[2], values.reshape(-1).tolist())
        self.assertEqual(self.login(token, email="new@example.com").status_code, 200)

    def test_registration_never_replaces_an_existing_account_reference(self):
        with patch.object(main, "face_feature") as feature:
            self.assertEqual(self.register(email="admin@example.com").status_code, 409)
            feature.assert_not_called()
        with Session(self.engine) as db:
            self.assertIsNone(db.get(main.FaceReference, self.user_id))

    def test_reference_insert_failure_rolls_back_the_user_insert(self):
        import numpy as np
        from sqlalchemy import event
        def reject_insert(mapper, connection, target):
            raise RuntimeError("synthetic reference storage failure")
        event.listen(main.FaceReference, "before_insert", reject_insert)
        try:
            self.client = TestClient(main.app, raise_server_exceptions=False)
            with patch.object(main, "face_feature", return_value=np.ones((1, 128))):
                self.assertEqual(self.register().status_code, 500)
            with Session(self.engine) as db:
                self.assertIsNone(db.scalar(select(main.User).where(main.User.email == "new@example.com")))
        finally:
            event.remove(main.FaceReference, "before_insert", reject_insert)

    def test_disabled_mode_and_unavailable_models_fail_closed(self):
        with patch.dict(os.environ, {"FACE_AUTH_MODE": "disabled"}):
            self.assertEqual(self.identify().status_code, 403)
        with patch.object(main, "models_available", return_value=False):
            self.assertEqual(self.identify().status_code, 503)

    def test_invalid_image_consumes_attempt_but_model_failure_does_not(self):
        token = self.identify().json()["challenge_token"]
        with patch.object(main, "compare_face", side_effect=RuntimeError("model unavailable")):
            response = self.client.post("/auth/face/verify", data={"challenge_token": token},
                files={"image": ("camera.jpg", b"x", "image/jpeg")})
        self.assertEqual(response.status_code, 503)
        with patch.object(main, "compare_face", side_effect=ValueError("no face")):
            response = self.client.post("/auth/face/verify", data={"challenge_token": token},
                files={"image": ("camera.jpg", b"x", "image/jpeg")})
        self.assertEqual(response.json()["detail"]["remaining_attempts"], 1)

    def test_startup_assigns_iban_and_initial_credit_to_existing_users_only_once(self):
        from decimal import Decimal
        with patch.object(main, "SessionLocal", lambda: Session(self.engine)):
            main.startup()
            with Session(self.engine) as db:
                account = db.get(main.Account, self.user_id)
                self.assertEqual(account.balance, Decimal("500.00"))
                initial_iban = account.iban
                account.balance = Decimal("23.45")
                db.commit()
            main.startup()
        with Session(self.engine) as db:
            account = db.get(main.Account, self.user_id)
            self.assertEqual(account.balance, Decimal("23.45"))
            self.assertEqual(account.iban, initial_iban)

    def test_registration_retries_an_iban_collision_without_duplicate_credit(self):
        import bank_accounts
        import numpy as np
        from decimal import Decimal
        with Session(self.engine) as db:
            existing = bank_accounts.create_account(db, self.user_id)
            db.commit()
            used_iban = existing.iban
        new_iban = bank_accounts.make_demo_iban()
        with patch.object(bank_accounts, "make_demo_iban", side_effect=[used_iban, new_iban]):
            with patch.object(main, "face_feature", return_value=np.ones((1, 128))):
                response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["iban"], new_iban)
        with Session(self.engine) as db:
            self.assertEqual(db.get(main.Account, self.user_id).balance, Decimal("500.00"))

    def test_invalid_images(self):
        with self.assertRaises(ValueError):
            face_auth.image_from_bytes(b"not an image")
        with self.assertRaises(ValueError):
            face_auth.image_from_bytes(b"x" * (face_auth.MAX_IMAGE_BYTES + 1))

    def test_no_face_or_multiple_faces_rejected(self):
        import numpy as np
        image = io.BytesIO()
        Image.new("RGB", (32, 32)).save(image, format="PNG")
        for faces in (None, np.zeros((2, 15))):
            with patch.object(face_auth, "_models") as models:
                models.return_value[0].detect.return_value = (None, faces)
                with self.assertRaises(ValueError):
                    face_auth.feature(image.getvalue())


if __name__ == "__main__":
    unittest.main()

import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-only-face-secret-at-least-32-bytes-long"
import time
import unittest
from unittest.mock import patch
import jwt
from fastapi.testclient import TestClient
import main


class PaymentFaceAccessTests(unittest.TestCase):
    def test_deleted_user_token_is_revoked(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import StaticPool
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT)"))
        def database():
            with Session(engine) as db:
                yield db
        main.app.dependency_overrides[main.get_db] = database
        token = jwt.encode({"sub": "deleted-user", "role": "admin", "iss": main.JWT_ISSUER,
            "aud": main.JWT_AUDIENCE, "exp": int(time.time()) + 600, "face_verified_until": int(time.time()) + 300,
            "face_method": "image_comparison", "auth_flow": "identifier_face_password"}, main.JWT_SECRET, algorithm="HS256")
        try:
            with patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"}):
                result = TestClient(main.app).get("/admin/payments", headers={"Authorization": "Bearer " + token})
            self.assertEqual(result.status_code, 401)
        finally:
            main.app.dependency_overrides.clear()
            engine.dispose()

    def test_admin_routes_require_unexpired_face_claim(self):
        client = TestClient(main.app)
        now = int(time.time())
        base = {"sub": "test-admin", "role": "admin", "iss": main.JWT_ISSUER,
                "aud": main.JWT_AUDIENCE, "exp": now + 600}
        variants = ({}, {"face_method": "image_comparison", "auth_flow": "identifier_face_password", "face_verified_until": now - 1},
                    {"face_method": "other", "face_verified_until": now + 300})
        with patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"}):
            for extra in variants:
                token = jwt.encode({**base, **extra}, main.JWT_SECRET, algorithm="HS256")
                headers = {"Authorization": "Bearer " + token}
                for path in ("/admin/payments", "/admin/stats"):
                    self.assertEqual(client.get(path, headers=headers).status_code, 401)

    def test_old_password_only_user_cannot_reach_personal_payments(self):
        token = jwt.encode({"sub": "test-user", "role": "user", "iss": main.JWT_ISSUER,
                            "aud": main.JWT_AUDIENCE, "exp": int(time.time()) + 600},
                           main.JWT_SECRET, algorithm="HS256")
        with patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"}):
            response = TestClient(main.app).get("/payments", headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 401)

    def test_valid_face_claim_and_disabled_mode(self):
        from fastapi import HTTPException
        current = {"user_id": "admin", "role": "admin", "face_method": "image_comparison", "auth_flow": "identifier_face_password",
                   "face_verified_until": int(time.time()) + 300}
        with patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"}):
            self.assertEqual(main.require_admin(current), current)
        with patch.dict(os.environ, {"FACE_AUTH_MODE": "disabled"}):
            with self.assertRaises(HTTPException):
                main.require_admin(current)


if __name__ == "__main__":
    unittest.main()

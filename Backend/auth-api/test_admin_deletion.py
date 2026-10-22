"""Deletion uses an isolated database; never deletes a real application account."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "deletion-test-secret-at-least-32-bytes"
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import main


class AdminDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        main.Account.metadata.create_all(self.engine)
        self.ids = ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]
        self.tokens = []
        with Session(self.engine) as db:
            for index, user_id in enumerate(self.ids):
                user = main.User(id=user_id, email=f"delete-test-{index}@example.com", password_hash="unused", role="admin" if index == 0 else "user")
                db.add(user)
                db.flush()
                main.create_account(db, user_id)
                self.tokens.append(main.create_token(user, face_verified_until=int(time.time()) + 300))
            db.add(main.FaceReference(user_id=self.ids[1], embedding="[]"))
            db.add(main.FaceLoginState(user_id=self.ids[1]))
            db.execute(text("CREATE TABLE payments (id TEXT PRIMARY KEY, user_id TEXT)"))
            db.execute(text("INSERT INTO payments VALUES ('history', :user_id)"), {"user_id": self.ids[1]})
            db.commit()
        def database():
            with Session(self.engine) as db:
                yield db
        main.app.dependency_overrides[main.get_db] = database
        self.mode = patch.dict(os.environ, {"FACE_AUTH_MODE": "dev-images"})
        self.mode.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.mode.stop()
        self.engine.dispose()

    def headers(self, index=0):
        return {"Authorization": "Bearer " + self.tokens[index]}

    def test_only_admin_can_delete_and_cannot_delete_self(self):
        self.assertEqual(self.client.delete("/admin/users/" + self.ids[0], headers=self.headers(1)).status_code, 403)
        self.assertEqual(self.client.delete("/admin/users/" + self.ids[0], headers=self.headers()).status_code, 400)

    def test_cleanup_preserves_history_and_revokes_auth(self):
        response = self.client.delete("/admin/users/" + self.ids[1], headers=self.headers())
        self.assertEqual(response.status_code, 204, response.text)
        with Session(self.engine) as db:
            for model in (main.User, main.Account, main.FaceReference, main.FaceLoginState):
                self.assertIsNone(db.get(model, self.ids[1]))
            self.assertEqual(db.execute(text("SELECT count(*) FROM payments")).scalar(), 1)
        self.assertEqual(self.client.get("/auth/me", headers=self.headers(1)).status_code, 401)
        self.assertEqual(self.client.delete("/admin/users/" + self.ids[1], headers=self.headers()).status_code, 404)

    def test_delete_preflight(self):
        result = self.client.options("/admin/users/" + self.ids[1], headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "DELETE", "Access-Control-Request-Headers": "authorization"})
        self.assertEqual(result.status_code, 200)

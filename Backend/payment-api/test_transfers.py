"""Ledger tests use isolated SQLite or per-test schemas in the dedicated transfer_test database."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "transfer-test-only-secret-at-least-32-bytes"
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import (Column, MetaData, String, Numeric, DateTime, Table, create_engine,
                        event, func, select, insert)
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlalchemy.pool import StaticPool

import bank_accounts
import main

A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"
POSTGRES_URL = os.getenv("TEST_DATABASE_URL")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.schema = None
        if POSTGRES_URL:
            if urlparse(POSTGRES_URL).path != "/transfer_test":
                raise RuntimeError("Integration tests require the dedicated transfer_test database")
            self.schema = "test_transfer_" + uuid.uuid4().hex
            self.admin_engine = create_engine(POSTGRES_URL)
            with self.admin_engine.begin() as connection:
                connection.execute(CreateSchema(self.schema))
            self.engine = create_engine(POSTGRES_URL, connect_args={
                "options": f"-csearch_path={self.schema} -cstatement_timeout=10000"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                        poolclass=StaticPool)
        main.Account.metadata.create_all(self.engine)
        main.Base.metadata.create_all(self.engine)
        users = Table("users", MetaData(), Column("id", String(36), primary_key=True),
                      Column("email", String(255), nullable=False),
                      Column("first_name", String(80), default="Camille"), Column("last_name", String(80), default="Martin"))
        users.create(self.engine)
        self.ibans = {uid: bank_accounts.make_demo_iban() for uid in (A, B, C)}
        with main.Session(self.engine) as db:
            db.execute(insert(users), [{"id": uid, "email": f"person{i}@example.com"}
                                      for i, uid in enumerate((A, B, C))])
            for uid in (A, B, C):
                db.add(main.Account(user_id=uid, iban=self.ibans[uid], balance=Decimal("500.00")))
            db.commit()
        def database():
            with main.Session(self.engine) as db:
                yield db
        def identity(request: Request):
            return {"user_id": request.headers.get("X-Test-User", A), "role": "user"}
        main.app.dependency_overrides[main.get_db] = database
        main.app.dependency_overrides[main.get_current_user] = identity
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        if self.schema:
            with self.admin_engine.begin() as connection:
                connection.execute(DropSchema(self.schema, cascade=True))
            self.admin_engine.dispose()

    def send(self, amount="100.00", sender=A, recipient=B, key=None, **extra):
        return self.client.post("/payments", headers={
            "X-Test-User": sender, "Idempotency-Key": key or str(uuid.uuid4()),
        }, json={"amount": amount, "currency": "EUR", "recipient_iban": self.ibans[recipient], **extra})

    def balances(self):
        with main.Session(self.engine) as db:
            return {account.user_id: account.balance for account in db.scalars(select(main.Account))}

    def count(self):
        with main.Session(self.engine) as db:
            return db.scalar(select(func.count()).select_from(main.Payment))

    def test_debit_credit_and_record_are_exact_and_conserve_money(self):
        result = self.send("12.34")
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["status"], "SUCCESS")
        self.assertEqual(result.json()["recipient_user_id"], B)
        balances = self.balances()
        self.assertEqual(balances[A], Decimal("487.66"))
        self.assertEqual(balances[B], Decimal("512.34"))
        self.assertEqual(sum(balances.values()), Decimal("1500.00"))

    def test_entire_balance_can_be_sent_but_never_below_zero(self):
        self.assertEqual(self.send("500").json()["status"], "SUCCESS")
        self.assertEqual(self.send("0.01").json()["status"], "DECLINED")
        self.assertEqual(self.balances()[A], Decimal("0.00"))

    def test_insufficient_funds_are_recorded_without_moving_money(self):
        result = self.send("500.01")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["failure_reason"], "INSUFFICIENT_FUNDS")
        self.assertTrue(all(value == Decimal("500.00") for value in self.balances().values()))
        self.assertEqual(self.count(), 1)

    def test_idempotent_retry_returns_same_record_without_second_debit(self):
        key = str(uuid.uuid4())
        first, second = self.send(key=key), self.send(key=key)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.balances()[A], Decimal("400.00"))

    def test_key_cannot_be_reused_for_another_amount_or_recipient(self):
        key = str(uuid.uuid4())
        self.send(key=key)
        self.assertEqual(self.send("101", key=key).status_code, 409)
        self.assertEqual(self.send(key=key, recipient=C).status_code, 409)
        self.assertEqual(self.count(), 1)

    def test_self_transfer_and_unknown_recipient_are_rejected(self):
        self.assertEqual(self.send(recipient=A).status_code, 400)
        self.assertEqual(self.send(recipient_iban=bank_accounts.make_demo_iban()).status_code, 404)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.balances()[A], Decimal("500.00"))

    def test_invalid_amount_precision_currency_and_iban_are_rejected(self):
        for amount in ("0", "-1", "1.001", "NaN", "10000000000"):
            with self.subTest(amount=amount):
                self.assertEqual(self.send(amount).status_code, 422)
        self.assertEqual(self.send(currency="USD").status_code, 422)
        self.assertEqual(self.send(recipient_iban="FR0012345678901234567890123").status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_missing_idempotency_key_is_rejected(self):
        response = self.client.post("/payments", json={
            "amount": "1", "currency": "EUR", "recipient_iban": self.ibans[B]})
        self.assertEqual(response.status_code, 422)

    def test_database_failure_rolls_back_both_balances_and_the_record(self):
        def fail(mapper, connection, target):
            raise RuntimeError("synthetic ledger storage failure")
        event.listen(main.Payment, "before_insert", fail)
        try:
            self.assertEqual(self.send().status_code, 500)
        finally:
            event.remove(main.Payment, "before_insert", fail)
        self.assertEqual(self.count(), 0)
        self.assertTrue(all(value == Decimal("500.00") for value in self.balances().values()))

    def test_sender_and_recipient_see_transfer_but_other_users_do_not(self):
        payment_id = self.send().json()["id"]
        for user in (A, B):
            history = self.client.get("/payments", headers={"X-Test-User": user})
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()[0]["id"], payment_id)
            self.assertEqual(self.client.get("/payments/" + payment_id,
                             headers={"X-Test-User": user}).status_code, 200)
        self.assertEqual(self.client.get("/payments", headers={"X-Test-User": C}).json(), [])
        self.assertEqual(self.client.get("/payments/" + payment_id,
                         headers={"X-Test-User": C}).status_code, 404)

    def test_recipient_cannot_see_declined_outgoing_attempt(self):
        payment_id = self.send("501").json()["id"]
        self.assertEqual(self.client.get("/payments", headers={"X-Test-User": B}).json(), [])
        self.assertEqual(self.client.get("/payments/" + payment_id,
                         headers={"X-Test-User": B}).status_code, 404)

    def test_account_and_beneficiaries_are_bound_to_current_user(self):
        account = self.client.get("/accounts/me", headers={"X-Test-User": B}).json()
        self.assertEqual(account["iban"], self.ibans[B])
        self.assertEqual(Decimal(account["balance"]), Decimal("500.00"))
        recipients = self.client.get("/beneficiaries").json()
        self.assertEqual({row["user_id"] for row in recipients}, {B, C})
        self.assertTrue(all("@" in row["email"] for row in recipients))

    def test_formatted_lowercase_iban_is_normalized(self):
        formatted = " ".join(self.ibans[B][i:i+4] for i in range(0, 27, 4)).lower()
        result = self.send(recipient_iban=formatted)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["recipient_iban"], self.ibans[B])

    def test_recipient_balance_overflow_does_not_debit_sender(self):
        with main.Session(self.engine) as db:
            db.get(main.Account, B).balance = bank_accounts.MAX_BALANCE
            db.commit()
        self.assertEqual(self.send().status_code, 409)
        self.assertEqual(self.balances()[A], Decimal("500.00"))

    def test_demo_iban_checksum_and_database_uniqueness(self):
        self.assertEqual(bank_accounts.normalize_iban("FR14 2004 1010 0505 0001 3M02 606"),
                         "FR1420041010050500013M02606")
        for value in self.ibans.values():
            self.assertEqual(len(value), 27)
            self.assertEqual(bank_accounts.normalize_iban(value), value)
        from sqlalchemy.exc import IntegrityError
        with main.Session(self.engine) as db:
            db.add(main.Account(user_id="duplicate", iban=self.ibans[A], balance=Decimal("500")))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_one_time_initial_credit_does_not_reset_a_balance(self):
        with main.Session(self.engine) as db:
            existing = db.get(main.Account, A)
            existing.balance = Decimal("37.45")
            db.commit()
        with main.Session(self.engine) as db:
            account = bank_accounts.create_account(db, A)
            self.assertEqual(account.balance, Decimal("37.45"))
            self.assertEqual(account.iban, self.ibans[A])

    def test_legacy_payments_are_preserved_and_migration_is_repeatable(self):
        main.Payment.__table__.drop(self.engine)
        legacy = Table("payments", MetaData(),
                       Column("id", String(36), primary_key=True), Column("user_id", String(36)),
                       Column("amount", Numeric(12, 2)), Column("currency", String(3)),
                       Column("status", String(20)), Column("created_at", DateTime(timezone=True)))
        legacy.create(self.engine)
        from datetime import datetime, timezone
        with self.engine.begin() as connection:
            connection.execute(insert(legacy).values(id="legacy-record", user_id=A, amount=12,
                currency="EUR", status="SUCCESS", created_at=datetime.now(timezone.utc)))
        main.initialize_payment_schema(self.engine)
        main.initialize_payment_schema(self.engine)
        with main.Session(self.engine) as db:
            payment = db.get(main.Payment, "legacy-record")
            self.assertEqual(payment.amount, Decimal("12.00"))
            self.assertIsNone(payment.recipient_user_id)
        self.assertEqual(self.balances()[A], Decimal("500.00"))

    def race(self, jobs):
        barrier = threading.Barrier(len(jobs))
        def before_execute(connection, cursor, statement, params, context, many):
            if "FOR UPDATE" in statement and "accounts" in statement:
                barrier.wait(timeout=10)
        event.listen(self.engine, "before_cursor_execute", before_execute)
        try:
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = [pool.submit(job) for job in jobs]
                return [future.result(timeout=20) for future in futures]
        finally:
            event.remove(self.engine, "before_cursor_execute", before_execute)

    @unittest.skipUnless(POSTGRES_URL, "PostgreSQL row-lock integration test")
    def test_concurrent_spending_cannot_overdraw_or_create_money(self):
        results = self.race([lambda: self.send("400", recipient=B), lambda: self.send("400", recipient=C)])
        self.assertTrue(all(result.status_code == 201 for result in results))
        self.assertEqual(sorted(result.json()["status"] for result in results), ["DECLINED", "SUCCESS"])
        balances = self.balances()
        self.assertEqual(balances[A], Decimal("100.00"))
        self.assertEqual(sum(balances.values()), Decimal("1500.00"))

    @unittest.skipUnless(POSTGRES_URL, "PostgreSQL row-lock integration test")
    def test_concurrent_duplicate_request_debits_once(self):
        key = str(uuid.uuid4())
        results = self.race([lambda: self.send(key=key), lambda: self.send(key=key)])
        self.assertTrue(all(result.status_code == 201 for result in results), [r.text for r in results])
        self.assertEqual(results[0].json()["id"], results[1].json()["id"])
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.balances()[A], Decimal("400.00"))

    @unittest.skipUnless(POSTGRES_URL, "PostgreSQL row-lock integration test")
    def test_opposing_transfers_complete_without_deadlock(self):
        results = self.race([lambda: self.send("100", sender=A, recipient=B),
                             lambda: self.send("200", sender=B, recipient=A)])
        self.assertTrue(all(result.status_code == 201 for result in results), [r.text for r in results])
        self.assertEqual(self.balances()[A], Decimal("600.00"))
        self.assertEqual(self.balances()[B], Decimal("400.00"))


if __name__ == "__main__":
    unittest.main()

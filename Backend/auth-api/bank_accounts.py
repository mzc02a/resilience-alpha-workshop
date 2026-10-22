"""Shared demo account schema (kept identical in auth-api and payment-api). No external banking."""
import re
import secrets
from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

INITIAL_BALANCE = Decimal("500.00")
MAX_BALANCE = Decimal("9999999999.99")
SCHEMA_LOCK = 2627001


class AccountBase(DeclarativeBase):
    pass


class Account(AccountBase):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance >= 0 AND balance <= 9999999999.99", name="ck_accounts_balance"),
    )
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    iban: Mapped[str] = mapped_column(String(27), unique=True, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=INITIAL_BALANCE)


def schema_lock(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK})


def make_demo_iban():
    # Synthetic bank/branch numbers; this identifier is used only by this local lab.
    bank, branch = 99999, 1
    number = secrets.randbelow(10**11)
    rib_key = 97 - (89 * bank + 15 * branch + 3 * number) % 97
    bban = f"{bank:05d}{branch:05d}{number:011d}{rib_key:02d}"
    check = 98 - int(bban + "152700") % 97  # FR00 -> F=15, R=27
    return f"FR{check:02d}{bban}"


def normalize_iban(value):
    normalized = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"FR[0-9]{12}[A-Z0-9]{11}[0-9]{2}", normalized):
        raise ValueError("IBAN francais invalide")
    rearranged = normalized[4:] + normalized[:4]
    digits = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    if int(digits) % 97 != 1:
        raise ValueError("Cle de controle IBAN invalide")
    return normalized


def create_account(db, user_id):
    existing = db.get(Account, user_id)
    if existing is not None:
        return existing
    for _ in range(10):
        try:
            with db.begin_nested():
                account = Account(user_id=user_id, iban=make_demo_iban(), balance=INITIAL_BALANCE)
                db.add(account)
                db.flush()
            return account
        except IntegrityError:
            # A concurrent creation or an IBAN collision cannot credit a second time.
            existing = db.get(Account, user_id)
            if existing is not None:
                return existing
    raise RuntimeError("Impossible d'attribuer un IBAN unique")

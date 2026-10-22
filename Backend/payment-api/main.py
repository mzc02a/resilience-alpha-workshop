import os
import uuid
from time import perf_counter, time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import jwt
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest
from lab_capacity import LabCapacity, CapacityExceeded
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from bank_accounts import Account, MAX_BALANCE, normalize_iban, schema_lock
from sqlalchemy import (
    create_engine,
    String,
    Numeric,
    DateTime,
    select,
    func,
    inspect, text, Index, or_, and_, table, column,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
    Session,
)


# =====================================================
# 1. CONFIGURATION
# =====================================================

DATABASE_URL = os.environ["DATABASE_URL"]
JWT_SECRET = os.environ["JWT_SECRET"]

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "resilience-alpha-auth"
JWT_AUDIENCE = "resilience-alpha"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine)

bearer = HTTPBearer()


# =====================================================
# 2. DATABASE MODELS
# =====================================================

class Base(DeclarativeBase):
    pass


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ux_payments_sender_key", "user_id", "idempotency_key", unique=True),)

    recipient_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sender_iban: Mapped[str | None] = mapped_column(String(27), nullable=True)
    recipient_iban: Mapped[str | None] = mapped_column(String(27), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


# =====================================================
# 3. PYDANTIC SCHEMAS
# =====================================================

class PaymentRequest(BaseModel):
    recipient_iban: str = Field(min_length=15, max_length=64)

    @field_validator("recipient_iban")
    @classmethod
    def canonical_iban(cls, value):
        return normalize_iban(value)

    amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    currency: Literal["EUR"] = "EUR"


class PaymentResponse(BaseModel):
    recipient_user_id: str | None = None
    sender_iban: str | None = None
    recipient_iban: str | None = None
    failure_reason: str | None = None
    id: str
    user_id: str
    amount: Decimal
    currency: str
    status: str
    created_at: datetime


class AdminStatsResponse(BaseModel):
    total_transactions: int
    successful_transactions: int
    declined_transactions: int
    total_successful_amount: Decimal


# =====================================================
# 4. FASTAPI APPLICATION
# =====================================================

app = FastAPI(
    title="RESILIENCE-ALPHA Payment API",
    description="Payment processing and administration service",
    version="3.0",
)

# Fixed status labels keep cardinality bounded; never label by user or payment ID.
http_requests = Counter("http_requests_total", "Completed business HTTP requests", ["status"])
http_duration = Histogram("http_request_duration_seconds", "Business HTTP request duration")
lab_capacity = LabCapacity(enabled=os.getenv("LAB_DOS_ENABLED", "0") == "1")
for status_code in (200, 201, 400, 401, 403, 404, 409, 422, 500, 503):
    http_requests.labels(str(status_code))


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    if request.url.path in ("/metrics", "/health"):
        return await call_next(request)
    started = perf_counter()
    status = 500
    try:
        if request.url.path.startswith("/payments"):
            try:
                async with lab_capacity.operation():
                    response = await call_next(request)
            except CapacityExceeded:
                response = Response('{"detail":"Laboratory payment capacity exhausted"}', status_code=503,
                                    headers={"Content-Type": "application/json", "Retry-After": "1", "X-Lab-Scenario": "limited-capacity"})
        else:
            response = await call_next(request)
        status = response.status_code
        return response
    finally:
        http_requests.labels(str(status)).inc()
        http_duration.observe(perf_counter() - started)


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.get("/lab-profile", include_in_schema=False)
def lab_profile():
    return {"enabled": lab_capacity.enabled, "slots": 2, "simulated_work_seconds": 1.0,
            "description": "Explicit finite-capacity lab simulation; not production capacity"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=[
        "GET",
        "POST",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
    ],
)


# =====================================================
# 5. DATABASE INITIALIZATION
# =====================================================

@app.on_event("startup")
def startup():
    initialize_payment_schema(engine)


def initialize_payment_schema(target_engine):
    with Session(target_engine) as db:
        schema_lock(db)
        Account.metadata.create_all(bind=db.connection())
        Base.metadata.create_all(bind=db.connection())
        columns = {column["name"] for column in inspect(db.connection()).get_columns("payments")}
        for name, size in (("recipient_user_id", 36), ("sender_iban", 27),
                           ("recipient_iban", 27), ("failure_reason", 64), ("idempotency_key", 64)):
            if name not in columns:
                db.execute(text(f"ALTER TABLE payments ADD COLUMN {name} VARCHAR({size})"))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_sender_key ON payments (user_id, idempotency_key)"))
        db.commit()


def get_db():
    with SessionLocal() as db:
        yield db


# =====================================================
# 6. JWT AUTHENTICATION
# =====================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={
                "require": [
                    "sub",
                    "exp",
                    "iss",
                    "aud",
                    "role",
                ]
            },
        )

        until = payload.get("face_verified_until")
        if (os.getenv("FACE_AUTH_MODE", "disabled") != "dev-images"
                or payload.get("auth_flow") != "identifier_face_password"
                or payload.get("face_method") != "image_comparison"
                or type(until) is not int or until <= time()):
            raise ValueError("Facial authentication required")

        user_id = payload.get("sub")
        role = payload.get("role")

        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Invalid user ID")

        if role not in ("user", "admin"):
            raise ValueError("Invalid role")

        users = table("users", column("id"), column("role"))
        existing = db.execute(select(users.c.role).where(users.c.id == user_id)).one_or_none()
        if existing is None or existing.role != role:
            raise ValueError("User deleted or role changed")

        return {
            "user_id": user_id,
            "role": role,
            "face_method": payload.get("face_method"),
            "auth_flow": payload.get("auth_flow"),
            "face_verified_until": payload.get("face_verified_until"),
        }

    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )


# =====================================================
# 7. ADMIN AUTHORIZATION
# =====================================================

def require_admin(
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    until = current_user.get("face_verified_until")
    if (os.getenv("FACE_AUTH_MODE", "disabled") != "dev-images"
            or current_user.get("face_method") != "image_comparison"
            or current_user.get("auth_flow") != "identifier_face_password"
            or type(until) is not int or until <= time()):
        raise HTTPException(403, "Verification faciale requise")
    return current_user


# =====================================================
# 8. HEALTH CHECK
# =====================================================

@app.get("/health")
def health(
    db: Session = Depends(get_db),
):
    db.execute(select(1))

    return {
        "status": "healthy",
        "service": "payment-api",
    }


# =====================================================
# 9. CREATE PAYMENT
# =====================================================

# Account balances and internal transfers are authoritative in this service.
class AccountResponse(BaseModel):
    user_id: str
    iban: str
    balance: Decimal
    currency: Literal["EUR"] = "EUR"


class BeneficiaryResponse(BaseModel):
    first_name: str = ""
    last_name: str = ""
    user_id: str
    email: str
    iban: str


@app.get("/accounts/me", response_model=AccountResponse)
def my_account(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    account = db.get(Account, current_user["user_id"])
    if account is None:
        raise HTTPException(404, "Compte non initialise")
    return account


@app.get("/beneficiaries", response_model=list[BeneficiaryResponse])
def beneficiaries(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    users = table("users", column("id"), column("email"), column("first_name"), column("last_name"))
    rows = db.execute(
        select(Account.user_id, users.c.email, users.c.first_name, users.c.last_name, Account.iban)
        .select_from(Account).join(users, users.c.id == Account.user_id)
        .where(Account.user_id != current_user["user_id"]).order_by(users.c.last_name, users.c.first_name, users.c.email)
    ).all()
    return [BeneficiaryResponse(user_id=row.user_id, email=row.email, first_name=row.first_name, last_name=row.last_name, iban=row.iban) for row in rows]


def previous_transfer(db, user_id, key, data):
    payment = db.scalar(select(Payment).where(Payment.user_id == user_id, Payment.idempotency_key == key))
    if payment is not None:
        if payment.amount != data.amount or payment.recipient_iban != data.recipient_iban:
            raise HTTPException(409, "Cette reference de virement a deja ete utilisee avec d'autres donnees")
    return payment


@app.post("/payments", response_model=PaymentResponse, status_code=201)
def create_payment(
    data: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16,
                                 max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    sender_id = current_user["user_id"]
    previous = previous_transfer(db, sender_id, idempotency_key, data)
    if previous is not None:
        return previous
    destination = db.scalar(select(Account).where(Account.iban == data.recipient_iban))
    if destination is None:
        raise HTTPException(404, "Destinataire introuvable dans le demonstrateur")
    if destination.user_id == sender_id:
        raise HTTPException(400, "Impossible d'envoyer de l'argent a son propre compte")

    # Stable lock ordering prevents opposing transfers from deadlocking.
    # populate_existing refreshes objects already read before waiting for these locks.
    locked = db.scalars(select(Account).where(Account.user_id.in_([sender_id, destination.user_id]))
                        .order_by(Account.user_id).with_for_update()
                        .execution_options(populate_existing=True)).all()
    accounts = {account.user_id: account for account in locked}
    if sender_id not in accounts:
        raise HTTPException(404, "Compte emetteur non initialise")
    sender, recipient = accounts[sender_id], accounts[destination.user_id]
    # A duplicate concurrent request may have completed while this one waited.
    previous = previous_transfer(db, sender_id, idempotency_key, data)
    if previous is not None:
        return previous
    insufficient = sender.balance < data.amount
    if not insufficient and recipient.balance + data.amount > MAX_BALANCE:
        raise HTTPException(409, "Plafond du compte destinataire atteint")
    if not insufficient:
        sender.balance -= data.amount
        recipient.balance += data.amount
    payment = Payment(
        id=str(uuid.uuid4()), user_id=sender_id, recipient_user_id=recipient.user_id,
        sender_iban=sender.iban, recipient_iban=recipient.iban,
        amount=data.amount, currency="EUR", status="DECLINED" if insufficient else "SUCCESS",
        failure_reason="INSUFFICIENT_FUNDS" if insufficient else None,
        idempotency_key=idempotency_key, created_at=datetime.now(timezone.utc),
    )
    db.add(payment)
    # Debit, credit and immutable transfer record belong to ONE database transaction.
    db.commit()
    db.refresh(payment)
    return payment


# =====================================================
# 10. USER PAYMENT HISTORY
# =====================================================

@app.get(
    "/payments",
    response_model=list[PaymentResponse],
)
def list_payments(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    statement = (
        select(Payment)
        .where(
            or_(Payment.user_id == current_user["user_id"],
                and_(Payment.recipient_user_id == current_user["user_id"], Payment.status == "SUCCESS"))
        )
        .order_by(Payment.created_at.desc())
        .limit(100)
    )

    return db.scalars(statement).all()


# =====================================================
# 11. ADMIN - ALL PAYMENTS
# =====================================================

@app.get(
    "/admin/payments",
    response_model=list[PaymentResponse],
)
def admin_list_payments(
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
):
    statement = (
        select(Payment)
        .order_by(Payment.created_at.desc())
        .limit(200)
    )

    return db.scalars(statement).all()


# =====================================================
# 12. ADMIN - GLOBAL STATISTICS
# =====================================================

@app.get(
    "/admin/stats",
    response_model=AdminStatsResponse,
)
def admin_stats(
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
):
    total_transactions = db.scalar(
        select(func.count())
        .select_from(Payment)
    ) or 0

    successful_transactions = db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.status == "SUCCESS")
    ) or 0

    declined_transactions = db.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.status == "DECLINED")
    ) or 0

    total_successful_amount = db.scalar(
        select(func.sum(Payment.amount))
        .where(Payment.status == "SUCCESS")
    ) or Decimal("0.00")

    return AdminStatsResponse(
        total_transactions=total_transactions,
        successful_transactions=successful_transactions,
        declined_transactions=declined_transactions,
        total_successful_amount=total_successful_amount,
    )


# =====================================================
# 13. PAYMENT DETAILS
# =====================================================

@app.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
)
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    statement = select(Payment).where(
        Payment.id == payment_id,
        or_(Payment.user_id == current_user["user_id"],
            and_(Payment.recipient_user_id == current_user["user_id"], Payment.status == "SUCCESS")) ,
    )

    payment = db.scalar(statement)

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment

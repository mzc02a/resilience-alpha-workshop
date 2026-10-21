import os
import uuid
from time import perf_counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import jwt
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest
from lab_capacity import LabCapacity, CapacityExceeded
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    String,
    Numeric,
    DateTime,
    select,
    func,
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
    amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    currency: Literal["EUR"] = "EUR"


class PaymentResponse(BaseModel):
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
    ],
)


# =====================================================
# 5. DATABASE INITIALIZATION
# =====================================================

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


def get_db():
    with SessionLocal() as db:
        yield db


# =====================================================
# 6. JWT AUTHENTICATION
# =====================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
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

        user_id = payload.get("sub")
        role = payload.get("role")

        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Invalid user ID")

        if role not in ("user", "admin"):
            raise ValueError("Invalid role")

        return {
            "user_id": user_id,
            "role": role,
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

@app.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=201,
)
def create_payment(
    data: PaymentRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Business simulation only:
    # Payments <= 1000 EUR are accepted.
    # Payments > 1000 EUR are declined.

    payment_status = (
        "SUCCESS"
        if data.amount <= Decimal("1000")
        else "DECLINED"
    )

    payment = Payment(
        id=str(uuid.uuid4()),
        user_id=current_user["user_id"],
        amount=data.amount,
        currency=data.currency,
        status=payment_status,
        created_at=datetime.now(timezone.utc),
    )

    db.add(payment)
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
            Payment.user_id == current_user["user_id"]
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
        Payment.user_id == current_user["user_id"],
    )

    payment = db.scalar(statement)

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    return payment

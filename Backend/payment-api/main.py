import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import jwt
from fastapi import FastAPI, Depends, HTTPException
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
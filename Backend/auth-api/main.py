import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import create_engine, String, select
from sqlalchemy.exc import IntegrityError
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
JWT_EXPIRATION_MINUTES = 30

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine)

password_hash = PasswordHash.recommended()
bearer = HTTPBearer()


# =====================================================
# 2. DATABASE MODELS
# =====================================================

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="user",
        nullable=False,
    )


# =====================================================
# 3. PYDANTIC SCHEMAS
# =====================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=12,
        max_length=128,
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RoleUpdateRequest(BaseModel):
    role: Literal["user", "admin"]


# =====================================================
# 4. FASTAPI APPLICATION
# =====================================================

app = FastAPI(
    title="RESILIENCE-ALPHA Auth API",
    description="Authentication and role management service",
    version="2.0",
)

Instrumentator(
    excluded_handlers=["/metrics"],
).instrument(app).expose(app, include_in_schema=False)

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
        "PATCH",
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
# 6. JWT TOKEN GENERATION
# =====================================================

def create_token(user: User):
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user.id,
        "role": user.role,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(
            minutes=JWT_EXPIRATION_MINUTES
        ),
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


# =====================================================
# 7. AUTHENTICATION DEPENDENCY
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
                ]
            },
        )

        user_id = payload.get("sub")
        token_role = payload.get("role")

        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Invalid user ID")

        user = db.get(User, user_id)

        if user is None:
            raise ValueError("User not found")

        if user.role != token_role:
            raise ValueError("User role has changed")

        return user

    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )


# =====================================================
# 8. ADMIN AUTHORIZATION
# =====================================================

def require_admin(
    user: User = Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    return user


# =====================================================
# 9. HEALTH CHECK
# =====================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "auth-api",
    }


# =====================================================
# 10. REGISTER USER
# =====================================================

@app.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=201,
)
def register(
    data: RegisterRequest,
    db: Session = Depends(get_db),
):
    email = str(data.email).lower()

    existing_user = db.scalar(
        select(User).where(User.email == email)
    )

    if existing_user:
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=password_hash.hash(
            data.password
        ),
        role="user",
    )

    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    db.refresh(user)

    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
    )


# =====================================================
# 11. LOGIN USER
# =====================================================

@app.post(
    "/auth/login",
    response_model=TokenResponse,
)
def login(
    data: LoginRequest,
    db: Session = Depends(get_db),
):
    email = str(data.email).lower()

    user = db.scalar(
        select(User).where(User.email == email)
    )

    if (
        user is None
        or not password_hash.verify(
            data.password,
            user.password_hash,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    token = create_token(user)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
    )


# =====================================================
# 12. CURRENT USER PROFILE
# =====================================================

@app.get(
    "/auth/me",
    response_model=UserResponse,
)
def me(
    user: User = Depends(get_current_user),
):
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
    )


# =====================================================
# 13. ADMIN ACCESS TEST
# =====================================================

@app.get("/auth/admin")
def admin_only(
    admin: User = Depends(require_admin),
):
    return {
        "message": "Admin access granted",
        "admin_email": admin.email,
    }


# =====================================================
# 14. LIST ALL USERS - ADMIN ONLY
# =====================================================

@app.get(
    "/admin/users",
    response_model=list[UserResponse],
)
def list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(
        select(User).order_by(User.email)
    ).all()

    return [
        UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
        )
        for user in users
    ]


# =====================================================
# 15. UPDATE USER ROLE - ADMIN ONLY
# =====================================================

@app.patch(
    "/admin/users/{user_id}/role",
    response_model=UserResponse,
)
def update_user_role(
    user_id: str,
    data: RoleUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Prevent an admin from removing their own role
    if user.id == admin.id and data.role != "admin":
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own admin role",
        )

    user.role = data.role

    db.commit()
    db.refresh(user)

    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
    )

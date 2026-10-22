import os
import uuid
import logging
import time
import hashlib
import math
import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from face_auth import compare as compare_face, reference_path, models_available, MAX_IMAGE_BYTES, feature as face_feature
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator
from bank_accounts import Account, create_account, schema_lock
from pwdlib import PasswordHash
from sqlalchemy import create_engine, String, Integer, Float, Boolean, Text, ForeignKey, select, inspect, text
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

    first_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="user",
        nullable=False,
    )


class FaceReference(Base):
    __tablename__ = "face_references"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    embedding: Mapped[str] = mapped_column(Text, nullable=False)


class FaceLoginState(Base):
    __tablename__ = "face_login_states"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_at: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    blocked_until: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    challenge_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    challenge_expires: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    face_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# =====================================================
# 3. PYDANTIC SCHEMAS
# =====================================================

class IdentifyRequest(BaseModel):
    email: EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    face_challenge: str | None = Field(default=None, max_length=128)


class NameUpdateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)

    @field_validator("first_name", "last_name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Nom et prenom obligatoires")
        return value


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    first_name: str = ""
    last_name: str = ""
    iban: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    face_verified_until: int = 0
    backoffice_ready_at: int = 0


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
    with SessionLocal() as db:
        schema_lock(db)
        Base.metadata.create_all(bind=db.connection())
        columns = {item["name"] for item in inspect(db.connection()).get_columns("users")}
        for name in ("first_name", "last_name"):
            if name not in columns:
                db.execute(text(f"ALTER TABLE users ADD COLUMN {name} VARCHAR(80) NOT NULL DEFAULT ''"))
        Account.metadata.create_all(bind=db.connection())
        missing = db.scalars(select(User.id).outerjoin(Account, Account.user_id == User.id)
                            .where(Account.user_id.is_(None))).all()
        for user_id in missing:
            create_account(db, user_id)
        db.commit()


def get_db():
    with SessionLocal() as db:
        yield db


# =====================================================
# 6. JWT TOKEN GENERATION
# =====================================================

def create_token(user: User, face_verified_until=0, expires_at=None, backoffice_ready_at=0):
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

    if expires_at is not None:
        payload["exp"] = expires_at
    if face_verified_until:
        payload["face_verified_until"] = face_verified_until
        payload["face_method"] = "image_comparison"
        payload["auth_flow"] = "identifier_face_password"
        payload["backoffice_ready_at"] = backoffice_ready_at
        if backoffice_ready_at:
            payload["nbf"] = backoffice_ready_at

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

        until = payload.get("face_verified_until")
        if (os.getenv("FACE_AUTH_MODE", "disabled") != "dev-images"
                or payload.get("auth_flow") != "identifier_face_password"
                or payload.get("face_method") != "image_comparison"
                or type(until) is not int or until <= time.time()):
            raise ValueError("Facial authentication required")

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

def decode_face_token(credentials):
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                          issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid or expired token") from exc


def require_admin(
    user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    payload = decode_face_token(credentials)
    until = payload.get("face_verified_until")
    if (os.getenv("FACE_AUTH_MODE", "disabled") != "dev-images"
            or payload.get("face_method") != "image_comparison"
            or payload.get("auth_flow") != "identifier_face_password"
            or type(until) is not int or until <= time.time()):
        raise HTTPException(403, "Verification faciale requise")
    return user


def has_face_reference(db, user_id):
    return db.get(FaceReference, user_id) is not None or reference_path(user_id).is_file()


@app.get("/auth/face/status")
def face_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"mode": os.getenv("FACE_AUTH_MODE", "disabled"),
            "models_ready": models_available(), "enrolled": has_face_reference(db, user.id),
            "user_id": user.id}


def require_face_mode():
    if os.getenv("FACE_AUTH_MODE", "disabled") != "dev-images":
        raise HTTPException(403, "Verification faciale desactivee")


def flow_error(code, message, *, retry_at=0, camera_off_at=0, led="off", remaining=None):
    detail = {"message": message, "reset_identification": True,
              "retry_at": retry_at, "camera_off_at": camera_off_at,
              "led_state": led, "led_until": camera_off_at or retry_at}
    if remaining is not None:
        detail["remaining_attempts"] = remaining
    raise HTTPException(code, detail=detail)


def locked_state(db, user):
    # Lock the account too: concurrent creation/rotation of challenges is serialized.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    state = db.get(FaceLoginState, user.id)
    if state is None:
        state = FaceLoginState(user_id=user.id, failures=0, retry_at=0,
                               blocked_until=0, challenge_expires=0, face_verified=False)
        db.add(state)
        db.flush()
    else:
        db.refresh(state)
    return state


def check_retry(state, now):
    if state.blocked_until > now:
        flow_error(429, "Identification bloquee pendant cinq minutes",
                   retry_at=state.blocked_until)
    if state.retry_at > now:
        flow_error(429, "Attendre avant de recommencer", retry_at=state.retry_at)
    if state.blocked_until:
        state.failures = 0
        state.blocked_until = 0


def challenge_state(db, token):
    if not token or len(token) > 128:
        flow_error(403, "Recommencer l'identification")
    digest = hashlib.sha256(token.encode()).hexdigest()
    candidate = db.scalar(select(FaceLoginState).where(FaceLoginState.challenge_hash == digest))
    if candidate is None:
        flow_error(403, "Identification expiree ou deja utilisee")
    user = db.get(User, candidate.user_id)
    if user is None or user.role not in ("admin", "user"):
        flow_error(403, "Identification non autorisee")
    state = locked_state(db, user)
    if state.challenge_hash != digest or state.challenge_expires <= time.time():
        flow_error(403, "Identification expiree ou deja utilisee")
    check_retry(state, time.time())
    return user, state


def invalidate_challenge(state):
    state.challenge_hash = None
    state.challenge_expires = 0
    state.face_verified = False


def reject_face(db, user, state):
    now = time.time()
    state.failures += 1
    invalidate_challenge(state)
    state.retry_at = math.ceil(now + 30)
    if state.failures >= 2:
        state.blocked_until = math.ceil(now + 300)
    retry_at = max(state.retry_at, state.blocked_until)
    camera_off_at = state.retry_at
    remaining = max(0, 2 - state.failures)
    db.commit()
    logging.getLogger("uvicorn.error").info("face_attempt user_id=%s state=denied", user.id)
    flow_error(403, "Visage non reconnu" if remaining else "Deux echecs : blocage de cinq minutes",
               retry_at=retry_at, camera_off_at=camera_off_at,
               led="red_solid", remaining=remaining)


@app.post("/auth/backoffice/identify")
def identify(data: IdentifyRequest, db: Session = Depends(get_db)):
    require_face_mode()
    user = db.scalar(select(User).where(User.email == str(data.email).lower()))
    if user is None or user.role not in ("admin", "user"):
        flow_error(403, "Identification indisponible pour ce compte")
    state = locked_state(db, user)
    check_retry(state, time.time())
    if not models_available():
        raise HTTPException(503, "Modeles faciaux indisponibles")
    if not has_face_reference(db, user.id):
        raise HTTPException(409, "Reference faciale absente : contacter l'operateur")
    token = secrets.token_urlsafe(32)
    state.challenge_hash = hashlib.sha256(token.encode()).hexdigest()
    state.challenge_expires = math.ceil(time.time() + 120)
    state.face_verified = False
    expires_at = state.challenge_expires
    db.commit()
    return {"challenge_token": token, "expires_at": expires_at, "led_state": "off"}


@app.post("/auth/face/verify")
def face_verify(
    image: UploadFile = File(...),
    challenge_token: str = Form(..., min_length=20, max_length=128),
    db: Session = Depends(get_db),
):
    require_face_mode()
    user, state = challenge_state(db, challenge_token)
    if state.face_verified:
        flow_error(409, "Visage deja verifie : saisir le mot de passe")
    try:
        data = image.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image trop volumineuse : maximum 4 Mo")
        stored = db.get(FaceReference, user.id)
        if stored is not None:
            matched = compare_face(user.id, data, json.loads(stored.embedding))
        else:
            matched = compare_face(user.id, data)
    except ValueError:
        # No face, several faces and malformed images all consume an attempt.
        reject_face(db, user, state)
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger("uvicorn.error").error("face_attempt user_id=%s state=service_error", user.id)
        raise HTTPException(503, "Verification faciale indisponible") from exc
    finally:
        image.file.close()
    if not matched:
        reject_face(db, user, state)
    state.face_verified = True
    state.challenge_expires = math.ceil(time.time() + 120)
    expires_at = state.challenge_expires
    db.commit()
    logging.getLogger("uvicorn.error").info("face_attempt user_id=%s state=recognized", user.id)
    # A challenge is not a session token and cannot authorize any business endpoint.
    return {"face_challenge": challenge_token, "expires_at": expires_at,
            "led_state": "green_blink"}


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
    email: EmailStr = Form(...),
    first_name: str = Form(..., min_length=1, max_length=80),
    last_name: str = Form(..., min_length=1, max_length=80),
    password: str = Form(..., min_length=12, max_length=128),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    require_face_mode()
    try:
        names = NameUpdateRequest(first_name=first_name, last_name=last_name)
    except ValueError:
        raise HTTPException(422, "Nom et prenom obligatoires (80 caracteres maximum)")
    email = str(email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    try:
        data = image.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image trop volumineuse : maximum 4 Mo")
        embedding = json.dumps(face_feature(data).reshape(-1).tolist(), allow_nan=False)
    except ValueError as exc:
        raise HTTPException(422, "Un seul visage net doit etre visible pour l'inscription") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Enrolement facial indisponible") from exc
    finally:
        image.file.close()
    user = User(id=str(uuid.uuid4()), email=email, first_name=names.first_name, last_name=names.last_name,
                password_hash=password_hash.hash(password), role="user")
    try:
        db.add(user)
        db.flush()
        db.add(FaceReference(user_id=user.id, embedding=embedding))
        account = create_account(db, user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered")
    # Account and reference are committed atomically. The raw picture is not retained.
    db.refresh(user)
    return UserResponse(id=user.id, email=user.email, first_name=user.first_name, last_name=user.last_name, role=user.role, iban=account.iban)


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

    if user is None:
        raise HTTPException(401, "Invalid email or password")

    require_face_mode()
    verified_user, state = challenge_state(db, data.face_challenge)
    if verified_user.id != user.id or not state.face_verified:
        flow_error(403, "Verifier le visage avant de saisir le mot de passe")
    invalidate_challenge(state)
    if not password_hash.verify(data.password, user.password_hash):
        state.retry_at = math.ceil(time.time() + 10)
        retry_at = state.retry_at
        db.commit()
        logging.getLogger("uvicorn.error").info("face_attempt user_id=%s state=password_denied", user.id)
        flow_error(401, "Mot de passe incorrect : recommencer l'identification",
                   retry_at=retry_at, led="red_blink")
    ready_at = math.ceil(time.time() + 30)
    expires_at = ready_at + JWT_EXPIRATION_MINUTES * 60
    state.failures = 0
    state.blocked_until = 0
    state.retry_at = ready_at
    db.commit()
    logging.getLogger("uvicorn.error").info("face_attempt user_id=%s state=authenticated", user.id)
    return TokenResponse(
        access_token=create_token(user, expires_at, expires_at, ready_at),
        face_verified_until=expires_at, backoffice_ready_at=ready_at,
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
    db: Session = Depends(get_db),
):
    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name, last_name=user.last_name,
        role=user.role,
        iban=db.get(Account, user.id).iban if db.get(Account, user.id) else None,
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

    ibans = {account.user_id: account.iban for account in db.scalars(select(Account)).all()}
    return [
        UserResponse(
            id=user.id,
            email=user.email,
        first_name=user.first_name, last_name=user.last_name,
            role=user.role,
            iban=ibans.get(user.id),
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
        first_name=user.first_name, last_name=user.last_name,
        role=user.role,
        iban=db.get(Account, user.id).iban if db.get(Account, user.id) else None,
    )


@app.patch("/auth/me", response_model=UserResponse)
def update_my_names(data: NameUpdateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.first_name, user.last_name = data.first_name, data.last_name
    db.add(user)
    db.commit()
    return me(user, db)

"""
Router de autenticación.

POST /auth/login    — verifica email/contraseña y devuelve un JWT Bearer token.
POST /auth/refresh  — renueva el JWT a partir de un token vigente.

El token puede usarse en cualquier endpoint protegido mediante:
    Authorization: Bearer <token>
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.operador import Operador

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer_scheme = HTTPBearer()


# ── Helpers de contraseña ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hashea una contraseña con PBKDF2-HMAC-SHA256 (estándar biblioteca standard)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"pbkdf2:sha256:260000${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifica una contraseña contra su hash almacenado."""
    try:
        algo_part, salt, dk_hex = stored_hash.split("$")
        _, hash_name, iterations_str = algo_part.split(":")
        iterations = int(iterations_str)
        dk = hashlib.pbkdf2_hmac(hash_name, password.encode(), salt.encode(), iterations)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── Helpers de JWT ────────────────────────────────────────────────────────────

def create_access_token(sub: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": sub, "exp": expire},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


# ── Dependencia de autenticación ──────────────────────────────────────────────

def get_current_operador(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer_scheme)],
    db: Session = Depends(get_db),
) -> Operador:
    """FastAPI Depends — valida el JWT y retorna el Operador activo."""
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        email: str | None = payload.get("sub")
        if email is None:
            raise jwt.InvalidTokenError("sub ausente")
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )

    operador = (
        db.query(Operador)
        .filter(Operador.email == email, Operador.activo.is_(True))
        .first()
    )
    if operador is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operador no encontrado o inactivo",
        )
    return operador


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Autentica un operador y devuelve un JWT Bearer token."""
    operador = (
        db.query(Operador)
        .filter(Operador.email == body.email, Operador.activo.is_(True))
        .first()
    )
    if operador is None or not verify_password(body.password, operador.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
        )

    token = create_access_token(sub=operador.email)
    logger.info("Operador '%s' inició sesión", operador.email)
    return LoginResponse(access_token=token)


@router.post("/refresh", response_model=LoginResponse)
def refresh(operador: Operador = Depends(get_current_operador)):
    """Renueva el JWT de un operador autenticado y devuelve uno nuevo."""
    token = create_access_token(sub=operador.email)
    logger.info("Operador '%s' renovó su sesión", operador.email)
    return LoginResponse(access_token=token)

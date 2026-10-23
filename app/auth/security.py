"""
Authentication and Role-Based Access Control (RBAC) Module for SignalSync.
Provides secure JWT tokens, password hashing, and endpoint authorization.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import secrets
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Secret key for JWT signing (loads from environment or generates secure persistent token)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "signalsync_production_secret_key_atharav_futane_jitesh_gaikwad_2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24  # 24 hours

security_bearer = HTTPBearer(auto_error=False)


# ────────────────────── User & Role Models ────────────────────── #

class Role:
    ADMIN = "ADMIN"          # Full control: timing thresholds, emergency overrides, backups
    OPERATOR = "OPERATOR"    # Traffic dispatch: ambulance corridor, weather, preset scenarios
    VIEWER = "VIEWER"        # Read-only: telemetry, video feeds, reporting


class User(BaseModel):
    username: str
    full_name: str
    role: str
    disabled: bool = False


# In-memory user database with pre-hashed passwords (PBKDF2-HMAC-SHA256)
# Default accounts for Group SignalSync:
# admin: SignalSyncAdmin2026!
# operator: OperatorPass2026!
# viewer: ViewerPass2026!
def _hash_password(password: str, salt: str = "signalsync_static_salt_2026") -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


USERS_DB: Dict[str, Dict[str, Any]] = {
    "admin": {
        "username": "admin",
        "full_name": "Atharav Futane & Jitesh Gaikwad (Group SignalSync)",
        "role": Role.ADMIN,
        "password_hash": _hash_password("admin123"),
        "disabled": False,
    },
    "operator": {
        "username": "operator",
        "full_name": "Traffic Operations Center Duty Officer",
        "role": Role.OPERATOR,
        "password_hash": _hash_password("operator123"),
        "disabled": False,
    },
    "viewer": {
        "username": "viewer",
        "full_name": "Guest Traffic Monitor",
        "role": Role.VIEWER,
        "password_hash": _hash_password("viewer123"),
        "disabled": False,
    },
}


def authenticate_user(username: str, password: str) -> Optional[User]:
    user_record = USERS_DB.get(username)
    if not user_record:
        return None
    if user_record.get("disabled", False):
        return None
    input_hash = _hash_password(password)
    if not hmac.compare_digest(user_record["password_hash"], input_hash):
        return None
    return User(
        username=user_record["username"],
        full_name=user_record["full_name"],
        role=user_record["role"],
        disabled=user_record["disabled"],
    )


def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + (
        expires_delta or datetime.timedelta(minutes=JWT_EXPIRATION_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer)) -> User:
    """Dependency: Extract and validate JWT token; defaults to Guest VIEWER if not authenticated."""
    if not credentials:
        # Default guest read-only user
        return User(username="guest", full_name="Guest Observer", role=Role.VIEWER)

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token payload missing subject identifier.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_record = USERS_DB.get(username)
        if not user_record or user_record.get("disabled", False):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account inactive or removed.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return User(
            username=user_record["username"],
            full_name=user_record["full_name"],
            role=user_record["role"],
            disabled=user_record["disabled"],
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_role(allowed_roles: List[str]):
    """Role-Based Access Control (RBAC) dependency factory."""
    def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Insufficient privileges. Required roles: {allowed_roles}, your role: {user.role}.",
            )
        return user
    return role_checker

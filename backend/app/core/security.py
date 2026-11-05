"""
AI-Powered Multilingual Shopping Intelligence Platform
Cryptographic & Authentication Security Utilities.
Provides password hashing via native bcrypt (safe against 72-byte truncation)
and signed JWT access tokens using HS256 and centralized settings.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import uuid
import bcrypt
import jwt
from backend.app.core.config import get_settings

JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
MIN_PASSWORD_LENGTH_BYTES = 8
MAX_PASSWORD_LENGTH_BYTES = 72


def validate_password_length(password: str) -> None:
    """
    Validate password length against safety constraints.
    Enforces minimum 8 bytes and strict 72-byte upper limit to prevent bcrypt truncation.
    """
    if not password:
        raise ValueError("Password is required.")
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) < MIN_PASSWORD_LENGTH_BYTES:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH_BYTES} characters long.")
    if len(pw_bytes) > MAX_PASSWORD_LENGTH_BYTES:
        raise ValueError(f"Password cannot exceed {MAX_PASSWORD_LENGTH_BYTES} bytes in UTF-8 encoding.")


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using native bcrypt.
    Enforces strict 72-byte boundary before hashing.
    """
    validate_password_length(password)
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a bcrypt hash.
    Safely rejects passwords exceeding 72 bytes without silent truncation.
    """
    if not plain_password or not hashed_password:
        return False
    pw_bytes = plain_password.encode("utf-8")
    if len(pw_bytes) > MAX_PASSWORD_LENGTH_BYTES:
        return False
    try:
        return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    user_id: str,
    email: str,
    name: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, int]:
    """
    Generate a signed JWT access token containing standard subject and token identifier.
    Returns: (encoded_jwt_token, jti, expires_in_seconds)
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    delta = expires_delta or timedelta(minutes=DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = now + delta
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email.strip().lower(),
        "name": name,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    token = jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )
    expires_in_seconds = int(delta.total_seconds())
    return token, jti, expires_in_seconds


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Authoritatively verify and decode a JWT access token.
    Enforces explicit HS256 algorithm and presence of required claims.
    Raises jwt.ExpiredSignatureError, jwt.InvalidTokenError on failure.
    """
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[JWT_ALGORITHM],
        options={
            "require": ["exp", "sub", "jti", "iat"],
            "verify_exp": True,
            "verify_iat": True,
        },
    )
    return payload


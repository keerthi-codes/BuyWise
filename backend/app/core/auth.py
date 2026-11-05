"""
AI-Powered Multilingual Shopping Intelligence Platform
Authentication Dependencies for FastAPI.
Provides strict get_current_user and optional get_current_user_optional.
Mandatory rule: If an Authorization token is supplied, it must be valid and non-revoked.
Only requests with no authentication token are treated as guest requests.
"""

import logging
from typing import Annotated
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import decode_access_token
from backend.app.models.user import User, RevokedToken

logger = logging.getLogger(__name__)

# HTTPBearer with auto_error=False to allow optional guest handling
security_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security_bearer)],
    db: Session = Depends(get_db),
) -> User:
    """
    Authoritative authentication dependency.
    Requires a valid, non-expired, non-revoked Bearer token.
    Raises 401 Unauthorized on any validation failure.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jti = payload.get("jti")
    user_id = payload.get("sub")

    if not jti or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token payload.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Server-side token revocation check
    revoked = db.scalar(select(RevokedToken).where(RevokedToken.jti == jti))
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_user_optional(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security_bearer)],
    db: Session = Depends(get_db),
) -> User | None:
    """
    Optional authentication dependency for mixed guest/authenticated routes.
    - If NO Authorization header is provided: returns None (guest request).
    - If Authorization header IS provided: it MUST be valid. Any invalid, expired,
      or revoked token raises 401 Unauthorized.
    """
    if credentials is None or not credentials.credentials:
        return None

    # When token is present, enforce strict validation
    return get_current_user(credentials=credentials, db=db)


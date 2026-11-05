"""
AI-Powered Multilingual Shopping Intelligence Platform
User Authentication & Session Management API Endpoints.
Routes:
  POST /api/auth/register     - Register new account & return JWT
  POST /api/auth/login        - Authenticate credentials & return JWT
  GET  /api/auth/me           - Retrieve authenticated user profile
  POST /api/auth/logout       - Server-side JWT revocation
  POST /api/auth/link-session - Atomically link guest session to authenticated user
  POST /api/auth/session-token- Issue or verify guest proof-of-possession token
"""

import logging
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.auth import get_current_user, security_bearer
from backend.app.models.user import User
from backend.app.schemas.auth import (
    UserRegisterRequest,
    UserLoginRequest,
    UserResponse,
    TokenResponse,
    LinkSessionRequest,
    LinkSessionResponse,
    SessionCredentialResponse,
    SessionTokenRequest,
)
from backend.app.services.auth_service import (
    AuthService,
    DuplicateEmailError,
    InvalidCredentialsError,
    SessionNotFoundError,
    SessionOwnershipError,
    SessionProofOfPossessionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["User Authentication"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register New User Account",
    description="Create a secure user account, hash password via bcrypt, issue JWT access token, and optionally claim guest session.",
)
def register(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Register a new user account."""
    try:
        user, token, expires_in, linked_sid, linked_count = AuthService.register_user(
            db=db,
            request=payload,
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            user=UserResponse.model_validate(user),
            linked_session_id=linked_sid,
            linked_orders_count=linked_count,
        )
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except (SessionProofOfPossessionError, SessionOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate User",
    description="Validate user credentials with bcrypt, issue JWT access token, and optionally link active guest session.",
)
def login(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticate registered user."""
    try:
        user, token, expires_in, linked_sid, linked_count, session_link_err = AuthService.login_user(
            db=db,
            request=payload,
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            user=UserResponse.model_validate(user),
            linked_session_id=linked_sid,
            linked_orders_count=linked_count,
            session_link_error=session_link_err,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (SessionProofOfPossessionError, SessionOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get Current User Profile",
    description="Retrieve the authenticated user's profile. Requires valid, non-revoked Bearer token.",
)
def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Retrieve profile of currently authenticated user."""
    return UserResponse.model_validate(current_user)


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Server-Side Token Revocation",
    description="Invalidate current JWT access token server-side by adding jti to revoked_tokens ledger.",
)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(security_bearer)],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Revoke JWT token on the server."""
    if credentials and credentials.credentials:
        AuthService.revoke_token(db=db, token=credentials.credentials, user=current_user)
    return {"detail": "Logged out successfully. Token has been revoked."}


@router.post(
    "/link-session",
    response_model=LinkSessionResponse,
    summary="Claim Guest Shopping Session",
    description="Atomically link an unassigned guest session and its historical orders to the authenticated account.",
)
def link_session(
    payload: LinkSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LinkSessionResponse:
    """Claim a guest shopping session under authenticated account."""
    try:
        session_id, count = AuthService.link_guest_session(
            db=db,
            user=current_user,
            session_id=payload.guest_session_id,
            session_token=payload.guest_session_token,
            session_identifier=payload.session_identifier,
        )
        return LinkSessionResponse(
            success=True,
            session_id=session_id,
            orders_linked_count=count,
            message=f"Session successfully linked. {count} order(s) claimed.",
        )
    except SessionOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except SessionProofOfPossessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.post(
    "/session-token",
    response_model=SessionCredentialResponse,
    summary="Obtain Guest Proof-of-Possession Credential",
    description="Bootstrap a fresh guest session or verify credentials for an existing session. Never exposes tokens without proof of possession.",
)
def get_session_token(
    payload: SessionTokenRequest | None = None,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
) -> SessionCredentialResponse:
    """Issue fresh proof-of-possession credentials or verify existing session possession."""
    sid = payload.guest_session_id if payload else None
    ident = payload.session_identifier if payload else None
    token = (payload.guest_session_token if payload else None) or x_guest_session_token

    # Reject attempts to query existing sessions without credentials
    if (sid or ident) and not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Guest session credential required to access session token.",
        )

    try:
        session = AuthService.get_or_issue_session_credentials(
            db=db,
            session_id=sid,
            session_token=token,
            session_identifier=ident,
        )
        return SessionCredentialResponse(
            session_id=session.id,
            session_identifier=session.session_identifier,
            session_token=session.session_token or "",
        )
    except SessionProofOfPossessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


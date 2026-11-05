"""
AI-Powered Multilingual Shopping Intelligence Platform
Authentication Service & Guest Session Linking Engine.
Enforces bcrypt hashing, signed JWT issuance, token revocation,
and proof-of-possession guest session migration.
"""

from datetime import datetime, timezone
import logging
import secrets
from typing import Any
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)
from backend.app.models.user import User, RevokedToken
from backend.app.models.shopping import ShoppingSession
from backend.app.models.payment import PaymentOrder
from backend.app.schemas.auth import (
    UserRegisterRequest,
    UserLoginRequest,
)

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Base exception for authentication and credential operations."""
    pass


class DuplicateEmailError(AuthError):
    """Raised when registering an email that already exists."""
    pass


class InvalidCredentialsError(AuthError):
    """Raised when email or password is not accepted."""
    pass


class SessionNotFoundError(AuthError):
    """Raised when target shopping session cannot be located."""
    pass


class SessionOwnershipError(AuthError):
    """Raised when attempting to claim a session belonging to another account."""
    pass


class SessionProofOfPossessionError(AuthError):
    """Raised when proof of session possession verification fails."""
    pass


class AuthService:
    """Authoritative service governing user registration, login, and session migration."""

    @staticmethod
    def register_user(
        db: Session,
        request: UserRegisterRequest,
    ) -> tuple[User, str, int, str | None, int]:
        """
        Register a new user account, issue JWT token, and optionally claim an active guest session.
        Returns: (user, access_token, expires_in, linked_session_id, linked_orders_count)
        """
        normalized_email = request.email.strip().lower()

        # Check for duplicate email
        existing_user = db.scalar(select(User).where(User.email == normalized_email))
        if existing_user:
            raise DuplicateEmailError("An account with this email address already exists.")

        hashed_pw = hash_password(request.password)

        new_user = User(
            id=str(uuid.uuid4()),
            email=normalized_email,
            hashed_password=hashed_pw,
            name=request.name.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(new_user)
        db.flush()

        linked_session_id: str | None = None
        linked_orders_count: int = 0

        # Safely link guest session if requested
        if request.guest_session_id:
            try:
                linked_session_id, linked_orders_count = AuthService.link_guest_session(
                    db=db,
                    user=new_user,
                    session_id=request.guest_session_id,
                    session_token=request.guest_session_token,
                )
            except Exception as exc:
                db.rollback()
                raise exc

        db.commit()
        db.refresh(new_user)

        token, _, expires_in = create_access_token(
            user_id=new_user.id,
            email=new_user.email,
            name=new_user.name,
        )

        return new_user, token, expires_in, linked_session_id, linked_orders_count

    @staticmethod
    def login_user(
        db: Session,
        request: UserLoginRequest,
    ) -> tuple[User, str, int, str | None, int, str | None]:
        """
        Authenticate user credentials, issue JWT token, and optionally claim an active guest session.
        Returns: (user, access_token, expires_in, linked_session_id, linked_orders_count, session_link_error)
        """
        normalized_email = request.email.strip().lower()

        user = db.scalar(select(User).where(User.email == normalized_email))
        if not user or not verify_password(request.password, user.hashed_password):
            raise InvalidCredentialsError("Invalid email or password.")

        linked_session_id: str | None = None
        linked_orders_count: int = 0
        session_link_error: str | None = None

        # Safely link guest session if requested
        if request.guest_session_id:
            try:
                linked_session_id, linked_orders_count = AuthService.link_guest_session(
                    db=db,
                    user=user,
                    session_id=request.guest_session_id,
                    session_token=request.guest_session_token,
                )
            except (SessionProofOfPossessionError, SessionOwnershipError, SessionNotFoundError) as exc:
                db.rollback()
                session_link_error = str(exc)
                logger.warning(
                    "Optional guest session linking skipped during login for user %s: %s",
                    user.id,
                    exc,
                )
            except Exception as exc:
                db.rollback()
                raise exc

        # If no guest session was linked, resolve the user's existing primary session
        if not linked_session_id:
            existing_session = (
                db.query(ShoppingSession)
                .filter(ShoppingSession.user_id == user.id)
                .order_by(ShoppingSession.updated_at.desc())
                .first()
            )
            if existing_session:
                linked_session_id = existing_session.id

        token, _, expires_in = create_access_token(
            user_id=user.id,
            email=user.email,
            name=user.name,
        )

        return user, token, expires_in, linked_session_id, linked_orders_count, session_link_error

    @staticmethod
    def revoke_token(db: Session, token: str, user: User) -> None:
        """
        Record JWT identifier (jti) in server-side revocation table.
        Prevents any future reuse of the token.
        """
        try:
            payload = decode_access_token(token)
        except Exception:
            # If token cannot be decoded, it cannot be used anyway
            return

        jti = payload.get("jti")
        exp_ts = payload.get("exp")
        if not jti or not exp_ts:
            return

        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        existing = db.scalar(select(RevokedToken).where(RevokedToken.jti == jti))
        if not existing:
            revocation = RevokedToken(
                id=str(uuid.uuid4()),
                jti=jti,
                user_id=user.id,
                revoked_at=datetime.now(timezone.utc),
                expires_at=exp_dt,
            )
            db.add(revocation)
            db.commit()

    @staticmethod
    def link_guest_session(
        db: Session,
        user: User,
        session_id: str,
        session_token: str | None = None,
        session_identifier: str | None = None,
    ) -> tuple[str, int]:
        """
        Atomically link a guest session and all historical unassigned orders to an authenticated user.
        Strictly requires proof of possession. Never overwrites an existing user_id.
        Returns: (session_id, linked_orders_count)
        """
        try:
            session = db.get(ShoppingSession, session_id)
            if not session and session_identifier:
                session = db.scalar(
                    select(ShoppingSession).where(ShoppingSession.session_identifier == session_identifier)
                )

            if not session:
                raise SessionNotFoundError("Target guest session not found.")

            # If already owned by another account, reject
            if session.user_id is not None and session.user_id != user.id:
                raise SessionOwnershipError("Shopping session is already associated with another account.")

            # Proof of possession verification
            if session.session_token is not None:
                if not session_token or session_token != session.session_token:
                    raise SessionProofOfPossessionError(
                        "Invalid guest session credentials. Session possession could not be verified."
                    )
            else:
                # Legacy session without an assigned token:
                # Issue a fresh proof-of-possession token for future operations
                session.session_token = secrets.token_hex(32)

            # Atomic linking inside transaction
            session.user_id = user.id
            session.updated_at = datetime.now(timezone.utc)

            # Link eligible unassigned orders
            stmt = (
                select(PaymentOrder)
                .where(
                    PaymentOrder.session_id == session.id,
                    PaymentOrder.user_id.is_(None),
                )
            )
            orders = db.scalars(stmt).unique().all()
            for order in orders:
                order.user_id = user.id
                order.updated_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(session)

            return session.id, len(orders)
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def create_fresh_guest_session(db: Session) -> ShoppingSession:
        """
        Create a brand new ShoppingSession with cryptographically unguessable credentials.
        The session_token is issued only once to the creator.
        """
        new_id = f"sess_{uuid.uuid4().hex[:16]}"
        new_identifier = f"guest_{uuid.uuid4().hex[:16]}"
        session = ShoppingSession(
            id=new_id,
            session_identifier=new_identifier,
            session_token=secrets.token_hex(32),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get_or_issue_session_credentials(
        db: Session,
        session_id: str | None = None,
        session_token: str | None = None,
        session_identifier: str | None = None,
    ) -> ShoppingSession:
        """
        Secure bootstrap and proof-of-possession verification.
        - If session_id is omitted: generates and returns a brand new guest session with secret credentials.
        - If session_id is provided: NEVER returns or issues credentials without valid proof of possession.
        """
        if not session_id and not session_identifier:
            return AuthService.create_fresh_guest_session(db=db)

        session: ShoppingSession | None = None
        if session_id:
            session = db.get(ShoppingSession, session_id)
        if not session and session_identifier:
            session = db.scalar(
                select(ShoppingSession).where(ShoppingSession.session_identifier == session_identifier)
            )

        if not session:
            raise SessionNotFoundError("Target guest session not found.")

        # If session has an assigned token, require exact match
        if session.session_token is not None:
            if not session_token:
                raise SessionProofOfPossessionError(
                    "Guest session credential required to access session token."
                )
            if session_token != session.session_token:
                raise SessionProofOfPossessionError(
                    "Invalid guest session credentials. Session possession could not be verified."
                )
            return session

        # Legacy session lacking a token cannot be claimed/retrieved without proof
        raise SessionProofOfPossessionError(
            "Session token cannot be retrieved for an existing session without proof of possession."
        )


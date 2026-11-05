"""
AI-Powered Multilingual Shopping Intelligence Platform
User & Authentication Domain Models.
Supports User Profiles, Credentials, Relationships, and Server-Side Token Revocation.
"""

from datetime import datetime, timezone
import uuid
from typing import TYPE_CHECKING
from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship

from backend.app.core.database import Base

if TYPE_CHECKING:
    from backend.app.models.shopping import ShoppingSession
    from backend.app.models.payment import PaymentOrder


def generate_uuid() -> str:
    """Generate a UUID v4 string identifier."""
    return str(uuid.uuid4())


class User(Base):
    """
    Authoritative registered user profile.
    Maintains hashed credentials, registered metadata, and relationships
    to owned shopping sessions, orders, and revoked authentication tokens.
    """

    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(128), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    sessions = relationship(
        "ShoppingSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    orders = relationship(
        "PaymentOrder",
        back_populates="user",
    )
    revoked_tokens = relationship(
        "RevokedToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RevokedToken(Base):
    """
    Server-side token revocation record.
    Tracks invalidated JWT identifiers (jti) to ensure server-side logout enforcement.
    """

    __tablename__ = "revoked_tokens"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    jti = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revoked_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Relationship
    user = relationship("User", back_populates="revoked_tokens")

    __table_args__ = (
        Index("ix_revoked_tokens_jti_expires", "jti", "expires_at"),
    )


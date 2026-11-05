"""
AI-Powered Multilingual Shopping Intelligence Platform
Authentication and Session Credential Pydantic Schemas.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from backend.app.core.security import (
    MIN_PASSWORD_LENGTH_BYTES,
    MAX_PASSWORD_LENGTH_BYTES,
    validate_password_length,
)


class UserRegisterRequest(BaseModel):
    """Payload for new user registration."""

    name: str = Field(..., min_length=1, max_length=128, description="User display name")
    email: EmailStr = Field(..., description="Unique email address")
    password: str = Field(..., description="Plaintext password (8-72 bytes)")
    guest_session_id: str | None = Field(default=None, description="Optional active guest session UUID to claim")
    guest_session_token: str | None = Field(default=None, description="Proof-of-possession token for guest session")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Name cannot be empty or only whitespace.")
        return stripped

    @field_validator("password")
    @classmethod
    def validate_pw(cls, v: str) -> str:
        validate_password_length(v)
        return v


class UserLoginRequest(BaseModel):
    """Payload for user sign in."""

    email: EmailStr = Field(..., description="Registered email address")
    password: str = Field(..., description="User password")
    guest_session_id: str | None = Field(default=None, description="Optional active guest session UUID to claim upon login")
    guest_session_token: str | None = Field(default=None, description="Proof-of-possession token for guest session")


class UserResponse(BaseModel):
    """Public user profile representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    created_at: datetime


class TokenResponse(BaseModel):
    """Successful authentication response containing JWT and user profile."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    linked_session_id: str | None = None
    linked_orders_count: int = 0
    session_link_error: str | None = None


class LinkSessionRequest(BaseModel):
    """Payload to claim a guest shopping session under an authenticated account."""

    guest_session_id: str = Field(..., description="Guest ShoppingSession UUID")
    guest_session_token: str | None = Field(default=None, description="Proof-of-possession token for the session")
    session_identifier: str | None = Field(default=None, description="Legacy guest identifier fallback")


class LinkSessionResponse(BaseModel):
    """Result of guest session linking."""

    success: bool
    session_id: str
    orders_linked_count: int
    message: str


class SessionCredentialResponse(BaseModel):
    """Proof-of-possession credentials for a guest shopping session."""

    session_id: str
    session_identifier: str
    session_token: str


class SessionTokenRequest(BaseModel):
    """Payload to bootstrap a fresh guest session or verify credentials for an existing session."""

    guest_session_id: str | None = Field(default=None, description="Optional existing ShoppingSession UUID")
    guest_session_token: str | None = Field(default=None, description="Proof-of-possession token required if session_id is provided")
    session_identifier: str | None = Field(default=None, description="Optional guest session identifier")


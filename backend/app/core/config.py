"""
AI-Powered Multilingual Shopping Intelligence Platform
Centralized Configuration Management via Pydantic Settings.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base repository root: backend/app/core/config.py -> 4 levels up
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_SQLITE_PATH: Path = REPO_ROOT / "shopping_platform.db"


class Settings(BaseSettings):
    """
    Authoritative application settings.
    Loads from environment variables and root .env file.
    All secrets are wrapped in SecretStr to prevent leakage in logs and API serialization.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Application
    APP_NAME: str = Field(
        default="AI-Powered Multilingual Shopping Intelligence Platform",
        description="Application title",
    )
    APP_VERSION: str = Field(
        default="0.1.0",
        description="Application release version",
    )
    ENVIRONMENT: Literal["development", "testing", "staging", "production"] = Field(
        default="development",
        description="Runtime deployment environment",
    )
    DEBUG: bool = Field(
        default=False,
        description="Enable debug mode (strictly disabled in production)",
    )
    HOST: str = Field(
        default="127.0.0.1",
        description="Server host interface",
    )
    PORT: int = Field(
        default=8000,
        description="Server listen port",
    )
    FRONTEND_URL: str = Field(
        default="http://localhost:5500",
        description="Canonical frontend origin URL (used for CORS and PayPal return/cancel defaults)",
    )
    CORS_ORIGINS: str = Field(
        default="",
        description="Comma-separated list of allowed CORS origins for production (e.g. 'https://buywise.vercel.app,https://mycustomdomain.com')",
    )

    # Database Settings
    DATABASE_URL: str = Field(
        default=f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}",
        description="SQLAlchemy database connection URL",
    )

    # Gemini AI Settings
    GEMINI_API_KEY: SecretStr | None = Field(
        default=None,
        description="Google Gemini API key (never hardcoded, kept confidential)",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-3.8-flash",
        description="Configurable Gemini model identifier (Gemini 3.8 Flash confirmed)",
    )
    GEMINI_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        description="Gemini API call timeout in seconds",
    )
    GEMINI_THINKING_LEVEL: str = Field(
        default="medium",
        description="Thinking level for Gemini 3.8 Flash ('low', 'medium', 'high')",
    )

    # Channel3 Product Aggregation Settings
    CHANNEL3_API_KEY: SecretStr | None = Field(
        default=None,
        description="Channel3 API key for live product aggregation (never hardcoded, kept confidential)",
    )
    CHANNEL3_BASE_URL: str = Field(
        default="https://api.trychannel3.com",
        description="Channel3 REST API base URL",
    )
    CHANNEL3_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description="Channel3 HTTP request timeout in seconds",
    )

    # PayPal Sandbox Settings
    PAYPAL_MODE: Literal["sandbox", "live"] = Field(
        default="sandbox",
        description="PayPal environment mode",
    )
    PAYPAL_ENV: Literal["sandbox", "live"] = Field(
        default="sandbox",
        description="PayPal environment mode (alias for PAYPAL_MODE)",
    )
    PAYPAL_CLIENT_ID: str = Field(
        default="",
        description="PayPal Sandbox client ID",
    )
    PAYPAL_CLIENT_SECRET: SecretStr | None = Field(
        default=None,
        description="PayPal Sandbox client secret",
    )
    PAYPAL_BASE_URL: str = Field(
        default="https://api-m.sandbox.paypal.com",
        description="PayPal REST API base URL (strictly Sandbox by default)",
    )
    PAYPAL_TIMEOUT_SECONDS: float = Field(
        default=15.0,
        description="PayPal HTTP request timeout in seconds",
    )
    PAYPAL_WEBHOOK_ID: str = Field(
        default="",
        description="PayPal Webhook ID for signature verification",
    )
    PAYPAL_IDEMPOTENCY_RETENTION_HOURS: int = Field(
        default=6,
        description="PayPal Request-ID retention window in hours (default 6)",
    )

    # Cryptographic & Session Security
    SECRET_KEY: SecretStr = Field(
        default=SecretStr("insecure-dev-secret-key-change-in-production"),
        description="Secret key for cryptographically signed tokens and sessions",
    )

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str | None) -> str:
        """
        Normalize database connection URLs:
        - SQLite relative URLs: converted to stable absolute path anchored at repo root.
        - PostgreSQL URLs: normalizes 'postgres://' to 'postgresql+psycopg2://' and
          'postgresql://' without driver to 'postgresql+psycopg2://' for SQLAlchemy 2.0 compatibility.
        """
        if not v:
            return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
        v_str = str(v).strip()
        if v_str.startswith("sqlite:///./"):
            rel_part = v_str.replace("sqlite:///./", "")
            abs_path = (REPO_ROOT / rel_part).resolve()
            return f"sqlite:///{abs_path.as_posix()}"
        if v_str.startswith("postgres://"):
            return "postgresql+psycopg2://" + v_str[len("postgres://"):]
        if v_str.startswith("postgresql://") and not v_str.startswith("postgresql+"):
            return "postgresql+psycopg2://" + v_str[len("postgresql://"):]
        return v_str

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "testing"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application settings."""
    return Settings()

"""
AI-Powered Multilingual Shopping Intelligence Platform
Configuration Management Unit Tests.
"""

from pydantic import SecretStr
from backend.app.core.config import Settings, get_settings


def test_settings_initialization():
    """Verify default settings load cleanly with expected metadata."""
    settings = get_settings()
    assert "AI-Powered Multilingual Shopping Intelligence Platform" in settings.APP_NAME
    assert settings.APP_VERSION == "0.1.0"
    assert settings.DATABASE_URL.startswith("sqlite:///")
    assert settings.is_development is True


def test_secret_str_masking():
    """
    Verify that sensitive configuration values (API keys, secrets) are wrapped in SecretStr
    and are never exposed via standard repr() or str().
    """
    settings = Settings(
        GEMINI_API_KEY=SecretStr("super-secret-gemini-key"),
        PAYPAL_CLIENT_SECRET=SecretStr("super-secret-paypal-secret"),
        CHANNEL3_API_KEY=SecretStr("super-secret-channel3-key"),
    )

    # String and repr representations must mask secrets
    assert "super-secret-gemini-key" not in str(settings.GEMINI_API_KEY)
    assert "super-secret-gemini-key" not in repr(settings.GEMINI_API_KEY)
    assert "super-secret-paypal-secret" not in str(settings.PAYPAL_CLIENT_SECRET)
    assert "super-secret-paypal-secret" not in repr(settings.PAYPAL_CLIENT_SECRET)
    assert "super-secret-channel3-key" not in str(settings.CHANNEL3_API_KEY)
    assert "super-secret-channel3-key" not in repr(settings.CHANNEL3_API_KEY)

    # Actual value accessible only via get_secret_value()
    assert settings.GEMINI_API_KEY.get_secret_value() == "super-secret-gemini-key"
    assert settings.CHANNEL3_API_KEY.get_secret_value() == "super-secret-channel3-key"


def test_sqlite_url_normalization():
    """Verify that relative SQLite paths are converted to stable absolute paths."""
    settings = Settings(DATABASE_URL="sqlite:///./test_custom.db")
    assert settings.DATABASE_URL.startswith("sqlite:///")
    assert "test_custom.db" in settings.DATABASE_URL
    assert not settings.DATABASE_URL.startswith("sqlite:///./")


def test_database_url_normalization_with_percent_encoded_credentials():
    """
    Verify that PostgreSQL URLs containing percent-encoded characters in credentials
    (e.g., %40 for @, %25 for %, %23 for #) are normalized with the psycopg2 driver
    without corrupting, changing, or double-encoding the credentials.
    """
    from sqlalchemy.engine.url import make_url

    raw_pg = "postgresql://admin:p%40ss%25w%23rd@db.example.com:5432/app_prod"
    settings = Settings(DATABASE_URL=raw_pg)
    assert settings.DATABASE_URL == "postgresql+psycopg2://admin:p%40ss%25w%23rd@db.example.com:5432/app_prod"

    parsed = make_url(settings.DATABASE_URL)
    assert parsed.username == "admin"
    assert parsed.password == "p@ss%w#rd"
    assert parsed.host == "db.example.com"
    assert parsed.port == 5432
    assert parsed.database == "app_prod"


def test_alembic_config_percent_escaping():
    """
    Verify that database URLs containing percent signs are escaped with '%%'
    when passed to Alembic Config.set_main_option to prevent configparser interpolation errors,
    and verify that Alembic Config retrieves the exact unescaped URL without double encoding.
    """
    from alembic.config import Config
    from backend.app.core.config import REPO_ROOT

    url_with_percent = "postgresql+psycopg2://appuser:p%40ss%25word@dbhost:5432/testdb"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))

    # Unescaped URL must trigger interpolation error in configparser
    try:
        cfg.set_main_option("sqlalchemy.url", url_with_percent)
        cfg.get_main_option("sqlalchemy.url")
        assert False, "Unescaped URL should have raised ValueError during config interpolation"
    except ValueError:
        pass

    # Escaped URL ('%' replaced with '%%') passes safely and decodes back to the exact URL
    cfg.set_main_option("sqlalchemy.url", url_with_percent.replace("%", "%%"))
    retrieved = cfg.get_main_option("sqlalchemy.url")
    assert retrieved == url_with_percent

    section = cfg.get_section(cfg.config_ini_section, {})
    assert section.get("sqlalchemy.url") == url_with_percent

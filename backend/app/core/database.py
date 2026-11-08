"""
AI-Powered Multilingual Shopping Intelligence Platform
SQLAlchemy 2.0 Database Configuration & Session Management.
Supports SQLite (development/testing with WAL mode & foreign keys) and PostgreSQL (production).
"""

import logging
from typing import Any, Generator
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from backend.app.core.config import get_settings

logger = logging.getLogger(__name__)

# SQLAlchemy 2.0 declarative base for all future domain models
Base = declarative_base()


def build_engine(database_url: str | None = None) -> Engine:
    """
    Construct and configure SQLAlchemy 2.0 Engine.
    Applies SQLite-specific PRAGMAs (WAL mode, foreign keys, busy timeout) when dialect is sqlite.
    Maintains full compatibility with PostgreSQL connection pools.
    """
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    is_sqlite = url.startswith("sqlite")

    engine_kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "echo": settings.DEBUG and is_sqlite,
    }

    if is_sqlite:
        # SQLite connection arguments for multi-threaded FastAPI workers
        engine_kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": 15.0,  # 15s connection lock timeout
        }
    else:
        # PostgreSQL production pool configuration
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    eng = create_engine(url, **engine_kwargs)

    if is_sqlite:
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
            """
            Enforce SQLite durability and relational integrity:
            1. WAL (Write-Ahead Logging) prevents readers and writers from blocking each other.
            2. foreign_keys = ON enforces relational integrity constraints.
            3. busy_timeout = 5000ms waits for locked database before raising busy error.
            4. synchronous = NORMAL optimizes WAL disk sync.
            """
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode = WAL;")
                cursor.execute("PRAGMA foreign_keys = ON;")
                cursor.execute("PRAGMA busy_timeout = 5000;")
                cursor.execute("PRAGMA synchronous = NORMAL;")
            finally:
                cursor.close()

    return eng


# Application-wide Engine instance
engine: Engine = build_engine()

# SQLAlchemy 2.0 Session Factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """
    Reusable FastAPI database session dependency.
    Yields an active Session and strictly guarantees closure upon request completion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_database(db: Session) -> bool:
    """
    Execute an authoritative database connectivity check.
    Returns True if database is reachable and responsive; raises exception otherwise.
    """
    db.execute(text("SELECT 1"))
    return True


def ensure_schema_compatibility(target_engine: Engine) -> None:
    """
    Ensure existing tables have all required columns without destructive drops.
    Handles non-destructive forward migrations for SQLite development environments.
    Bypassed safely on PostgreSQL/non-SQLite where schema is managed via Alembic.
    """
    if target_engine.dialect.name != "sqlite":
        logger.info(
            "Schema compatibility PRAGMA check skipped for dialect '%s'; "
            "schema evolution is governed authoritatively by Alembic in non-SQLite environments.",
            target_engine.dialect.name,
        )
        return

    with target_engine.connect() as conn:
        try:
            # Check products table
            prod_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(products)")).fetchall()]
            if prod_cols:
                if "provider" not in prod_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN provider VARCHAR(64) DEFAULT 'synthetic'"))
                if "provider_product_id" not in prod_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN provider_product_id VARCHAR(128)"))
                if "is_synthetic" not in prod_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN is_synthetic BOOLEAN DEFAULT 1"))
                if "updated_at" not in prod_cols:
                    conn.execute(text("ALTER TABLE products ADD COLUMN updated_at DATETIME"))
                conn.commit()

            # Check retailer_offers table
            offer_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(retailer_offers)")).fetchall()]
            if offer_cols:
                if "compare_at_price" not in offer_cols:
                    conn.execute(text("ALTER TABLE retailer_offers ADD COLUMN compare_at_price FLOAT"))
                if "provider_offer_id" not in offer_cols:
                    conn.execute(text("ALTER TABLE retailer_offers ADD COLUMN provider_offer_id VARCHAR(128)"))
                if "is_synthetic" not in offer_cols:
                    conn.execute(text("ALTER TABLE retailer_offers ADD COLUMN is_synthetic BOOLEAN DEFAULT 1"))
                if "retrieved_at" not in offer_cols:
                    conn.execute(text("ALTER TABLE retailer_offers ADD COLUMN retrieved_at DATETIME"))
                conn.commit()

            # Check payment_orders table
            order_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(payment_orders)")).fetchall()]
            if order_cols:
                if "paypal_capture_request_id" not in order_cols:
                    conn.execute(text("ALTER TABLE payment_orders ADD COLUMN paypal_capture_request_id VARCHAR(64)"))
                if "budget_override_applied" not in order_cols:
                    conn.execute(text("ALTER TABLE payment_orders ADD COLUMN budget_override_applied BOOLEAN DEFAULT 0"))
                if "original_budget_amount" not in order_cols:
                    conn.execute(text("ALTER TABLE payment_orders ADD COLUMN original_budget_amount NUMERIC(12, 2)"))
                if "budget_exceeded_amount" not in order_cols:
                    conn.execute(text("ALTER TABLE payment_orders ADD COLUMN budget_exceeded_amount NUMERIC(12, 2)"))
                if "user_id" not in order_cols:
                    conn.execute(text("ALTER TABLE payment_orders ADD COLUMN user_id VARCHAR(64)"))
                conn.commit()

            # Check shopping_sessions table
            session_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(shopping_sessions)")).fetchall()]
            if session_cols:
                if "user_id" not in session_cols:
                    conn.execute(text("ALTER TABLE shopping_sessions ADD COLUMN user_id VARCHAR(64)"))
                if "session_token" not in session_cols:
                    conn.execute(text("ALTER TABLE shopping_sessions ADD COLUMN session_token VARCHAR(64)"))
                conn.commit()
# Check price_snapshots table
            snapshot_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(price_snapshots)")).fetchall()]
            if not snapshot_cols:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS price_snapshots (
                        id VARCHAR(64) PRIMARY KEY,
                        product_id VARCHAR(64) NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                        retailer_name VARCHAR(128) NOT NULL,
                        provider_offer_id VARCHAR(128),
                        price FLOAT NOT NULL,
                        currency VARCHAR(3) NOT NULL DEFAULT 'USD',
                        availability VARCHAR(64) NOT NULL DEFAULT 'IN_STOCK',
                        recorded_at DATETIME NOT NULL,
                        is_synthetic BOOLEAN NOT NULL DEFAULT 0
                    );
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_product_id ON price_snapshots(product_id);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_retailer_name ON price_snapshots(retailer_name);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_provider_offer_id ON price_snapshots(provider_offer_id);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_recorded_at ON price_snapshots(recorded_at);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_product_currency ON price_snapshots(product_id, currency);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_product_retailer ON price_snapshots(product_id, retailer_name);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_snapshots_lookup ON price_snapshots(product_id, currency, recorded_at);"))
                conn.commit()
        except Exception as e:
            logger.warning("Schema compatibility check skipped or not needed: %s", e)

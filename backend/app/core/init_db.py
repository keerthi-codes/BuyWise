"""
AI-Powered Multilingual Shopping Intelligence Platform
Production Database Initializer.
Provides a deliberate, one-time schema initialization and seeding command
for production cloud deployments (Render Release Command / Railway Pre-deploy).
Prevents multi-worker concurrency race conditions on application startup.
"""

import logging
import sys
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.app.core.config import get_settings, REPO_ROOT
from backend.app.core.database import engine as default_engine, Base, ensure_schema_compatibility
from backend.app.core.seed import seed_database
import backend.app.models  # Register all domain entities on Base.metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shopping_platform.init_db")


def initialize_database(target_engine: Engine | None = None, stamp_alembic: bool = True) -> dict[str, int]:
    """
    Perform a deliberate, safe, one-time schema initialization and catalog seed.
    Guarantees:
    1. Single-leader execution (run as deployment command, not inside multi-worker lifespan).
    2. Atomically creates all 10 SQLAlchemy tables, indexes, and foreign keys.
    3. Idempotently seeds initial product catalog and retailer offers.
    4. Stamps Alembic version table to head ('20261104_02') if requested.
    """
    eng = target_engine or default_engine
    settings = get_settings()

    logger.info("Initializing database schema on dialect '%s'...", eng.dialect.name)

    # 1. Verify connectivity
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connectivity confirmed.")

    # 2. Create all tables defined in SQLAlchemy models
    Base.metadata.create_all(bind=eng)
    logger.info("All domain tables and indexes created / verified successfully.")

    # 3. Apply dialect-specific compatibility adjustments
    if eng.dialect.name == "sqlite":
        ensure_schema_compatibility(eng)

    # 4. Idempotently seed catalog
    seed_database(bind=eng)
    logger.info("Catalog seeding verified.")

    # 5. Authoritatively stamp Alembic version to head if requested
    if stamp_alembic:
        from alembic.config import Config
        from alembic import command

        ini_path = REPO_ROOT / "alembic.ini"
        if not ini_path.exists():
            raise FileNotFoundError(f"Alembic configuration file not found at {ini_path}")

        alembic_cfg = Config(str(ini_path))
        target_url = (
            eng.url.render_as_string(hide_password=False)
            if target_engine is not None
            else settings.DATABASE_URL
        )
        # Escape '%' as '%%' for Alembic ConfigParser interpolation safety
        alembic_cfg.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
        alembic_cfg.attributes["connection"] = eng

        try:
            command.stamp(alembic_cfg, "head")
            logger.info("Alembic schema version stamped to 'head' successfully.")
        except Exception as exc:
            logger.error("Alembic version stamping failed: %s", exc, exc_info=True)
            raise
    else:
        logger.info("Alembic version stamp skipped (stamp_alembic=False).")

    # 6. Count initialized tables
    from sqlalchemy import inspect
    inspector = inspect(eng)
    tables = inspector.get_table_names()
    logger.info("Database initialization completed. Active tables (%d): %s", len(tables), sorted(tables))

    return {"table_count": len(tables)}


if __name__ == "__main__":
    try:
        res = initialize_database()
        logger.info("Initialization finished successfully with %d tables.", res["table_count"])
        sys.exit(0)
    except Exception as exc:
        logger.error("Database initialization failed: %s", exc, exc_info=True)
        sys.exit(1)


"""
AI-Powered Multilingual Shopping Intelligence Platform
Production Database Initializer Unit & Integration Tests.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine, text

from backend.app.core.init_db import initialize_database


def test_initialize_database_without_stamping():
    """
    Verify that initialize_database creates all domain tables and seeds the catalog
    when stamp_alembic=False, without creating alembic_version.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_no_stamp.db")
        eng = create_engine(f"sqlite:///{db_path}")

        result = initialize_database(target_engine=eng, stamp_alembic=False)
        assert result["table_count"] >= 10

        with eng.connect() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars().all()
            assert "products" in tables
            assert "users" in tables
            assert "payment_orders" in tables
            assert "alembic_version" not in tables

            # Verify seed data exists
            product_count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
            assert product_count >= 18

        eng.dispose()


def test_initialize_database_with_stamping():
    """
    Verify that initialize_database creates all domain tables, seeds the catalog,
    and accurately stamps the Alembic version to '20261104_02' when stamp_alembic=True.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_stamped.db")
        eng = create_engine(f"sqlite:///{db_path}")

        result = initialize_database(target_engine=eng, stamp_alembic=True)
        assert result["table_count"] >= 11

        with eng.connect() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars().all()
            assert "alembic_version" in tables

            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "20261104_02"

        eng.dispose()


def test_initialize_database_with_percent_encoded_url():
    """
    Verify that initialize_database handles URLs containing percent-encoded characters
    without ConfigParser interpolation errors or credential corruption.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test%20space%25pct.db")
        eng = create_engine(f"sqlite:///{db_path}")

        result = initialize_database(target_engine=eng, stamp_alembic=True)
        assert result["table_count"] >= 11

        with eng.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "20261104_02"

        eng.dispose()


def test_initialize_database_stamping_failure_raises():
    """
    Verify that an Alembic stamping failure raises an exception and does not silently continue.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_fail.db")
        eng = create_engine(f"sqlite:///{db_path}")

        with patch("alembic.command.stamp", side_effect=RuntimeError("Simulated stamp failure")):
            with pytest.raises(RuntimeError, match="Simulated stamp failure"):
                initialize_database(target_engine=eng, stamp_alembic=True)

        eng.dispose()


def test_initialize_database_missing_alembic_ini_raises():
    """
    Verify that missing alembic.ini raises FileNotFoundError when stamp_alembic=True.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_missing_ini.db")
        eng = create_engine(f"sqlite:///{db_path}")

        nonexistent = Path(tmpdir) / "nonexistent"

        with patch("backend.app.core.init_db.REPO_ROOT", nonexistent):
            with pytest.raises(FileNotFoundError, match="Alembic configuration file not found"):
                initialize_database(target_engine=eng, stamp_alembic=True)

        eng.dispose()


"""
AI-Powered Multilingual Shopping Intelligence Platform
FastAPI Application Entrypoint.
Initializes centralized settings, database tables, catalog seeding, error handlers, and API routers.
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.health import router as health_router
from backend.app.api.products import router as products_router
from backend.app.api.mandates import router as mandates_router
from backend.app.api.assistant import router as assistant_router
from backend.app.api.checkout import router as checkout_router
from backend.app.api.auth import router as auth_router
from backend.app.core.config import get_settings, Settings
from backend.app.core.database import engine, Base, ensure_schema_compatibility
from backend.app.core.errors import register_error_handlers
from backend.app.core.seed import seed_database
from backend.app.services.price_tracker import PriceTracker
import backend.app.models  # Register all domain and payment models on Base.metadata

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shopping_platform")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan management.
    Handles startup table creation, repeatable demo dataset seeding, and connection pool cleanup.
    """
    logger.info("Starting %s (v%s) [%s]", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)

    # Database initialization:
    # For SQLite (local development/testing), preserve automatic table creation and schema evolution.
    # For PostgreSQL / production, schema is initialized as a deliberate deployment operation
    # (e.g. via python -m backend.app.core.init_db) to prevent multi-worker concurrency races.
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
        ensure_schema_compatibility(engine)
        seed_database(bind=engine)
    else:
        logger.info(
            "Production dialect '%s' detected; bypassing automatic in-process create_all in lifespan "
            "to prevent multi-worker race conditions. Schema is managed via deliberate deployment operations.",
            engine.dialect.name,
        )

    # Start periodic price tracking background worker
    PriceTracker.start()

    yield

    logger.info("Shutting down application and stopping background price tracker...")
    await PriceTracker.stop()
    logger.info("Disposing database connection pool...")
    engine.dispose()
    logger.info("Database connection pool disposed.")


def resolve_cors_origins(settings: Settings) -> list[str]:
    """
    Resolve allowed CORS origins based on environment and settings:
    - Development / Testing: permits all origins ["*"] to preserve local development and testing.
    - Production / Staging: strictly uses explicit origins from FRONTEND_URL and CORS_ORIGINS.
      Never allows wildcard '*' when credentials are permitted in production.
    """
    if settings.is_development or settings.is_testing:
        return ["*"]

    origins: list[str] = []
    if settings.CORS_ORIGINS:
        for item in settings.CORS_ORIGINS.split(","):
            cleaned = item.strip().rstrip("/")
            if cleaned and cleaned != "*" and cleaned not in origins:
                origins.append(cleaned)

    if settings.FRONTEND_URL:
        cleaned = settings.FRONTEND_URL.strip().rstrip("/")
        if cleaned and cleaned != "*" and cleaned not in origins:
            origins.append(cleaned)

    if not origins:
        logger.warning(
            "CORS: No production origins configured via FRONTEND_URL or CORS_ORIGINS; "
            "cross-origin browser requests will be blocked."
        )

    return origins


def create_application() -> FastAPI:
    """
    FastAPI Application Factory.
    Initializes settings, RFC 9457 error handlers, middlewares, and registered routers.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS Middleware (allows browser frontend communication)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolve_cors_origins(settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register centralized RFC 9457 error handlers
    register_error_handlers(app)

    # Clean router registration
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(products_router)
    app.include_router(mandates_router)
    app.include_router(assistant_router)
    app.include_router(checkout_router)

    # Mount frontend static assets
    base_dir = Path(__file__).resolve().parent.parent.parent
    frontend_dir = base_dir / "frontend"
    assets_dir = frontend_dir / "assets"

    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/index.html", include_in_schema=False)
    def serve_index_html():
        index_file = frontend_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file, media_type="text/html; charset=utf-8")
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    return app


app: FastAPI = create_application()

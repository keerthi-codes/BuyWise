"""
AI-Powered Multilingual Shopping Intelligence Platform
System & Health Check Endpoints.
"""

import logging
from pathlib import Path
from typing import Literal, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["System"])
settings = get_settings()


class HealthResponse(BaseModel):
    """Authoritative response schema for GET /health."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        default="healthy", description="Overall system health status"
    )
    app: str = Field(description="Application name")
    version: str = Field(description="Application release version")
    environment: str = Field(description="Runtime environment")
    database: Literal["connected", "disconnected"] = Field(
        default="connected", description="Database connectivity state"
    )


class RootResponse(BaseModel):
    """Authoritative response schema for GET /."""

    app: str = Field(description="Application name")
    version: str = Field(description="Application release version")
    status: Literal["running"] = Field(
        default="running", description="Process execution status"
    )
    environment: str = Field(description="Deployment environment")
    docs_url: str = Field(default="/docs", description="Interactive OpenAPI documentation path")
    health_url: str = Field(default="/health", description="System health endpoint path")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="System & Database Health Check",
    description="Executes a live query against the configured database. Returns 200 when reachable, 503 when disconnected.",
    responses={
        200: {"description": "Application and database are healthy and responsive."},
        503: {"description": "Database connectivity check failed."},
    },
)
@router.get(
    "/api/health",
    response_model=HealthResponse,
    include_in_schema=False,
)
def health_check(
    db: Session = Depends(get_db),
    check_timeout: int = Query(
        default=5,
        ge=1,
        le=60,
        description="Maximum seconds allowed for database probe verification",
    ),
) -> HealthResponse:
    """
    Authoritative health verification.
    1. Ensures FastAPI application process is active.
    2. Executes a real database round-trip (SELECT 1).
    3. Rejects reporting healthy if the database connection fails.
    """
    try:
        # Execute real database connectivity probe
        db.execute(text("SELECT 1"))
        return HealthResponse(
            status="healthy",
            app=settings.APP_NAME,
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
            database="connected",
        )
    except Exception as exc:
        logger.error("Health check failed - database probe error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connectivity check failed",
        )


FRONTEND_INDEX = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "index.html"


@router.get(
    "/",
    response_model=RootResponse,
    summary="Root Information",
    description="Basic platform identification and API documentation links, or serves the frontend SPA for browsers.",
)
def root_endpoint(request: Request) -> Any:
    """Return top-level application metadata or serve frontend SPA to web browsers."""
    accept = request.headers.get("accept", "")
    # Check if request specifically requests HTML from a web browser
    if "text/html" in accept and not accept.strip() == "*/*" and FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX, media_type="text/html; charset=utf-8")

    return RootResponse(
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        status="running",
        environment=settings.ENVIRONMENT,
        docs_url="/docs",
        health_url="/health",
    )

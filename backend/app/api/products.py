"""
AI-Powered Multilingual Shopping Intelligence Platform
Product Catalog & Comparison API Endpoints.
Routes:
  GET /api/products          - Search and filter products
  GET /api/products/compare  - Multi-product comparison matrix
  GET /api/products/{id}     - Detailed product specifications & retailer offers
"""

import logging
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.schemas.product import (
    ProductDetailResponse,
    ProductListResponse,
    ProductComparisonResponse,
    ProductIntelligenceResponse,
)
from backend.app.services.product_service import ProductService
from backend.app.services.product_intelligence_service import ProductIntelligenceService
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.price_tracker import PriceTracker
from backend.app.services.channel3_client import (
    Channel3Error,
    Channel3AuthError,
    Channel3RateLimitError,
    Channel3TimeoutError,
    Channel3ServerError,
    Channel3APIError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/products", tags=["Products"])


@router.get(
    "/search",
    response_model=ProductListResponse,
    summary="Live Product Search (Channel3 Aggregator)",
    description="Query live products across tens of thousands of retailers via Channel3. Normalizes and persists results.",
)
def search_live_products(
    q: Annotated[str, Query(min_length=1, description="Natural language search query for live retailer aggregation")],
    limit: Annotated[int, Query(ge=1, le=30, description="Max products to retrieve (1-30)")] = 10,
    db: Session = Depends(get_db),
) -> ProductListResponse:
    """Execute live product discovery and offer aggregation via Channel3."""
    try:
        return ProductService.search_live_products(db=db, query=q, limit=limit)
    except Channel3AuthError as exc:
        logger.error("Channel3 authentication failed during search: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Channel3 provider authentication failed. Check configuration.",
        )
    except Channel3RateLimitError as exc:
        logger.warning("Channel3 rate limit exceeded: %s", exc)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Channel3 rate limit or quota exceeded. Please retry later.",
            headers=headers,
        )
    except Channel3TimeoutError as exc:
        logger.warning("Channel3 search timed out: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Channel3 live search request timed out.",
        )
    except (Channel3ServerError, Channel3APIError, Channel3Error) as exc:
        logger.error("Channel3 provider error during search: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Channel3 provider error: {str(exc)}",
        )


@router.get(
    "",
    response_model=ProductListResponse,
    summary="Search & Filter Products",
    description="Search stored products by text, filter by category, brand, source ('synthetic' | 'live'), and price range.",
)
def list_products(
    q: Annotated[str | None, Query(description="Search keyword (matches name, description, brand)")] = None,
    category: Annotated[str | None, Query(description="Filter by taxonomy category (e.g. Electronics)")] = None,
    brand: Annotated[str | None, Query(description="Filter by manufacturer brand (e.g. Apple)")] = None,
    min_price: Annotated[float | None, Query(ge=0, description="Minimum price across retailer offers")] = None,
    max_price: Annotated[float | None, Query(ge=0, description="Maximum price across retailer offers")] = None,
    source: Annotated[str | None, Query(description="Filter by catalog source ('synthetic', 'live', or None for all)")] = None,
    order_by: Annotated[str | None, Query(description="Ordering mode: 'random', 'name', 'price_asc', 'price_desc'")] = None,
    seed: Annotated[int | None, Query(description="Optional random seed for reproducible varied order")] = None,
    expand_discovery: Annotated[bool, Query(description="Whether to attempt controlled live discovery to expand catalog")] = False,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page (max 100)")] = 20,
    db: Session = Depends(get_db),
) -> ProductListResponse:
    """Execute product catalog search, dynamic discovery, and filtering."""
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_price cannot exceed max_price.",
        )

    return ProductService.search_products(
        db=db,
        query=q,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        source=source,
        order_by=order_by,
        seed=seed,
        expand_discovery=expand_discovery,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/discover",
    summary="Controlled Live Catalogue Discovery",
    description="Discovers and ingests fresh genuine products from Channel3 with rate-limit and cooldown protection.",
)
def trigger_catalog_discovery(
    category: Annotated[str | None, Query(description="Target taxonomy category")] = None,
    force: Annotated[bool, Query(description="Force discovery sweep ignoring cooldown (test only)")] = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    count, message = ProductService.discover_live_products(db=db, category=category, force=force)
    return {
        "status": "success" if count > 0 or "cooldown" in message.lower() else "notice",
        "ingested_count": count,
        "message": message,
    }


@router.get(
    "/compare",
    response_model=ProductComparisonResponse,
    summary="Compare Multiple Products",
    description="Compare technical specifications, retailer prices, best offers, and availability across products.",
)
def compare_products(
    ids: Annotated[list[str], Query(description="Product IDs to compare (supports multiple ?ids=... or comma-separated)")],
    include_ai: Annotated[bool, Query(description="Whether to generate grounded AI comparison explanation")] = True,
    db: Session = Depends(get_db),
) -> ProductComparisonResponse:
    """
    Build a multi-product comparison matrix.
    Accepts list of product IDs via repeated params (?ids=a&ids=b) or comma-delimited (?ids=a,b).
    Optionally enriches comparison with grounded AI trade-offs and recommendations.
    """
    # Normalize IDs (handle both list and comma-separated formats)
    clean_ids: list[str] = []
    for item in ids:
        clean_ids.extend([x.strip() for x in item.split(",") if x.strip()])

    if not clean_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one valid product ID must be provided for comparison.",
        )

    comparison = ProductService.compare_products(db=db, product_ids=clean_ids, include_ai=include_ai)
    if not comparison.products:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="None of the requested product IDs were found in the catalog.",
        )

    return comparison


@router.get(
    "/{product_id}/intelligence",
    response_model=ProductIntelligenceResponse,
    summary="Individual Product Intelligence & Historical Pricing",
    description="Retrieve comprehensive product price intelligence including current best prices, historical extremes, price changes, cross-retailer comparisons, stability assessments, and chronological price history.",
)
def get_product_intelligence(
    product_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ProductIntelligenceResponse:
    """Retrieve full product price intelligence and history by product ID."""
    product = ProductRepository.get_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID '{product_id}' was not found.",
        )

    # Safe on-demand refresh if cooldown has expired (does not block UI)
    if PriceTracker.is_eligible_for_refresh(product):
        background_tasks.add_task(PriceTracker.refresh_product_on_demand, product.id)

    intelligence = ProductIntelligenceService.get_product_intelligence(db=db, product_id=product_id)
    return intelligence


@router.get(
    "/{product_id}",
    response_model=ProductDetailResponse,
    summary="Product Details & Retailer Offers",
    description="Retrieve comprehensive product metadata, technical specs, all competing retailer offers, and lowest price.",
)
def get_product(
    product_id: str,
    db: Session = Depends(get_db),
) -> ProductDetailResponse:
    """Retrieve full product details by product ID."""
    product = ProductService.get_product_detail(db=db, product_id=product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID '{product_id}' was not found.",
        )
    return product

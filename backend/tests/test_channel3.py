"""
AI-Powered Multilingual Shopping Intelligence Platform
Channel3 Live Product Aggregation Integration Tests.
All tests use strictly mocked HTTPX responses to guarantee ZERO credit consumption.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import httpx
import pytest
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.models.shopping import Product, ShoppingSession, SpendingMandate
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.channel3_client import (
    Channel3Client,
    Channel3Error,
    Channel3AuthError,
    Channel3RateLimitError,
    Channel3TimeoutError,
    Channel3ServerError,
    Channel3ResponseError,
)
from backend.app.services.channel3_normalizer import (
    Channel3Normalizer,
    NormalizedProduct,
    NormalizedOffer,
)
from backend.app.services.product_service import ProductService
from backend.app.services.policy_engine import SpendingPolicyEngine


# -------------------------------------------------------------------------
# Sample Channel3 JSON Fixtures
# -------------------------------------------------------------------------

MOCK_RAW_PRODUCT_1 = {
    "id": "c3_prod_001",
    "title": "Sennheiser Accentum Wireless Headphones",
    "description": "Hybrid Active Noise Cancelling over-ear headphones with 50-hour battery.",
    "brands": [{"id": "b1", "name": "Sennheiser"}],
    "category": {"slug": "over-ear-headphones", "title": "Over-Ear Headphones"},
    "images": [
        {
            "url": "https://cdn.trychannel3.com/raw/img1.jpg",
            "cleaned_url": "https://cdn.trychannel3.com/cleaned/img1.jpg",
            "is_main_image": True,
        }
    ],
    "key_features": ["50-hour battery life", "Hybrid ANC"],
    "structured_attributes": {
        "connectivity-technology": ["Bluetooth", "Wireless"],
        "battery-type": ["Rechargeable"],
    },
    "offers": [
        {
            "id": "off_01",
            "domain": "newegg.com",
            "url": "https://buy.trychannel3.com/deal1",
            "price": {"price": 175.0, "currency": "USD", "compare_at_price": 199.99},
            "availability": "InStock",
            "condition": "new",
        }
    ],
}

MOCK_RAW_PRODUCT_2 = {
    "id": "c3_prod_002",
    "title": "Anker Soundcore Space One",
    "description": "Adaptive ANC headphones with 55-hour battery.",
    "brands": [{"id": "b2", "name": "Anker"}],
    "category": {"slug": "over-ear-headphones", "title": "Over-Ear Headphones"},
    "images": [
        {"url": "https://cdn.trychannel3.com/img2.jpg", "is_main_image": True}
    ],
    "key_features": ["55-hour battery", "Adaptive ANC"],
    "structured_attributes": {"charging": ["USB-C"]},
    "offers": [
        {
            "id": "off_02_bb",
            "domain": "bestbuy.com",
            "url": "https://buy.trychannel3.com/deal2_bb",
            "price": {"price": 79.99, "currency": "USD", "compare_at_price": 99.99},
            "availability": "InStock",
        },
        {
            "id": "off_02_cr",
            "domain": "crutchfield.com",
            "url": "https://buy.trychannel3.com/deal2_cr",
            "price": {"price": 99.99, "currency": "USD", "compare_at_price": None},
            "availability": "InStock",
        },
    ],
}


# -------------------------------------------------------------------------
# 1. Successful provider response
# -------------------------------------------------------------------------

def test_successful_provider_response():
    """Verify Channel3Client executes POST /v1/search and returns parsed JSON."""
    client = Channel3Client(api_key="test-mock-key")
    mock_payload = {"products": [MOCK_RAW_PRODUCT_1], "next_page_token": None}

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = client.search_products(query="wireless headphones", limit=5)
        assert mock_post.called
        assert len(result["products"]) == 1
        assert result["products"][0]["id"] == "c3_prod_001"


# -------------------------------------------------------------------------
# 2. Product normalization
# -------------------------------------------------------------------------

def test_product_normalization():
    """Verify Channel3Normalizer accurately maps fields from raw provider JSON."""
    norm = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_1)
    assert norm is not None
    assert norm.name == "Sennheiser Accentum Wireless Headphones"
    assert norm.brand == "Sennheiser"
    assert norm.category == "Over-Ear Headphones"
    assert "Hybrid Active Noise Cancelling" in norm.description
    assert norm.provider == "channel3"
    assert norm.provider_product_id == "c3_prod_001"
    assert norm.is_synthetic is False
    assert norm.image_url == "https://cdn.trychannel3.com/cleaned/img1.jpg"
    assert norm.specifications["connectivity-technology"] == "Bluetooth, Wireless"
    assert "50-hour battery life" in norm.specifications["key_features"]


# -------------------------------------------------------------------------
# 3. Retailer-offer normalization
# -------------------------------------------------------------------------

def test_retailer_offer_normalization():
    """Verify retailer offer normalization extracts domain, price, and currency accurately."""
    norm = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_1)
    assert norm is not None
    assert len(norm.offers) == 1
    offer = norm.offers[0]
    assert offer.retailer_name == "newegg.com"
    assert offer.price == 175.0
    assert offer.currency == "USD"
    assert offer.compare_at_price == 199.99
    assert offer.availability == "IN_STOCK"
    assert offer.is_synthetic is False
    assert offer.product_url == "https://buy.trychannel3.com/deal1"


# -------------------------------------------------------------------------
# 4. Multiple offers for one product
# -------------------------------------------------------------------------

def test_multiple_offers_for_one_product():
    """Verify products with multiple competing retailer offers are fully captured."""
    norm = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_2)
    assert norm is not None
    assert len(norm.offers) == 2
    domains = [o.retailer_name for o in norm.offers]
    assert "bestbuy.com" in domains
    assert "crutchfield.com" in domains
    prices = [o.price for o in norm.offers]
    assert 79.99 in prices
    assert 99.99 in prices


# -------------------------------------------------------------------------
# 5. Missing optional fields
# -------------------------------------------------------------------------

def test_missing_optional_fields():
    """Verify safe fallbacks when optional metadata fields are missing."""
    sparse_raw = {
        "id": "sparse_01",
        "title": "Minimal Generic Headset",
        "brands": None,
        "category": None,
        "description": None,
        "images": [],
        "offers": [],
        "structured_attributes": None,
    }
    norm = Channel3Normalizer.normalize_product(sparse_raw)
    assert norm is not None
    assert norm.name == "Minimal Generic Headset"
    assert norm.brand == "Unknown"
    assert norm.category == "General"
    assert norm.description == ""
    assert norm.image_url is None
    assert norm.specifications == {}
    assert len(norm.offers) == 0


# -------------------------------------------------------------------------
# 6. Duplicate products handling
# -------------------------------------------------------------------------

def test_duplicate_products_upsert(test_session: Session):
    """Verify upserting an existing Channel3 product updates details without creating duplicates."""
    norm1 = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_1)
    assert norm1 is not None

    # First insert
    saved1 = ProductRepository.upsert_channel3_product(test_session, norm1)
    initial_id = saved1.id
    assert saved1.offers[0].price == 175.0

    # Modify mock data (price drop) and upsert again
    updated_raw = dict(MOCK_RAW_PRODUCT_1)
    updated_raw["offers"] = [
        {
            "id": "off_01",
            "domain": "newegg.com",
            "url": "https://buy.trychannel3.com/deal1_new",
            "price": {"price": 149.99, "currency": "USD", "compare_at_price": 199.99},
            "availability": "InStock",
        }
    ]
    norm2 = Channel3Normalizer.normalize_product(updated_raw)
    assert norm2 is not None

    saved2 = ProductRepository.upsert_channel3_product(test_session, norm2)
    # Primary key must be stable (no new duplicate record)
    assert saved2.id == initial_id
    assert len(saved2.offers) == 1
    assert saved2.offers[0].price == 149.99


# -------------------------------------------------------------------------
# 7. Invalid provider response
# -------------------------------------------------------------------------

def test_invalid_provider_response():
    """Verify Channel3ResponseError is raised on malformed JSON payload."""
    client = Channel3Client(api_key="test-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("Corrupt JSON")

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(Channel3ResponseError):
            client.search_products(query="test")


# -------------------------------------------------------------------------
# 8. Authentication failure
# -------------------------------------------------------------------------

def test_authentication_failure():
    """Verify Channel3AuthError is raised on HTTP 401 or missing API key."""
    # Test missing API key
    client_no_key = Channel3Client(api_key="")
    with pytest.raises(Channel3AuthError):
        client_no_key.search_products(query="headphones")

    # Test HTTP 401 from server
    client = Channel3Client(api_key="invalid-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 401
    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(Channel3AuthError):
            client.search_products(query="headphones")


# -------------------------------------------------------------------------
# 9. Rate-limit response
# -------------------------------------------------------------------------

def test_rate_limit_response():
    """Verify Channel3RateLimitError is raised on HTTP 429 with Retry-After header."""
    client = Channel3Client(api_key="test-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.headers = {"Retry-After": "15"}

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(Channel3RateLimitError) as exc_info:
            client.search_products(query="headphones")
        assert exc_info.value.retry_after == 15


# -------------------------------------------------------------------------
# 10. Timeout handling
# -------------------------------------------------------------------------

def test_timeout_handling():
    """Verify Channel3TimeoutError is raised when request exceeds deadline."""
    client = Channel3Client(api_key="test-key")
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Timeout")):
        with pytest.raises(Channel3TimeoutError):
            client.search_products(query="headphones")


# -------------------------------------------------------------------------
# 11. Empty search results
# -------------------------------------------------------------------------

def test_empty_search_results(test_session: Session):
    """Verify clean empty response handling when Channel3 returns 0 matches."""
    client = Channel3Client(api_key="test-key")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"products": [], "next_page_token": None}

    with patch("httpx.Client.post", return_value=mock_resp):
        res = ProductService.search_live_products(
            db=test_session, query="nonexistent item xyz12345", limit=10, client=client
        )
        assert res.total == 0
        assert res.items == []


# -------------------------------------------------------------------------
# 12. Provider failure without synthetic fallback
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_failure_without_synthetic_fallback(async_client: httpx.AsyncClient):
    """Verify API returns 502/504 Bad Gateway on provider failure and NEVER silently falls back to synthetic data."""
    with patch("backend.app.services.channel3_client.Channel3Client.search_products") as mock_search:
        mock_search.side_effect = Channel3ServerError("Upstream 500 error")
        response = await async_client.get("/api/products/search?q=wireless+headphones")
        assert response.status_code == 502
        body = response.json()
        assert body["type"] == "about:blank"
        assert "Channel3 provider error" in body["detail"]


# -------------------------------------------------------------------------
# 13. Existing product API compatibility
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_product_api_compatibility(
    async_client: httpx.AsyncClient, test_session: Session
):
    """Verify GET /api/products returns synthetic and live products with source indicators."""
    # Insert a live product into test db
    norm = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_1)
    assert norm is not None
    ProductRepository.upsert_channel3_product(test_session, norm)

    # 1. Fetch all products
    res_all = await async_client.get("/api/products")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] >= 19  # 18 synthetic + 1 live

    # 2. Filter by live source
    res_live = await async_client.get("/api/products?source=live")
    assert res_live.status_code == 200
    data_live = res_live.json()
    assert data_live["total"] == 1
    assert data_live["items"][0]["is_synthetic"] is False
    assert data_live["items"][0]["provider"] == "channel3"

    # 3. Filter by synthetic source
    res_synth = await async_client.get("/api/products?source=synthetic")
    assert res_synth.status_code == 200
    data_synth = res_synth.json()
    assert data_synth["total"] == 18
    assert all(item["is_synthetic"] is True for item in data_synth["items"])


# -------------------------------------------------------------------------
# 14. Product comparison compatibility
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_product_comparison_compatibility(
    async_client: httpx.AsyncClient, test_session: Session
):
    """Verify comparing a live Channel3 product with a synthetic product."""
    # Insert live product
    norm = Channel3Normalizer.normalize_product(MOCK_RAW_PRODUCT_2)
    assert norm is not None
    live_prod = ProductRepository.upsert_channel3_product(test_session, norm)

    # Get a synthetic product
    synth_prods, _ = ProductRepository.list_products(test_session, page=1, page_size=1)
    synth_id = synth_prods[0].id

    res = await async_client.get(f"/api/products/compare?ids={live_prod.id}&ids={synth_id}")
    assert res.status_code == 200
    comp = res.json()
    assert comp["compared_count"] == 2
    prod_names = [p["name"] for p in comp["products"]]
    assert live_prod.name in prod_names
    assert synth_prods[0].name in prod_names
    assert "cheapest_product" in comp["price_summary"]


# -------------------------------------------------------------------------
# 15. Spending-policy engine compatibility
# -------------------------------------------------------------------------

def test_spending_policy_engine_compatibility(test_session: Session):
    """Verify deterministic policy engine evaluates purchase eligibility against live Channel3 prices."""
    # Create guest session and spending mandate
    session_model = ShoppingSession(session_identifier="channel3-test-session")
    test_session.add(session_model)
    test_session.commit()
    test_session.refresh(session_model)

    mandate = SpendingMandate(
        session_id=session_model.id,
        total_budget=100.0,
        maximum_item_price=100.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    # Case A: Live offer within budget (Soundcore Space One at $79.99) -> ALLOWED
    decision_allowed = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=79.99,
        quantity=1,
        currency="USD",
    )
    assert decision_allowed.is_allowed is True
    assert decision_allowed.decision == "ALLOWED"

    # Case B: Live offer exceeding total budget (Sennheiser Accentum at $175.00) -> BLOCKED
    decision_blocked = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=175.00,
        quantity=1,
        currency="USD",
    )
    assert decision_blocked.is_allowed is False
    assert decision_blocked.decision in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
    assert any("exceeds" in r for r in decision_blocked.reasons)

"""
BUYWISE AI — Phase 4A.4B Dedicated Test Suite: Dynamic Explore Product Population
Verifies:
1. Dynamic randomized product discovery across repeated requests/seeds.
2. Zero duplicate products within single collection and across paged slices with stable seed.
3. Category and brand diversification in random discovery mode.
4. Correct product provenance (Live Channel3 vs Demo Synthetic badges).
5. Existing filters (category, brand, min_price, max_price, source) working seamlessly with random ordering.
6. No fabricated product data (all returned items exist in database with real offers).
7. Empty catalogue handling without error.
8. Backward compatibility: default GET /api/products returns alphabetical order; keyword search intact.
9. Controlled Channel3 discovery with cooldown enforcement and duplicate prevention.
10. Frontend Refresh Discovery elements and interaction.
"""

import pytest
import httpx
from unittest.mock import MagicMock
from fastapi import status

from backend.app.core.database import SessionLocal
from backend.app.models.shopping import Product, RetailerOffer
from backend.app.services.product_service import ProductService
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.channel3_client import Channel3Client


@pytest.mark.asyncio
async def test_dynamic_random_selection_different_orders_across_seeds(async_client: httpx.AsyncClient):
    """
    Requirement 1: Explore should display a varied/random selection of products.
    Different seeds or repeated random calls produce different product orderings when sufficient products exist.
    """
    res1 = await async_client.get("/api/products?order_by=random&seed=101&page=1&page_size=10")
    assert res1.status_code == status.HTTP_200_OK
    data1 = res1.json()
    items1 = data1["items"]
    ids1 = [item["id"] for item in items1]

    res2 = await async_client.get("/api/products?order_by=random&seed=9999&page=1&page_size=10")
    assert res2.status_code == status.HTTP_200_OK
    data2 = res2.json()
    items2 = data2["items"]
    ids2 = [item["id"] for item in items2]

    assert len(ids1) >= 5
    assert len(ids2) >= 5
    # With distinct seeds, the orderings should not be identical
    assert ids1 != ids2, "Different random seeds must yield different product orderings"


@pytest.mark.asyncio
async def test_no_duplicate_products_in_collection(async_client: httpx.AsyncClient):
    """
    Requirement 1: Avoid duplicates within the displayed collection.
    - No duplicates in page 1.
    - No duplicates between page 1 and page 2 when navigating with the same seed.
    """
    seed = 42
    res_p1 = await async_client.get(f"/api/products?order_by=random&seed={seed}&page=1&page_size=8")
    assert res_p1.status_code == status.HTTP_200_OK
    items_p1 = res_p1.json()["items"]
    ids_p1 = [item["id"] for item in items_p1]

    # No duplicate products within page 1
    assert len(ids_p1) == len(set(ids_p1)), "Page 1 contains duplicate product IDs"

    res_p2 = await async_client.get(f"/api/products?order_by=random&seed={seed}&page=2&page_size=8")
    assert res_p2.status_code == status.HTTP_200_OK
    items_p2 = res_p2.json()["items"]
    ids_p2 = [item["id"] for item in items_p2]

    # No duplicate products within page 2
    assert len(ids_p2) == len(set(ids_p2)), "Page 2 contains duplicate product IDs"

    # Crucial pagination invariant: Page 1 and Page 2 must not overlap
    overlap = set(ids_p1).intersection(set(ids_p2))
    assert len(overlap) == 0, f"Page 1 and Page 2 overlap with duplicate IDs: {overlap}"


@pytest.mark.asyncio
async def test_category_diversification_in_random_discovery(async_client: httpx.AsyncClient):
    """
    Requirement 1: Diversify categories, brands and retailers where available.
    In dynamic discovery mode across catalog items, adjacent items should rotate across categories.
    """
    res = await async_client.get("/api/products?order_by=random&seed=777&page=1&page_size=10")
    assert res.status_code == status.HTTP_200_OK
    items = res.json()["items"]

    categories = [item["category"] for item in items]
    unique_categories = set(categories)

    # In a catalog with 5 distinct categories, a 10-item sample must exhibit multiple categories
    assert len(unique_categories) >= 3, f"Expected category diversity, got categories: {categories}"

    # Verify that the first 3 items are not all identical category
    first_three_cats = categories[:3]
    assert len(set(first_three_cats)) >= 2, f"First 3 items should not all be the same category: {first_three_cats}"


@pytest.mark.asyncio
async def test_correct_product_provenance_badges(async_client: httpx.AsyncClient):
    """
    Requirement 2: Maintain accurate Live Channel3 and Demo provenance badges.
    Never fabricate products or misrepresent synthetic items.
    """
    res = await async_client.get("/api/products?order_by=random&seed=12&page=1&page_size=20")
    assert res.status_code == status.HTTP_200_OK
    items = res.json()["items"]

    for item in items:
        assert "is_synthetic" in item
        assert "provider" in item
        if item["is_synthetic"]:
            assert item["provider"] in ("synthetic", "demo")
        else:
            assert item["provider"] == "channel3"
            assert item["provider_product_id"] is not None


@pytest.mark.asyncio
async def test_existing_filters_work_with_random_ordering(async_client: httpx.AsyncClient):
    """
    Requirement 1 & 5: Preserve existing category, brand, price and keyword filters with random order.
    """
    # 1. Filter by category
    res_cat = await async_client.get("/api/products?category=Electronics&order_by=random&seed=1&page_size=10")
    assert res_cat.status_code == status.HTTP_200_OK
    items_cat = res_cat.json()["items"]
    assert len(items_cat) > 0
    for item in items_cat:
        assert item["category"].lower() == "electronics"

    # 2. Filter by brand
    res_brand = await async_client.get("/api/products?brand=Apple&order_by=random&seed=2&page_size=10")
    assert res_brand.status_code == status.HTTP_200_OK
    items_brand = res_brand.json()["items"]
    assert len(items_brand) > 0
    for item in items_brand:
        assert item["brand"].lower() == "apple"

    # 3. Filter by price range
    res_price = await async_client.get("/api/products?min_price=100&max_price=500&order_by=random&seed=3&page_size=10")
    assert res_price.status_code == status.HTTP_200_OK
    items_price = res_price.json()["items"]
    assert len(items_price) > 0
    for item in items_price:
        # At least one offer must fall within [100, 500]
        offers_in_range = [o for o in item["offers"] if 100 <= o["price"] <= 500]
        assert len(offers_in_range) > 0

    # 4. Filter by source
    res_source = await async_client.get("/api/products?source=synthetic&order_by=random&seed=4&page_size=10")
    assert res_source.status_code == status.HTTP_200_OK
    items_source = res_source.json()["items"]
    assert len(items_source) > 0
    for item in items_source:
        assert item["is_synthetic"] is True


@pytest.mark.asyncio
async def test_no_fabricated_product_data(async_client: httpx.AsyncClient):
    """
    Requirement 2: Preserve actual product names, images, prices, currencies, merchant information.
    Never fabricate products or offers.
    """
    res = await async_client.get("/api/products?order_by=random&seed=555&page=1&page_size=15")
    assert res.status_code == status.HTTP_200_OK
    items = res.json()["items"]

    with SessionLocal() as db:
        for item in items:
            # Must exist in the authentic database
            db_product = ProductRepository.get_by_id(db, item["id"])
            assert db_product is not None, f"Product {item['id']} is not in the database!"
            assert db_product.name == item["name"]
            assert db_product.brand == item["brand"]
            assert db_product.category == item["category"]

            # Offers must be real
            assert len(item["offers"]) > 0
            for offer in item["offers"]:
                assert offer["price"] > 0
                assert len(offer["currency"]) == 3
                assert offer["retailer_name"] is not None and len(offer["retailer_name"]) > 0


@pytest.mark.asyncio
async def test_empty_catalogue_handling(async_client: httpx.AsyncClient):
    """
    Requirement 4 & 5: Display useful empty state when no products match filters.
    """
    res = await async_client.get("/api/products?q=nonexistent_xyz987654321_item&order_by=random")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_pages"] == 1


@pytest.mark.asyncio
async def test_backward_compatibility_default_alphabetical_and_search(async_client: httpx.AsyncClient):
    """
    Requirement 5: Existing product listing and search behaviour remaining intact.
    - Default GET /api/products without order_by returns alphabetical ascending order.
    - Keyword search works as expected.
    """
    res = await async_client.get("/api/products?page=1&page_size=20")
    assert res.status_code == status.HTTP_200_OK
    items = res.json()["items"]
    names = [item["name"] for item in items]
    assert names == sorted(names), "Default catalog listing must remain alphabetical ascending"

    # Search query
    res_q = await async_client.get("/api/products?q=iPhone")
    assert res_q.status_code == status.HTTP_200_OK
    q_items = res_q.json()["items"]
    assert len(q_items) > 0
    assert any("iphone" in item["name"].lower() for item in q_items)


@pytest.mark.asyncio
async def test_controlled_channel3_discovery_with_cooldown_and_no_duplicates():
    """
    Requirement 2 & 3:
    - Controlled Channel3 discovery to expand catalogue.
    - Cooldown enforcement: subsequent call within cooldown returns 0 and skips network.
    - Upserting avoids duplicate provider product records.
    """
    mock_client = MagicMock(spec=Channel3Client)
    mock_client.is_configured = True
    mock_client.search_products.return_value = {
        "products": [
            {
                "id": "c3_test_monitor_001",
                "title": "LG UltraFine 27-inch 4K UHD Monitor",
                "brand": "LG",
                "category": "Electronics",
                "description": "27-inch IPS UHD 4K Monitor with HDR10",
                "image_url": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf",
                "specs": {"resolution": "3840x2160", "refresh_rate": "60Hz"},
                "offers": [
                    {
                        "id": "c3_off_lg_01",
                        "merchant_name": "Best Buy",
                        "price": 349.99,
                        "currency": "USD",
                        "product_url": "https://www.bestbuy.com/site/lg27",
                        "availability": "IN_STOCK",
                    }
                ],
            }
        ]
    }

    with SessionLocal() as db:
        # First discovery call (force=True to bypass any leftover cooldown)
        count1, msg1 = ProductService.discover_live_products(
            db=db,
            query="4k monitor",
            client=mock_client,
            force=True,
        )
        assert count1 == 1
        assert "Discovered and stored" in msg1

        # Verify product is stored in DB
        prod = ProductRepository.get_by_provider_id(db, provider="channel3", provider_product_id="c3_test_monitor_001")
        assert prod is not None
        assert prod.name == "LG UltraFine 27-inch 4K UHD Monitor"
        assert prod.is_synthetic is False
        assert prod.provider == "channel3"

        # Second call immediately after (force=False) must trigger cooldown protection
        count2, msg2 = ProductService.discover_live_products(
            db=db,
            query="4k monitor",
            client=mock_client,
            force=False,
        )
        assert count2 == 0
        assert "cooldown active" in msg2.lower()

        # Call with force=True again to verify idempotent upsert (no duplicates!)
        count3, msg3 = ProductService.discover_live_products(
            db=db,
            query="4k monitor",
            client=mock_client,
            force=True,
        )
        assert count3 == 1

        # Check total count in DB with this provider ID is still strictly 1
        matching_prods = db.query(Product).filter(
            Product.provider == "channel3",
            Product.provider_product_id == "c3_test_monitor_001"
        ).all()
        assert len(matching_prods) == 1, "Upsert must not create duplicate provider products"


@pytest.mark.asyncio
async def test_frontend_refresh_discovery_elements(async_client: httpx.AsyncClient):
    """
    Requirement 4: Verify frontend contains the Refresh Discovery button, order dropdown,
    and app.js includes the required event handlers.
    """
    html_res = await async_client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert html_res.status_code == status.HTTP_200_OK
    html = html_res.text

    assert 'id="btn-refresh-discovery"' in html
    assert "Refresh Discovery" in html
    assert 'id="explore-order-filter"' in html
    assert 'value="random"' in html

    app_res = await async_client.get("/assets/js/app.js")
    assert app_res.status_code == status.HTTP_200_OK
    app_js = app_res.text

    assert "btn-refresh-discovery" in app_js
    assert "explore-order-filter" in app_js
    assert "Dynamic Discovery" in app_js


@pytest.mark.asyncio
async def test_all_seven_category_filters(async_client: httpx.AsyncClient):
    """
    Step 5 Requirement: Test all seven category filters against actual category mappings.
    Verifies that 'All', 'Electronics', 'Audio', 'Wearables', 'Computing', 'Smart Home', and 'Gaming'
    each return genuine matching products without zero-product false negatives.
    """
    expected_categories = [
        ("All", None),
        ("Electronics", "Electronics"),
        ("Audio", "Audio"),
        ("Wearables", "Wearables"),
        ("Computing", "Computing"),
        ("Smart Home", "Smart Home"),
        ("Gaming", "Gaming"),
    ]

    for label, param in expected_categories:
        url = "/api/products?page=1&page_size=20"
        if param:
            url += f"&category={param}"

        res = await async_client.get(url)
        assert res.status_code == status.HTTP_200_OK, f"Failed for category: {label}"
        data = res.json()
        items = data.get("items", [])
        total = data.get("total", 0)

        # Every one of the 7 supported categories must return at least 1 genuine product
        assert total > 0, f"Category '{label}' returned 0 products! Expected matching products."
        assert len(items) > 0, f"Category '{label}' items list is empty!"

        # Spot-check data validity
        first = items[0]
        assert "id" in first
        assert "name" in first
        assert len(first["offers"]) > 0


@pytest.mark.asyncio
async def test_genuinely_empty_category_returns_zero_products(async_client: httpx.AsyncClient):
    """
    Step 5 Requirement: Verify that genuinely empty / non-existent categories cleanly return 0 products.
    """
    res = await async_client.get("/api/products?category=NonExistentCategoryXYZ987")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_pages"] == 1


"""
BUYWISE AI — Authentic Price History Foundation Tests (Phase 3A).
Verifies PriceSnapshot data integrity, baseline snapshot creation, change detection,
deduplication, retailer & currency segregation, provider offer identification,
synthetic exclusion, invalid price rejection, offer replacement survival,
server-side cooldown enforcement, and graceful provider failure recovery.
"""

from datetime import datetime, timezone, timedelta
import math
from unittest.mock import MagicMock
import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.app.models.shopping import Product, RetailerOffer, PriceSnapshot
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.product_service import ProductService
from backend.app.services.channel3_client import (
    Channel3Client,
    Channel3RateLimitError,
    Channel3ServerError,
    Channel3TimeoutError,
)
from backend.app.services.channel3_normalizer import Channel3Normalizer, NormalizedProduct, NormalizedOffer


def create_live_channel3_product(db: Session, c3_id: str = "c3_prod_hist_1") -> Product:
    """Helper to create a live Channel3 product in the test database."""
    prod = Product(
        name="Audio-Technica ATH-M50xBT2",
        brand="Audio-Technica",
        category="Audio",
        description="Wireless Professional Monitor Headphones",
        specifications={"connectivity": "Bluetooth 5.0", "battery_life": "50 hours"},
        image_url="https://example.com/ath.jpg",
        provider="channel3",
        provider_product_id=c3_id,
        is_synthetic=False,
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod


def test_baseline_snapshot_on_first_genuine_offer(test_session: Session):
    """Test 1: First genuine live offer creates a baseline snapshot."""
    product = create_live_channel3_product(test_session, "c3_test_1")
    offer_data = {
        "retailer_name": "Best Buy",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "provider_offer_id": "bb_off_01",
        "is_synthetic": False,
    }

    snap = ProductRepository.record_price_snapshot(test_session, product, offer_data)
    test_session.commit()

    assert snap is not None
    assert snap.product_id == product.id
    assert snap.retailer_name == "Best Buy"
    assert snap.price == 199.99
    assert snap.currency == "USD"
    assert snap.availability == "IN_STOCK"
    assert snap.provider_offer_id == "bb_off_01"
    assert snap.is_synthetic is False

    snapshots = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(snapshots) == 1
    assert snapshots[0].id == snap.id


def test_changed_price_creates_new_snapshot(test_session: Session):
    """Test 2: Changed price creates a new chronological snapshot."""
    product = create_live_channel3_product(test_session, "c3_test_2")

    # Baseline at 199.99
    snap1 = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "retrieved_at": datetime.now(timezone.utc) - timedelta(days=2),
    })
    test_session.commit()

    # Price drops to 179.99
    snap2 = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 179.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "retrieved_at": datetime.now(timezone.utc) - timedelta(days=1),
    })
    test_session.commit()

    assert snap1 is not None
    assert snap2 is not None
    assert snap1.id != snap2.id
    assert snap2.price == 179.99

    snapshots = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(snapshots) == 2
    assert snapshots[0].price == 199.99
    assert snapshots[1].price == 179.99


def test_unchanged_offer_does_not_create_duplicate_snapshot(test_session: Session):
    """Test 3: Unchanged offer does not create duplicate snapshots."""
    product = create_live_channel3_product(test_session, "c3_test_3")

    # Baseline
    snap1 = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
    })
    test_session.commit()

    # Ingest same offer with unchanged price
    snap2 = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
    })
    test_session.commit()

    assert snap1 is not None
    assert snap2 is not None
    assert snap1.id == snap2.id  # Returns existing snapshot

    snapshots = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(snapshots) == 1


def test_different_retailers_remain_separate(test_session: Session):
    """Test 4: Different retailers maintain completely separate price timelines."""
    product = create_live_channel3_product(test_session, "c3_test_4")

    # Best Buy at $199.99
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 199.99,
        "currency": "USD",
    })
    # Amazon at $189.99
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Amazon",
        "price": 189.99,
        "currency": "USD",
    })
    # Amazon drops to $179.99
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Amazon",
        "price": 179.99,
        "currency": "USD",
    })
    test_session.commit()

    bb_snaps = ProductRepository.get_price_snapshots(test_session, product.id, retailer_name="Best Buy")
    amz_snaps = ProductRepository.get_price_snapshots(test_session, product.id, retailer_name="Amazon")

    assert len(bb_snaps) == 1
    assert bb_snaps[0].price == 199.99

    assert len(amz_snaps) == 2
    assert amz_snaps[0].price == 189.99
    assert amz_snaps[1].price == 179.99


def test_different_currencies_remain_separate(test_session: Session):
    """Test 5: Different currencies remain separate and are never combined or confused."""
    product = create_live_channel3_product(test_session, "c3_test_5")

    # USD offer
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "GlobalStore",
        "price": 100.00,
        "currency": "USD",
    })
    # EUR offer
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "GlobalStore",
        "price": 92.00,
        "currency": "EUR",
    })
    test_session.commit()

    usd_snaps = ProductRepository.get_price_snapshots(test_session, product.id, currency="USD")
    eur_snaps = ProductRepository.get_price_snapshots(test_session, product.id, currency="EUR")

    assert len(usd_snaps) == 1
    assert usd_snaps[0].currency == "USD"
    assert usd_snaps[0].price == 100.00

    assert len(eur_snaps) == 1
    assert eur_snaps[0].currency == "EUR"
    assert eur_snaps[0].price == 92.00


def test_different_provider_offer_ids_distinguishable(test_session: Session):
    """Test 6: Distinct provider offer IDs for the same retailer are distinguishable."""
    product = create_live_channel3_product(test_session, "c3_test_6")

    snap_black = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Newegg",
        "price": 150.00,
        "currency": "USD",
        "provider_offer_id": "off_var_black",
    })
    snap_white = ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Newegg",
        "price": 160.00,
        "currency": "USD",
        "provider_offer_id": "off_var_white",
    })
    test_session.commit()

    assert snap_black.id != snap_white.id
    assert snap_black.provider_offer_id == "off_var_black"
    assert snap_white.provider_offer_id == "off_var_white"


def test_synthetic_products_do_not_create_price_history(test_session: Session):
    """Test 7: Synthetic demo products do not record authentic price snapshots."""
    synth_prod = Product(
        name="Synthetic Demo Headset",
        brand="Acme",
        category="Audio",
        provider="synthetic",
        is_synthetic=True,
    )
    test_session.add(synth_prod)
    test_session.commit()

    snap = ProductRepository.record_price_snapshot(test_session, synth_prod, {
        "retailer_name": "DemoStore",
        "price": 49.99,
        "currency": "USD",
    })
    test_session.commit()

    assert snap is None
    all_snaps = ProductRepository.get_price_snapshots(test_session, synth_prod.id)
    assert len(all_snaps) == 0


def test_invalid_prices_rejected(test_session: Session):
    """Test 8: Invalid, negative, zero, NaN, or non-finite prices are rejected."""
    product = create_live_channel3_product(test_session, "c3_test_8")

    invalid_cases = [
        -10.0,
        0.0,
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "not_a_number",
    ]

    for inv in invalid_cases:
        res = ProductRepository.record_price_snapshot(test_session, product, {
            "retailer_name": "TestStore",
            "price": inv,
            "currency": "USD",
        })
        assert res is None, f"Expected invalid price {inv} to be rejected"

    snapshots = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(snapshots) == 0


def test_existing_snapshots_survive_offer_replacement(test_session: Session):
    """Test 9: Historical snapshots survive when current RetailerOffer records are wiped & replaced."""
    # 1. Ingest initial product and offer
    norm1 = Channel3Normalizer.normalize_product({
        "id": "c3_survive_test",
        "title": "Bose QuietComfort Ultra",
        "brands": [{"name": "Bose"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "bestbuy.com", "price": {"price": 379.00, "currency": "USD"}, "availability": "InStock"}],
    })
    prod1 = ProductRepository.upsert_channel3_product(test_session, norm1)
    test_session.commit()

    snaps_1 = ProductRepository.get_price_snapshots(test_session, prod1.id)
    assert len(snaps_1) == 1
    assert snaps_1[0].price == 379.00

    # 2. Offer price updates to 329.00
    norm2 = Channel3Normalizer.normalize_product({
        "id": "c3_survive_test",
        "title": "Bose QuietComfort Ultra",
        "brands": [{"name": "Bose"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "bestbuy.com", "price": {"price": 329.00, "currency": "USD"}, "availability": "InStock"}],
    })
    ProductRepository.upsert_channel3_product(test_session, norm2)
    test_session.commit()

    snaps_2 = ProductRepository.get_price_snapshots(test_session, prod1.id)
    assert len(snaps_2) == 2

    # 3. Third update: bestbuy.com offer disappears and is replaced by target.com offer
    norm3 = Channel3Normalizer.normalize_product({
        "id": "c3_survive_test",
        "title": "Bose QuietComfort Ultra",
        "brands": [{"name": "Bose"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "target.com", "price": {"price": 349.00, "currency": "USD"}, "availability": "InStock"}],
    })
    ProductRepository.upsert_channel3_product(test_session, norm3)
    test_session.commit()

    # Current RetailerOffer only has target.com
    test_session.refresh(prod1)
    assert len(prod1.offers) == 1
    assert prod1.offers[0].retailer_name == "target.com"

    # But PriceSnapshots MUST contain all 3 snapshots: 2 for bestbuy.com and 1 for target.com!
    all_snaps = ProductRepository.get_price_snapshots(test_session, prod1.id)
    assert len(all_snaps) == 3
    bb_snaps = [s for s in all_snaps if s.retailer_name == "bestbuy.com"]
    target_snaps = [s for s in all_snaps if s.retailer_name == "target.com"]
    assert len(bb_snaps) == 2
    assert bb_snaps[0].price == 379.00
    assert bb_snaps[1].price == 329.00
    assert len(target_snaps) == 1
    assert target_snaps[0].price == 349.00


def test_cooldown_prevents_unnecessary_refreshes(test_session: Session):
    """Test 10: Server-side cooldown of 6 hours prevents unnecessary external API calls."""
    product = create_live_channel3_product(test_session, "c3_test_cooldown")
    # Freshly updated
    product.updated_at = datetime.now(timezone.utc)
    test_session.commit()

    mock_client = MagicMock(spec=Channel3Client)

    # Attempt refresh within cooldown
    p, refreshed, reason = ProductService.refresh_product_offers_with_cooldown(
        test_session, product.id, cooldown_hours=6, client=mock_client
    )

    assert refreshed is False
    assert "Cooldown active" in reason
    mock_client.get_product_by_id.assert_not_called()

    # Manually age product updated_at past 6 hours (e.g. 7 hours ago)
    product.updated_at = datetime.now(timezone.utc) - timedelta(hours=7)
    test_session.commit()

    mock_client.get_product_by_id.return_value = {
        "id": "c3_test_cooldown",
        "title": "Audio-Technica ATH-M50xBT2",
        "brands": [{"name": "Audio-Technica"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "bestbuy.com", "price": {"price": 189.99, "currency": "USD"}, "availability": "InStock"}],
    }

    # Now refresh succeeds
    p, refreshed, reason = ProductService.refresh_product_offers_with_cooldown(
        test_session, product.id, cooldown_hours=6, client=mock_client
    )

    assert refreshed is True
    assert "successfully refreshed" in reason
    mock_client.get_product_by_id.assert_called_once_with("c3_test_cooldown")


def test_provider_failures_do_not_destroy_history(test_session: Session):
    """Test 11: Provider errors (HTTP 429, 500, timeouts) do not destroy existing price history."""
    product = create_live_channel3_product(test_session, "c3_test_fail_recovery")

    # Establish baseline snapshot
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 200.00,
        "currency": "USD",
        "retrieved_at": datetime.now(timezone.utc) - timedelta(days=5),
    })
    ProductRepository.record_price_snapshot(test_session, product, {
        "retailer_name": "Best Buy",
        "price": 180.00,
        "currency": "USD",
        "retrieved_at": datetime.now(timezone.utc) - timedelta(days=2),
    })
    product.updated_at = datetime.now(timezone.utc) - timedelta(hours=8)  # Past cooldown
    test_session.commit()

    initial_snaps = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(initial_snaps) == 2

    # Simulate HTTP 429 Rate Limit from provider
    mock_client = MagicMock(spec=Channel3Client)
    mock_client.get_product_by_id.side_effect = Channel3RateLimitError("Quota exceeded", retry_after=60)

    p, refreshed, reason = ProductService.refresh_product_offers_with_cooldown(
        test_session, product.id, cooldown_hours=6, client=mock_client
    )

    assert refreshed is False
    assert "rate limit" in reason.lower()

    # Verify history is completely intact
    after_429 = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(after_429) == 2
    assert after_429[0].price == 200.00
    assert after_429[1].price == 180.00

    # Simulate 500 Server Error
    mock_client.get_product_by_id.side_effect = Channel3ServerError("Upstream 503 unavailable")
    p, refreshed, reason = ProductService.refresh_product_offers_with_cooldown(
        test_session, product.id, cooldown_hours=6, client=mock_client
    )
    assert refreshed is False
    assert "provider error" in reason.lower()

    # History still intact
    after_500 = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(after_500) == 2

    # Simulate Timeout
    mock_client.get_product_by_id.side_effect = Channel3TimeoutError("Socket timeout")
    p, refreshed, reason = ProductService.refresh_product_offers_with_cooldown(
        test_session, product.id, cooldown_hours=6, client=mock_client
    )
    assert refreshed is False

    # History still intact
    after_timeout = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(after_timeout) == 2


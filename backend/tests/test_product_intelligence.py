"""
BUYWISE AI — Product Intelligence Service & API Tests (Phase 3B).
Verifies deterministic calculations using authentic PriceSnapshot and RetailerOffer records:
1. Empty historical data
2. One baseline snapshot
3. Price increase
4. Price decrease
5. Correct all-time low and high
6. Multiple retailers
7. Multiple currencies
8. Different provider offer IDs
9. Unavailable retailer offers
10. Synthetic data exclusion
11. Insufficient price stability observations
12. API endpoint response
13. Unknown product handling
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy.orm import Session
import httpx

from backend.app.models.shopping import Product, RetailerOffer, PriceSnapshot
from backend.app.services.product_intelligence_service import ProductIntelligenceService
from backend.app.schemas.product import ProductIntelligenceResponse
from backend.app.repositories.product_repository import ProductRepository


def create_live_channel3_product(
    db: Session,
    c3_id: str = "c3_prod_intel_1",
    name: str = "Sony WH-1000XM5 Wireless Headphones",
) -> Product:
    """Helper to create a live Channel3 product in the test database."""
    prod = Product(
        name=name,
        brand="Sony",
        category="Audio",
        description="Premium Noise Canceling Headphones",
        specifications={"anc": "Industry-leading", "battery_hours": "30"},
        image_url="https://example.com/sony_xm5.jpg",
        provider="channel3",
        provider_product_id=c3_id,
        is_synthetic=False,
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod


def test_empty_historical_data(test_session: Session):
    """Test 1: Product with no offers or historical snapshots returns valid empty intelligence structure."""
    product = create_live_channel3_product(test_session, "c3_test_empty_hist")

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert intel.product_id == product.id
    assert intel.total_genuine_observations == 0
    assert intel.tracking_start_date is None
    assert intel.historical_lowest_prices == []
    assert intel.historical_highest_prices == []
    assert intel.price_changes == []
    assert intel.price_history == []
    assert len(intel.current_offers) == 0
    assert len(intel.best_current_prices) == 0
    assert len(intel.retailer_comparisons) == 0
    assert len(intel.price_stability) == 1
    assert intel.price_stability[0].status == "INSUFFICIENT_DATA"
    assert intel.price_stability[0].observation_count == 0


def test_one_baseline_snapshot(test_session: Session):
    """Test 2: Single snapshot reports baseline extremes and INSUFFICIENT_DATA for price change (never fake 0%)."""
    product = create_live_channel3_product(test_session, "c3_test_one_snap")
    snap_time = datetime.now(timezone.utc) - timedelta(days=3)

    snap = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon",
        price=348.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=snap_time,
        is_synthetic=False,
    )
    test_session.add(snap)
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert intel.total_genuine_observations == 1
    assert intel.tracking_start_date == snap_time

    # Historical low and high should both match the baseline snapshot
    assert len(intel.historical_lowest_prices) == 1
    assert intel.historical_lowest_prices[0].price == 348.00
    assert intel.historical_lowest_prices[0].retailer_name == "Amazon"
    assert len(intel.historical_highest_prices) == 1
    assert intel.historical_highest_prices[0].price == 348.00

    # Price change must report INSUFFICIENT_DATA and NEVER fabricate 0.0% change
    assert len(intel.price_changes) == 1
    change = intel.price_changes[0]
    assert change.status == "INSUFFICIENT_DATA"
    assert change.percentage_change is None
    assert change.absolute_change is None
    assert change.previous_price is None
    assert change.current_price == 348.00
    assert change.observation_count == 1

    # Stability must be INSUFFICIENT_DATA
    assert intel.price_stability[0].status == "INSUFFICIENT_DATA"
    assert intel.price_stability[0].observation_count == 1


def test_price_increase(test_session: Session):
    """Test 3: Price increase calculation matches exact expected percentage and absolute difference."""
    product = create_live_channel3_product(test_session, "c3_test_price_inc")
    t0 = datetime.now(timezone.utc) - timedelta(days=5)
    t1 = datetime.now(timezone.utc) - timedelta(days=1)

    # 100.00 -> 125.00 (+25.00, +25.00%)
    s1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Target",
        price=100.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=t0,
        is_synthetic=False,
    )
    s2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Target",
        price=125.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=t1,
        is_synthetic=False,
    )
    test_session.add_all([s1, s2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert len(intel.price_changes) == 1

    change = intel.price_changes[0]
    assert change.retailer_name == "Target"
    assert change.status == "INCREASED"
    assert change.previous_price == 100.00
    assert change.current_price == 125.00
    assert change.absolute_change == 25.00
    assert change.percentage_change == 25.00
    assert change.observation_count == 2
    assert change.previous_recorded_at == t0
    assert change.latest_recorded_at == t1


def test_price_decrease(test_session: Session):
    """Test 4: Price decrease calculation matches exact expected percentage and negative difference."""
    product = create_live_channel3_product(test_session, "c3_test_price_dec")
    t0 = datetime.now(timezone.utc) - timedelta(days=7)
    t1 = datetime.now(timezone.utc) - timedelta(days=2)

    # 200.00 -> 150.00 (-50.00, -25.00%)
    s1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="B&H Photo",
        price=200.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=t0,
        is_synthetic=False,
    )
    s2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="B&H Photo",
        price=150.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=t1,
        is_synthetic=False,
    )
    test_session.add_all([s1, s2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert len(intel.price_changes) == 1

    change = intel.price_changes[0]
    assert change.retailer_name == "B&H Photo"
    assert change.status == "DECREASED"
    assert change.previous_price == 200.00
    assert change.current_price == 150.00
    assert change.absolute_change == -50.00
    assert change.percentage_change == -25.00
    assert change.observation_count == 2


def test_correct_all_time_low_and_high(test_session: Session):
    """Test 5: Correct identification of all-time low and high across fluctuating historical snapshots."""
    product = create_live_channel3_product(test_session, "c3_test_extremes")
    now = datetime.now(timezone.utc)

    # Sequence of prices: 150, 120 (low), 180 (high), 130
    snaps = [
        PriceSnapshot(
            product_id=product.id,
            retailer_name="Best Buy",
            price=150.00,
            currency="USD",
            availability="IN_STOCK",
            recorded_at=now - timedelta(days=20),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=product.id,
            retailer_name="Best Buy",
            price=120.00,  # All-time low
            currency="USD",
            availability="IN_STOCK",
            recorded_at=now - timedelta(days=15),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=product.id,
            retailer_name="Amazon",
            price=180.00,  # All-time high
            currency="USD",
            availability="IN_STOCK",
            recorded_at=now - timedelta(days=10),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=product.id,
            retailer_name="Best Buy",
            price=130.00,
            currency="USD",
            availability="IN_STOCK",
            recorded_at=now - timedelta(days=2),
            is_synthetic=False,
        ),
    ]
    test_session.add_all(snaps)
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None

    # Lowest
    assert len(intel.historical_lowest_prices) == 1
    low = intel.historical_lowest_prices[0]
    assert low.price == 120.00
    assert low.retailer_name == "Best Buy"
    assert low.recorded_at == snaps[1].recorded_at

    # Highest
    assert len(intel.historical_highest_prices) == 1
    high = intel.historical_highest_prices[0]
    assert high.price == 180.00
    assert high.retailer_name == "Amazon"
    assert high.recorded_at == snaps[2].recorded_at


def test_multiple_retailers(test_session: Session):
    """Test 6: Multiple retailers maintain completely isolated price histories and changes."""
    product = create_live_channel3_product(test_session, "c3_test_multi_retailer")
    now = datetime.now(timezone.utc)

    # Amazon: 100.00 -> 110.00 (+10.0%)
    s_amz_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon",
        price=100.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=5),
        is_synthetic=False,
    )
    s_amz_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon",
        price=110.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    # Best Buy: 105.00 -> 95.00 (-9.52%)
    s_bb_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=105.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=4),
        is_synthetic=False,
    )
    s_bb_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=95.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    test_session.add_all([s_amz_1, s_amz_2, s_bb_1, s_bb_2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert len(intel.price_changes) == 2

    amz_change = next(c for c in intel.price_changes if c.retailer_name == "Amazon")
    assert amz_change.status == "INCREASED"
    assert amz_change.previous_price == 100.00
    assert amz_change.current_price == 110.00
    assert amz_change.absolute_change == 10.00
    assert amz_change.percentage_change == 10.00

    bb_change = next(c for c in intel.price_changes if c.retailer_name == "Best Buy")
    assert bb_change.status == "DECREASED"
    assert bb_change.previous_price == 105.00
    assert bb_change.current_price == 95.00
    assert bb_change.absolute_change == -10.00
    assert bb_change.percentage_change == -9.52


def test_multiple_currencies(test_session: Session):
    """Test 7: Multiple currencies remain strictly segregated across all metrics without conversions."""
    product = create_live_channel3_product(test_session, "c3_test_multi_curr")
    now = datetime.now(timezone.utc)

    # USD: 100 -> 120
    s_usd_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon US",
        price=100.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=4),
        is_synthetic=False,
    )
    s_usd_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon US",
        price=120.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    # EUR: 90 -> 95
    s_eur_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon DE",
        price=90.00,
        currency="EUR",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=4),
        is_synthetic=False,
    )
    s_eur_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon DE",
        price=95.00,
        currency="EUR",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    test_session.add_all([s_usd_1, s_usd_2, s_eur_1, s_eur_2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None

    # Lowest prices by currency
    assert len(intel.historical_lowest_prices) == 2
    usd_low = next(p for p in intel.historical_lowest_prices if p.currency == "USD")
    eur_low = next(p for p in intel.historical_lowest_prices if p.currency == "EUR")
    assert usd_low.price == 100.00
    assert eur_low.price == 90.00

    # Highest prices by currency
    assert len(intel.historical_highest_prices) == 2
    usd_high = next(p for p in intel.historical_highest_prices if p.currency == "USD")
    eur_high = next(p for p in intel.historical_highest_prices if p.currency == "EUR")
    assert usd_high.price == 120.00
    assert eur_high.price == 95.00

    # Price changes
    assert len(intel.price_changes) == 2
    usd_change = next(c for c in intel.price_changes if c.currency == "USD")
    eur_change = next(c for c in intel.price_changes if c.currency == "EUR")
    assert usd_change.percentage_change == 20.00
    assert eur_change.percentage_change == 5.56


def test_different_provider_offer_ids(test_session: Session):
    """Test 8: Offers with the same retailer name but different provider offer IDs are evaluated separately."""
    product = create_live_channel3_product(test_session, "c3_test_provider_offers")
    now = datetime.now(timezone.utc)

    # Best Buy Offer 1 (e.g. Standard Variant): 200.00 -> 220.00 (+10.0%)
    s1_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        provider_offer_id="bb_variant_standard",
        price=200.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=3),
        is_synthetic=False,
    )
    s1_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        provider_offer_id="bb_variant_standard",
        price=220.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    # Best Buy Offer 2 (e.g. Bundle Variant): 250.00 -> 240.00 (-4.0%)
    s2_1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        provider_offer_id="bb_variant_bundle",
        price=250.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=3),
        is_synthetic=False,
    )
    s2_2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        provider_offer_id="bb_variant_bundle",
        price=240.00,
        currency="USD",
        availability="IN_STOCK",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )

    test_session.add_all([s1_1, s1_2, s2_1, s2_2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert len(intel.price_changes) == 2

    std_change = next(c for c in intel.price_changes if c.provider_offer_id == "bb_variant_standard")
    assert std_change.status == "INCREASED"
    assert std_change.percentage_change == 10.00

    bundle_change = next(c for c in intel.price_changes if c.provider_offer_id == "bb_variant_bundle")
    assert bundle_change.status == "DECREASED"
    assert bundle_change.percentage_change == -4.00


def test_unavailable_retailer_offers(test_session: Session):
    """Test 9: Out-of-stock offers are never selected as best current price and never imply false savings."""
    product = create_live_channel3_product(test_session, "c3_test_unavail")

    # Amazon: $100 IN_STOCK
    o_amz = RetailerOffer(
        product_id=product.id,
        retailer_name="Amazon",
        price=100.00,
        currency="USD",
        availability="IN_STOCK",
        product_url="https://amazon.com/item",
        is_synthetic=False,
    )
    # Walmart: $75 OUT_OF_STOCK (Should NOT be selected as best price or used for savings calculation!)
    o_wal = RetailerOffer(
        product_id=product.id,
        retailer_name="Walmart",
        price=75.00,
        currency="USD",
        availability="OUT_OF_STOCK",
        product_url="https://walmart.com/item",
        is_synthetic=False,
    )
    # Target: $125 IN_STOCK
    o_tgt = RetailerOffer(
        product_id=product.id,
        retailer_name="Target",
        price=125.00,
        currency="USD",
        availability="IN_STOCK",
        product_url="https://target.com/item",
        is_synthetic=False,
    )

    test_session.add_all([o_amz, o_wal, o_tgt])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None

    # Best current price MUST be Amazon ($100.00), NOT Walmart ($75.00)
    assert len(intel.best_current_prices) == 1
    assert intel.best_current_prices[0].price == 100.00
    assert intel.best_current_prices[0].retailer_name == "Amazon"

    # Retailer comparison MUST compare Target ($125) and Amazon ($100)
    assert len(intel.retailer_comparisons) == 1
    comp = intel.retailer_comparisons[0]
    assert comp.cheapest_retailer == "Amazon"
    assert comp.cheapest_price == 100.00
    assert comp.highest_retailer == "Target"
    assert comp.highest_price == 125.00
    assert comp.price_difference == 25.00
    # Potential savings: (125 - 100) / 125 = 20.00%
    assert comp.potential_savings_percentage == 20.00
    assert comp.available_offers_count == 2
    assert "Walmart" in comp.unavailable_retailers
    assert "Amazon" in comp.available_retailers
    assert "Target" in comp.available_retailers


def test_synthetic_data_exclusion(test_session: Session):
    """Test 10: Synthetic products and synthetic snapshots are strictly excluded from genuine historical analytics."""
    # Synthetic demo product
    synth_prod = Product(
        name="Demo Laptop",
        brand="DemoBrand",
        category="Computers",
        description="Demo specifications",
        provider="synthetic",
        is_synthetic=True,
    )
    test_session.add(synth_prod)
    test_session.commit()

    # Even if someone erroneously inserted a synthetic snapshot
    synth_snap = PriceSnapshot(
        product_id=synth_prod.id,
        retailer_name="Demo Store",
        price=999.99,
        currency="USD",
        is_synthetic=True,
    )
    test_session.add(synth_snap)
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, synth_prod.id)
    assert intel is not None
    assert intel.is_synthetic is True
    assert intel.total_genuine_observations == 0
    assert intel.tracking_start_date is None
    assert intel.is_tracking_active is False
    assert intel.price_history == []
    assert intel.historical_lowest_prices == []
    assert intel.historical_highest_prices == []
    assert intel.price_changes == []
    assert intel.price_stability[0].status == "INSUFFICIENT_DATA"


def test_insufficient_price_stability_observations(test_session: Session):
    """Test 11: Price stability requires minimum observations and classifies STABLE vs VOLATILE accurately."""
    product = create_live_channel3_product(test_session, "c3_test_stability")
    now = datetime.now(timezone.utc)

    # Subtest A: Only 2 snapshots -> INSUFFICIENT_DATA
    s1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=100.00,
        currency="USD",
        recorded_at=now - timedelta(days=10),
        is_synthetic=False,
    )
    s2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=101.00,
        currency="USD",
        recorded_at=now - timedelta(days=8),
        is_synthetic=False,
    )
    test_session.add_all([s1, s2])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel.price_stability[0].status == "INSUFFICIENT_DATA"
    assert intel.price_stability[0].observation_count == 2
    assert intel.price_stability[0].volatility_score is None

    # Subtest B: Add 2 more snapshots with minimal fluctuation -> STABLE (CV < 5%)
    s3 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=99.50,
        currency="USD",
        recorded_at=now - timedelta(days=5),
        is_synthetic=False,
    )
    s4 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Best Buy",
        price=100.50,
        currency="USD",
        recorded_at=now - timedelta(days=2),
        is_synthetic=False,
    )
    test_session.add_all([s3, s4])
    test_session.commit()

    intel_stable = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel_stable.price_stability[0].status == "STABLE"
    assert intel_stable.price_stability[0].observation_count == 4
    assert intel_stable.price_stability[0].volatility_score is not None
    assert intel_stable.price_stability[0].volatility_score < 5.0

    # Subtest C: Another product with wild fluctuation -> VOLATILE (CV > 15%)
    prod_volatile = create_live_channel3_product(test_session, "c3_test_vol")
    v_snaps = [
        PriceSnapshot(
            product_id=prod_volatile.id,
            retailer_name="Amazon",
            price=100.00,
            currency="USD",
            recorded_at=now - timedelta(days=12),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=prod_volatile.id,
            retailer_name="Amazon",
            price=220.00,
            currency="USD",
            recorded_at=now - timedelta(days=8),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=prod_volatile.id,
            retailer_name="Amazon",
            price=80.00,
            currency="USD",
            recorded_at=now - timedelta(days=5),
            is_synthetic=False,
        ),
        PriceSnapshot(
            product_id=prod_volatile.id,
            retailer_name="Amazon",
            price=250.00,
            currency="USD",
            recorded_at=now - timedelta(days=1),
            is_synthetic=False,
        ),
    ]
    test_session.add_all(v_snaps)
    test_session.commit()

    intel_vol = ProductIntelligenceService.get_product_intelligence(test_session, prod_volatile.id)
    assert intel_vol.price_stability[0].status == "VOLATILE"
    assert intel_vol.price_stability[0].observation_count == 4
    assert intel_vol.price_stability[0].volatility_score > 15.0


@pytest.mark.asyncio
async def test_api_endpoint_response(async_client: httpx.AsyncClient, test_session: Session):
    """Test 12: GET /api/products/{product_id}/intelligence returns HTTP 200 with complete JSON schema."""
    product = create_live_channel3_product(test_session, "c3_api_test_prod")
    now = datetime.now(timezone.utc)

    # Add offer
    offer = RetailerOffer(
        product_id=product.id,
        retailer_name="Amazon",
        price=189.99,
        currency="USD",
        product_url="https://amazon.com/sony",
        availability="IN_STOCK",
        is_synthetic=False,
    )
    test_session.add(offer)

    # Add snapshots
    s1 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon",
        price=199.99,
        currency="USD",
        recorded_at=now - timedelta(days=5),
        is_synthetic=False,
    )
    s2 = PriceSnapshot(
        product_id=product.id,
        retailer_name="Amazon",
        price=189.99,
        currency="USD",
        recorded_at=now - timedelta(days=1),
        is_synthetic=False,
    )
    test_session.add_all([s1, s2])
    test_session.commit()

    response = await async_client.get(f"/api/products/{product.id}/intelligence")
    assert response.status_code == 200
    data = response.json()

    # Validate schema compatibility
    validated = ProductIntelligenceResponse.model_validate(data)
    assert validated.product_id == product.id
    assert validated.total_genuine_observations == 2
    assert validated.is_tracking_active is True
    assert len(validated.best_current_prices) == 1
    assert validated.best_current_prices[0].price == 189.99
    assert len(validated.historical_lowest_prices) == 1
    assert validated.historical_lowest_prices[0].price == 189.99
    assert len(validated.historical_highest_prices) == 1
    assert validated.historical_highest_prices[0].price == 199.99
    assert len(validated.price_changes) == 1
    assert validated.price_changes[0].status == "DECREASED"
    assert validated.price_changes[0].percentage_change == -5.00
    assert len(validated.price_history) == 2


@pytest.mark.asyncio
async def test_unknown_product_handling(async_client: httpx.AsyncClient):
    """Test 13: Non-existent product ID returns HTTP 404 with descriptive error detail."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await async_client.get(f"/api/products/{fake_id}/intelligence")
    assert response.status_code == 404
    body = response.json()
    assert f"Product with ID '{fake_id}' was not found." in body["detail"]



def test_baseline_snapshot_created_for_preexisting_channel3_product(test_session: Session):
    """
    Test 14: Pre-existing genuine Channel3 product with valid offers and 0 snapshots
    automatically records authentic baseline snapshots on first intelligence request.
    """
    product = create_live_channel3_product(test_session, "c3_preexisting_baseline")
    t_retrieved = datetime(2026, 11, 3, 7, 51, 30, tzinfo=timezone.utc)

    # 2 offers with identical price ($679.99), mimicking the screenshot issue
    o1 = RetailerOffer(
        product_id=product.id,
        retailer_name="Best Buy",
        price=679.99,
        currency="USD",
        product_url="https://bestbuy.com/vivobook",
        availability="IN_STOCK",
        provider_offer_id="bb_v1",
        retrieved_at=t_retrieved,
        is_synthetic=False,
    )
    o2 = RetailerOffer(
        product_id=product.id,
        retailer_name="Walmart",
        price=679.99,
        currency="USD",
        product_url="https://walmart.com/vivobook",
        availability="IN_STOCK",
        provider_offer_id="wal_v1",
        retrieved_at=t_retrieved,
        is_synthetic=False,
    )
    test_session.add_all([o1, o2])
    test_session.commit()

    # Pre-condition: zero snapshots exist in database
    assert ProductRepository.get_price_snapshots(test_session, product.id) == []

    # First intelligence call
    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert intel.total_genuine_observations == 2
    assert len(intel.price_history) == 2
    assert intel.tracking_start_date == t_retrieved

    # Verify both retailers are recorded in DB
    db_snaps = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(db_snaps) == 2
    retailers = {s.retailer_name for s in db_snaps}
    assert retailers == {"Best Buy", "Walmart"}
    for s in db_snaps:
        assert s.price == 679.99
        assert s.currency == "USD"
        s_dt = s.recorded_at.replace(tzinfo=timezone.utc) if s.recorded_at.tzinfo is None else s.recorded_at
        assert s_dt == t_retrieved
        assert s.is_synthetic is False

    # Verify retailer comparison handles identical pricing ($0 spread)
    assert len(intel.retailer_comparisons) == 1
    comp = intel.retailer_comparisons[0]
    assert comp.available_offers_count == 2
    assert comp.price_difference == 0.0
    assert comp.cheapest_price == 679.99
    assert comp.highest_price == 679.99


def test_baseline_snapshot_preserves_offer_retrieved_at_timestamp(test_session: Session):
    """
    Test 15: Baseline snapshot preserves original offer retrieval timestamp (not datetime.now()).
    """
    product = create_live_channel3_product(test_session, "c3_timestamp_preserve")
    historical_time = datetime(2026, 10, 15, 12, 30, 0, tzinfo=timezone.utc)

    offer = RetailerOffer(
        product_id=product.id,
        retailer_name="Target",
        price=249.99,
        currency="USD",
        product_url="https://target.com/item",
        availability="IN_STOCK",
        retrieved_at=historical_time,
        is_synthetic=False,
    )
    test_session.add(offer)
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert intel.total_genuine_observations == 1
    assert intel.tracking_start_date == historical_time
    assert intel.price_history[0].recorded_at == historical_time

    # Verify persisted snapshot in DB
    db_snaps = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(db_snaps) == 1
    db_dt = db_snaps[0].recorded_at.replace(tzinfo=timezone.utc) if db_snaps[0].recorded_at.tzinfo is None else db_snaps[0].recorded_at
    assert db_dt == historical_time


def test_baseline_snapshot_duplicate_prevention_on_repeated_requests(test_session: Session):
    """
    Test 16: Repeated get_product_intelligence() calls do NOT duplicate snapshots.
    """
    product = create_live_channel3_product(test_session, "c3_dedup_baseline")
    t_retrieved = datetime(2026, 11, 3, 7, 51, 30, tzinfo=timezone.utc)

    offer = RetailerOffer(
        product_id=product.id,
        retailer_name="B&H Photo",
        price=899.00,
        currency="USD",
        product_url="https://bhphotovideo.com/item",
        availability="IN_STOCK",
        retrieved_at=t_retrieved,
        is_synthetic=False,
    )
    test_session.add(offer)
    test_session.commit()

    # Call 1
    intel1 = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel1.total_genuine_observations == 1
    assert len(ProductRepository.get_price_snapshots(test_session, product.id)) == 1

    # Call 2
    intel2 = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel2.total_genuine_observations == 1
    assert len(ProductRepository.get_price_snapshots(test_session, product.id)) == 1

    # Call 3
    intel3 = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel3.total_genuine_observations == 1
    assert len(ProductRepository.get_price_snapshots(test_session, product.id)) == 1


def test_baseline_snapshot_synthetic_product_exclusion(test_session: Session):
    """
    Test 17: Synthetic products (is_synthetic=True) NEVER record baseline price snapshots.
    """
    synth_product = Product(
        name="Demo Synthetic Gadget",
        brand="DemoBrand",
        category="Electronics",
        provider="synthetic",
        provider_product_id="synth_demo_01",
        is_synthetic=True,
    )
    test_session.add(synth_product)
    test_session.commit()

    offer = RetailerOffer(
        product_id=synth_product.id,
        retailer_name="Demo Store",
        price=49.99,
        currency="USD",
        product_url="https://demostore.com/gadget",
        availability="IN_STOCK",
        is_synthetic=True,
    )
    test_session.add(offer)
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, synth_product.id)
    assert intel is not None
    assert intel.is_synthetic is True
    assert intel.total_genuine_observations == 0
    assert intel.price_history == []

    # Zero snapshots in database
    db_snaps = ProductRepository.get_price_snapshots(test_session, synth_product.id)
    assert len(db_snaps) == 0


def test_baseline_snapshot_invalid_offer_exclusion(test_session: Session):
    """
    Test 18: Invalid offers (zero, negative, non-numeric prices, synthetic offers) are excluded from snapshots.
    """
    product = create_live_channel3_product(test_session, "c3_invalid_offers_test")

    # Invalid offers: price=0, price=-50, is_synthetic=True
    o_zero = RetailerOffer(
        product_id=product.id,
        retailer_name="Zero Store",
        price=0.0,
        currency="USD",
        product_url="https://zerostore.com",
        availability="IN_STOCK",
        is_synthetic=False,
    )
    o_neg = RetailerOffer(
        product_id=product.id,
        retailer_name="Negative Store",
        price=-25.00,
        currency="USD",
        product_url="https://negstore.com",
        availability="IN_STOCK",
        is_synthetic=False,
    )
    o_synth = RetailerOffer(
        product_id=product.id,
        retailer_name="Synth Store",
        price=100.00,
        currency="USD",
        product_url="https://synthstore.com",
        availability="IN_STOCK",
        is_synthetic=True,
    )
    test_session.add_all([o_zero, o_neg, o_synth])
    test_session.commit()

    intel = ProductIntelligenceService.get_product_intelligence(test_session, product.id)
    assert intel is not None
    assert intel.total_genuine_observations == 0
    assert intel.price_history == []

    # Zero snapshots recorded
    db_snaps = ProductRepository.get_price_snapshots(test_session, product.id)
    assert len(db_snaps) == 0

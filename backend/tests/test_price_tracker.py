"""
BUYWISE AI — Automatic Price Tracking & Background Worker Tests (Phase 3D).
Verifies:
1. Worker startup, singleton idempotency, and graceful shutdown.
2. Tracking isolation: only genuine Channel3 products with existing price history are tracked.
3. Cooldown enforcement: products updated < 6 hours ago are skipped.
4. Eligible product refresh: products updated >= 6 hours ago are refreshed.
5. Batch limit enforcement: max 5 products per tracking cycle.
6. Failure resilience: Channel3 errors/429 preserve existing history without stopping the cycle.
7. Authentic periodic observations: unchanged prices record periodic snapshots after 6+ hours.
8. Deduplication: unchanged prices within 6 hours return existing snapshot.
9. Immediate change detection: price drops/rises record new snapshots immediately.
10. Synthetic exclusion: synthetic products are never eligible or tracked.
11. On-demand refresh: respects cooldown and concurrency guards.
12. API integration: GET /api/products/{id}/intelligence triggers non-blocking background refresh.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import select, func
import httpx

from backend.app.models.shopping import Product, RetailerOffer, PriceSnapshot
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.product_service import ProductService, _REFRESH_LOCK, _ACTIVE_REFRESHES
from backend.app.services.price_tracker import PriceTracker
from backend.app.services.channel3_client import Channel3RateLimitError, Channel3ServerError


def create_c3_product_with_history(
    db: Session,
    c3_id: str = "c3_track_test_1",
    name: str = "Bose QuietComfort 45",
    price: float = 279.00,
    updated_hours_ago: float = 7.0,
    with_snapshot: bool = True,
    is_synthetic: bool = False,
) -> Product:
    """Helper to create a Channel3 product with configurable update timestamp and price history."""
    t_updated = datetime.now(timezone.utc) - timedelta(hours=updated_hours_ago)

    prod = Product(
        name=name,
        brand="Bose",
        category="Audio",
        description="Premium ANC Headphones",
        provider="channel3" if not is_synthetic else "synthetic",
        provider_product_id=c3_id if not is_synthetic else None,
        is_synthetic=is_synthetic,
        created_at=t_updated,
        updated_at=t_updated,
    )
    db.add(prod)
    db.flush()

    offer = RetailerOffer(
        product_id=prod.id,
        retailer_name="Amazon",
        price=price,
        currency="USD",
        product_url="https://amazon.com/bose",
        availability="IN_STOCK",
        provider_offer_id=f"{c3_id}_off_1",
        retrieved_at=t_updated,
        created_at=t_updated,
        is_synthetic=is_synthetic,
    )
    db.add(offer)
    db.flush()

    if with_snapshot and not is_synthetic:
        snap = PriceSnapshot(
            product_id=prod.id,
            retailer_name="Amazon",
            provider_offer_id=f"{c3_id}_off_1",
            price=price,
            currency="USD",
            availability="IN_STOCK",
            recorded_at=t_updated,
            is_synthetic=False,
        )
        db.add(snap)

    db.commit()
    db.refresh(prod)
    return prod


@pytest.mark.asyncio
async def test_worker_start_and_graceful_stop():
    """Test 1: Worker starts cleanly and shuts down gracefully without leaked tasks."""
    # Ensure stopped before test
    await PriceTracker.stop()
    assert PriceTracker._worker_task is None

    # Start with short interval for test
    task = PriceTracker.start(interval_seconds=60.0)
    assert task is not None
    assert not task.done()
    assert PriceTracker._is_running is True

    # Graceful stop
    await PriceTracker.stop()
    assert PriceTracker._worker_task is None
    assert PriceTracker._is_running is False
    assert task.done()


@pytest.mark.asyncio
async def test_duplicate_worker_start_ignored():
    """Test 2: Starting an already running worker returns existing task and creates no duplicates."""
    await PriceTracker.stop()

    task1 = PriceTracker.start(interval_seconds=60.0)
    task2 = PriceTracker.start(interval_seconds=60.0)
    assert task1 is task2

    await PriceTracker.stop()


def test_only_tracked_channel3_products_included(test_session: Session):
    """Test 3: Only genuine Channel3 products with existing price snapshots are selected for tracking."""
    # 1. Genuine Channel3 product with snapshot -> Tracked!
    p_tracked = create_c3_product_with_history(
        test_session, "c3_prod_tracked", with_snapshot=True, is_synthetic=False
    )
    # 2. Genuine Channel3 product WITHOUT snapshot -> Not tracked!
    p_no_snap = create_c3_product_with_history(
        test_session, "c3_prod_no_snap", with_snapshot=False, is_synthetic=False
    )
    # 3. Synthetic product -> Not tracked!
    p_synth = create_c3_product_with_history(
        test_session, "c3_prod_synth", with_snapshot=False, is_synthetic=True
    )

    tracked_ids = PriceTracker.get_tracked_channel3_product_ids(test_session, limit=10)
    assert p_tracked.id in tracked_ids
    assert p_no_snap.id not in tracked_ids
    assert p_synth.id not in tracked_ids


def test_cooldown_skips_recently_updated_product(test_session: Session):
    """Test 4: Product updated within 6 hours is skipped due to active cooldown."""
    p_recent = create_c3_product_with_history(
        test_session, "c3_prod_recent", updated_hours_ago=1.5
    )
    assert PriceTracker.is_eligible_for_refresh(p_recent) is False

    p_old = create_c3_product_with_history(
        test_session, "c3_prod_old", updated_hours_ago=6.5
    )
    assert PriceTracker.is_eligible_for_refresh(p_old) is True


@pytest.mark.asyncio
async def test_eligible_product_is_refreshed(test_session: Session, test_engine):
    """Test 5: Product with expired cooldown is refreshed and its updated_at timestamp is bumped."""
    p_eligible = create_c3_product_with_history(
        test_session, "c3_refresh_eligible", price=299.00, updated_hours_ago=8.0
    )
    old_updated_at = p_eligible.updated_at

    # Mock Channel3 response returning updated price ($289.00)
    mock_client = MagicMock()
    mock_client.get_product_by_id.return_value = {
        "id": p_eligible.provider_product_id,
        "title": p_eligible.name,
        "brand": "Bose",
        "description": "Premium ANC Headphones",
        "price": 289.00,
        "currency": "USD",
        "url": "https://amazon.com/bose",
        "retailer": "Amazon",
        "availability": "IN_STOCK",
        "offers": [
            {
                "id": "c3_refresh_eligible_off_1",
                "domain": "Amazon",
                "price": {
                    "price": 289.00,
                    "currency": "USD",
                },
                "availability": "InStock",
                "url": "https://amazon.com/bose",
            }
        ],
    }

    testing_session_factory = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)

    results = await PriceTracker.run_tracking_cycle(
        batch_size=5,
        stagger_seconds=0.0,
        client=mock_client,
        session_factory=testing_session_factory,
    )

    assert results["processed"] >= 1
    assert results["refreshed"] >= 1

    # Verify updated_at bumped and new snapshot recorded
    test_session.commit()
    test_session.refresh(p_eligible)
    assert p_eligible.updated_at > old_updated_at

    snaps = ProductRepository.get_price_snapshots(test_session, p_eligible.id)
    assert len(snaps) == 2
    assert snaps[-1].price == 289.00


@pytest.mark.asyncio
async def test_batch_size_limit_respected(test_session: Session, test_engine):
    """Test 6: Periodic tracking cycle processes at most batch_size products."""
    for i in range(7):
        create_c3_product_with_history(
            test_session, f"c3_batch_{i}", updated_hours_ago=10.0 + i
        )

    mock_client = MagicMock()
    mock_client.get_product_by_id.return_value = None  # Provider reports none, won't crash

    testing_session_factory = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)

    results = await PriceTracker.run_tracking_cycle(
        batch_size=5,
        stagger_seconds=0.0,
        client=mock_client,
        session_factory=testing_session_factory,
    )

    # Processed must never exceed batch_size
    assert results["processed"] <= 5


@pytest.mark.asyncio
async def test_failed_refreshes_preserve_existing_history(test_session: Session, test_engine):
    """Test 7: Provider failure or HTTP 429 during refresh preserves existing snapshots and history."""
    prod = create_c3_product_with_history(
        test_session, "c3_fail_preserve", price=150.00, updated_hours_ago=7.0
    )
    initial_snaps = ProductRepository.get_price_snapshots(test_session, prod.id)
    assert len(initial_snaps) == 1

    # Mock Channel3 raising rate limit
    mock_client = MagicMock()
    mock_client.get_product_by_id.side_effect = Channel3RateLimitError("Rate limit exceeded", retry_after=60)

    testing_session_factory = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)

    results = await PriceTracker.run_tracking_cycle(
        batch_size=5,
        stagger_seconds=0.0,
        client=mock_client,
        session_factory=testing_session_factory,
    )

    # Cooldown rejected refresh due to error, existing history strictly preserved
    remaining_snaps = ProductRepository.get_price_snapshots(test_session, prod.id)
    assert len(remaining_snaps) == 1
    assert remaining_snaps[0].price == 150.00


def test_unchanged_prices_generate_periodic_observation_after_cooldown(test_session: Session):
    """Test 8: Unchanged prices record an authentic periodic snapshot if 6+ hours elapsed."""
    prod = create_c3_product_with_history(
        test_session, "c3_periodic_obs", price=199.99, updated_hours_ago=7.0
    )
    t_now = datetime.now(timezone.utc)

    # Re-record identical price offer after 7 hours
    snap = ProductRepository.record_price_snapshot(test_session, prod, {
        "retailer_name": "Amazon",
        "provider_offer_id": "c3_periodic_obs_off_1",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "retrieved_at": t_now,
    })
    test_session.commit()

    assert snap is not None
    snaps = ProductRepository.get_price_snapshots(test_session, prod.id)
    assert len(snaps) == 2
    assert snaps[0].price == 199.99
    assert snaps[1].price == 199.99
    assert snaps[1].id != snaps[0].id


def test_unchanged_prices_deduplicated_within_cooldown(test_session: Session):
    """Test 9: Unchanged prices within 6-hour interval return existing snapshot without duplicating."""
    prod = create_c3_product_with_history(
        test_session, "c3_dedup_check", price=199.99, updated_hours_ago=1.0
    )

    # Re-record identical price after only 1 hour
    snap = ProductRepository.record_price_snapshot(test_session, prod, {
        "retailer_name": "Amazon",
        "provider_offer_id": "c3_dedup_check_off_1",
        "price": 199.99,
        "currency": "USD",
        "availability": "IN_STOCK",
        "retrieved_at": datetime.now(timezone.utc),
    })
    test_session.commit()

    snaps = ProductRepository.get_price_snapshots(test_session, prod.id)
    assert len(snaps) == 1  # Deduplicated!
    assert snap.id == snaps[0].id


def test_changed_prices_recorded_immediately(test_session: Session):
    """Test 10: Changed prices are recorded immediately even if within 6 hours."""
    prod = create_c3_product_with_history(
        test_session, "c3_immediate_change", price=200.00, updated_hours_ago=0.5
    )

    # Price drops to $180.00 after only 30 minutes
    snap = ProductRepository.record_price_snapshot(test_session, prod, {
        "retailer_name": "Amazon",
        "provider_offer_id": "c3_immediate_change_off_1",
        "price": 180.00,
        "currency": "USD",
        "availability": "IN_STOCK",
        "retrieved_at": datetime.now(timezone.utc),
    })
    test_session.commit()

    snaps = ProductRepository.get_price_snapshots(test_session, prod.id)
    assert len(snaps) == 2
    assert snaps[1].price == 180.00


def test_synthetic_products_excluded_from_tracking(test_session: Session):
    """Test 11: Synthetic products are strictly excluded from tracking eligibility."""
    p_synth = create_c3_product_with_history(
        test_session, "c3_synth_test", is_synthetic=True, with_snapshot=False
    )
    assert PriceTracker.is_eligible_for_refresh(p_synth) is False

    updated, msg = PriceTracker.refresh_product_on_demand(p_synth.id, db=test_session)
    assert updated is False
    assert "not eligible" in msg.lower() or "synthetic" in msg.lower()


def test_on_demand_refresh_respects_cooldown(test_session: Session):
    """Test 12: On-demand refresh checks cooldown and rejects recently updated products."""
    p_recent = create_c3_product_with_history(
        test_session, "c3_ondemand_cooldown", updated_hours_ago=2.0
    )
    updated, msg = PriceTracker.refresh_product_on_demand(p_recent.id, db=test_session)
    assert updated is False
    assert "cooldown" in msg.lower() or "not eligible" in msg.lower()


def test_concurrent_refreshes_prevent_duplicate_work(test_session: Session):
    """Test 13: Concurrency mutex lock prevents duplicate concurrent refreshes for the same product."""
    p = create_c3_product_with_history(
        test_session, "c3_mutex_test", updated_hours_ago=8.0
    )

    # Simulate an active ongoing refresh
    with _REFRESH_LOCK:
        _ACTIVE_REFRESHES.add(p.id)

    try:
        updated, msg = PriceTracker.refresh_product_on_demand(p.id, db=test_session)
        assert updated is False
        assert "already in progress" in msg.lower()
    finally:
        with _REFRESH_LOCK:
            _ACTIVE_REFRESHES.discard(p.id)


@pytest.mark.asyncio
async def test_on_demand_refresh_via_api_endpoint(async_client: httpx.AsyncClient, test_session: Session):
    """Test 14: GET /api/products/{id}/intelligence responds immediately without blocking."""
    p = create_c3_product_with_history(
        test_session, "c3_api_ondemand", updated_hours_ago=7.0
    )

    res = await async_client.get(f"/api/products/{p.id}/intelligence")
    assert res.status_code == 200
    data = res.json()
    assert data["product_id"] == p.id
    assert data["total_genuine_observations"] >= 1


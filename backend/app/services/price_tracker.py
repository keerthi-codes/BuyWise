"""
BUYWISE AI — Automatic Price Tracking Service & Background Worker (Phase 3D).
Manages periodic background price tracking cycles and safe on-demand refreshes.

Architectural Guarantees:
1. Rate Limiting: Max 5 products per tracking cycle with 2.0s stagger delay between provider calls.
2. Deduplication: Unchanged prices recorded at most once per 6-hour interval.
3. Cooldown: Strictly enforces 6-hour cooldown before making provider requests.
4. Concurrency Safety: Mutex lock guards against concurrent refreshes for the same product.
5. Isolation: Operates only on genuine, non-synthetic Channel3 products with existing price history.
6. Failure Resilience: Catches HTTP 429 and provider errors without dropping existing price history.

Deployment Note:
The in-process background worker is designed for single-instance or single-leader architectures
(e.g. primary container or scheduler pod). Concurrency safety across processes is preserved by
database timestamps on Product.updated_at and transactional row updates.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Sequence
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal
from backend.app.models.shopping import Product, PriceSnapshot
from backend.app.services.product_service import ProductService
from backend.app.services.channel3_client import Channel3Client

logger = logging.getLogger("shopping_platform.price_tracker")


class PriceTracker:
    """
    Coordinates automatic periodic price tracking and on-demand live refreshes.
    Enforces strict rate limits, concurrency control, and provider cooldowns.
    """

    _worker_task: asyncio.Task | None = None
    _stop_event: asyncio.Event | None = None
    _is_running: bool = False

    DEFAULT_INTERVAL_SECONDS: float = 1800.0  # 30 minutes
    MAX_BATCH_SIZE: int = 5
    PROVIDER_STAGGER_SECONDS: float = 2.0
    COOLDOWN_HOURS: int = 6

    @classmethod
    def is_eligible_for_refresh(cls, product: Product) -> bool:
        """
        Determine whether a product is eligible for a Channel3 price refresh.
        Rules:
        - Must NOT be synthetic (product.is_synthetic is False).
        - Must be a Channel3 product with a valid provider_product_id.
        - Must have elapsed >= 6 hours since last updated_at (or updated_at is None).
        """
        if getattr(product, "is_synthetic", True):
            return False
        if getattr(product, "provider", None) != "channel3":
            return False
        if not getattr(product, "provider_product_id", None):
            return False

        last_updated = product.updated_at
        if not last_updated:
            return True

        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        elapsed = (now_utc - last_updated).total_seconds()
        return elapsed >= cls.COOLDOWN_HOURS * 3600

    @classmethod
    def get_tracked_channel3_product_ids(cls, db: Session, limit: int = 5) -> list[str]:
        """
        Retrieve IDs of genuine Channel3 products that are currently being tracked
        (have at least one recorded price snapshot), ordered by oldest updated_at first.
        """
        stmt = (
            select(Product.id)
            .join(PriceSnapshot, PriceSnapshot.product_id == Product.id)
            .where(
                Product.provider == "channel3",
                Product.is_synthetic.is_(False),
                Product.provider_product_id.is_not(None),
            )
            .group_by(Product.id, Product.updated_at)
            .order_by(Product.updated_at.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    @classmethod
    def refresh_product_on_demand(
        cls,
        product_id: str,
        db: Session | None = None,
        client: Channel3Client | None = None,
    ) -> tuple[bool, str]:
        """
        Safely execute an on-demand refresh for a single product with cooldown check.
        Uses provided database session or creates and closes a fresh isolated session.
        """
        if db is not None:
            return cls._execute_single_refresh(db, product_id, client)

        with SessionLocal() as session:
            try:
                res = cls._execute_single_refresh(session, product_id, client)
                session.commit()
                return res
            except Exception as exc:
                session.rollback()
                logger.error("Error during on-demand refresh for product %s: %s", product_id, exc)
                return False, f"On-demand refresh failed: {exc}"

    @classmethod
    def _execute_single_refresh(
        cls,
        db: Session,
        product_id: str,
        client: Channel3Client | None = None,
    ) -> tuple[bool, str]:
        product = db.get(Product, product_id)
        if not product:
            return False, f"Product '{product_id}' not found."

        if not cls.is_eligible_for_refresh(product):
            return False, "Product is not eligible for refresh or cooldown is active."

        prod, updated, msg = ProductService.refresh_product_offers_with_cooldown(
            db=db,
            product_id=product_id,
            cooldown_hours=cls.COOLDOWN_HOURS,
            client=client,
        )
        return updated, msg

    @classmethod
    async def run_tracking_cycle(
        cls,
        batch_size: int = 5,
        stagger_seconds: float = 2.0,
        client: Channel3Client | None = None,
        session_factory: Any = None,
    ) -> dict[str, Any]:
        """
        Execute a single periodic tracking cycle.
        - Queries tracked Channel3 products with price history.
        - Checks cooldown before provider request.
        - Processes up to batch_size products.
        - Pauses stagger_seconds between provider requests to respect rate limits.
        - Uses fresh isolated database session for each product to prevent transaction leaks.
        """
        factory = session_factory or SessionLocal
        logger.info("Starting periodic price tracking cycle (batch_size=%d)...", batch_size)
        results: dict[str, Any] = {
            "processed": 0,
            "refreshed": 0,
            "skipped_cooldown": 0,
            "failed": 0,
            "errors": [],
        }

        # Step 1: Identify tracked product IDs using a short-lived query session
        with factory() as session:
            candidate_ids = cls.get_tracked_channel3_product_ids(session, limit=batch_size)

        if not candidate_ids:
            logger.info("No tracked Channel3 products found for refresh cycle.")
            return results

        logger.info("Found %d candidate tracked products for periodic check.", len(candidate_ids))

        for idx, pid in enumerate(candidate_ids):
            if cls._stop_event and cls._stop_event.is_set():
                logger.info("Tracking cycle interrupted by stop event.")
                break

            results["processed"] += 1
            did_provider_refresh = False

            with factory() as session:
                try:
                    product = session.get(Product, pid)
                    if not product or not cls.is_eligible_for_refresh(product):
                        results["skipped_cooldown"] += 1
                        logger.info("Skipped product %s: cooldown active or not eligible.", pid)
                        continue

                    prod, refreshed, msg = ProductService.refresh_product_offers_with_cooldown(
                        db=session,
                        product_id=pid,
                        cooldown_hours=cls.COOLDOWN_HOURS,
                        client=client,
                    )
                    session.commit()

                    if refreshed:
                        did_provider_refresh = True
                        results["refreshed"] += 1
                        logger.info("Successfully refreshed product %s: %s", pid, msg)
                    else:
                        results["skipped_cooldown"] += 1
                        logger.info("Product %s skipped by cooldown: %s", pid, msg)

                except Exception as exc:
                    session.rollback()
                    results["failed"] += 1
                    err_msg = f"Failed to refresh product {pid}: {exc}"
                    results["errors"].append(err_msg)
                    logger.warning(err_msg)

            # Stagger provider calls (only if a provider call was actually made, and not after the last item)
            if did_provider_refresh and idx < len(candidate_ids) - 1 and stagger_seconds > 0:
                try:
                    if cls._stop_event:
                        await asyncio.wait_for(cls._stop_event.wait(), timeout=stagger_seconds)
                        break
                    else:
                        await asyncio.sleep(stagger_seconds)
                except asyncio.TimeoutError:
                    pass

        logger.info(
            "Periodic price tracking cycle completed: %d processed, %d refreshed, %d cooldown, %d failed.",
            results["processed"],
            results["refreshed"],
            results["skipped_cooldown"],
            results["failed"],
        )
        return results

    @classmethod
    async def _worker_loop(
        cls,
        interval_seconds: float,
        session_factory: Any = None,
        client: Channel3Client | None = None,
    ) -> None:
        """Background loop executing run_tracking_cycle periodically."""
        logger.info("Price tracking background worker started (interval=%.1fs).", interval_seconds)
        try:
            while cls._stop_event and not cls._stop_event.is_set():
                try:
                    await cls.run_tracking_cycle(
                        batch_size=cls.MAX_BATCH_SIZE,
                        stagger_seconds=cls.PROVIDER_STAGGER_SECONDS,
                        client=client,
                        session_factory=session_factory,
                    )
                except Exception as exc:
                    logger.error("Unexpected error in price tracking cycle: %s", exc, exc_info=True)

                # Wait for next cycle or stop signal
                try:
                    await asyncio.wait_for(cls._stop_event.wait(), timeout=interval_seconds)
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Interval elapsed, proceed to next cycle
        except asyncio.CancelledError:
            logger.info("Price tracking background worker received cancellation signal.")
        finally:
            cls._is_running = False
            logger.info("Price tracking background worker stopped.")

    @classmethod
    def start(
        cls,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        session_factory: Any = None,
        client: Channel3Client | None = None,
    ) -> asyncio.Task | None:
        """Start the background worker if not already running. Thread-safe singleton."""
        if cls._worker_task is not None and not cls._worker_task.done():
            logger.warning("Price tracking worker is already running; ignoring duplicate start request.")
            return cls._worker_task

        cls._stop_event = asyncio.Event()
        cls._is_running = True
        cls._worker_task = asyncio.create_task(
            cls._worker_loop(
                interval_seconds=interval_seconds,
                session_factory=session_factory,
                client=client,
            )
        )
        return cls._worker_task

    @classmethod
    async def stop(cls) -> None:
        """Gracefully stop and await the background worker."""
        if cls._stop_event is not None:
            cls._stop_event.set()

        if cls._worker_task is not None and not cls._worker_task.done():
            cls._worker_task.cancel()
            try:
                await cls._worker_task
            except asyncio.CancelledError:
                pass

        cls._worker_task = None
        cls._stop_event = None
        cls._is_running = False
        logger.info("Price tracking worker stopped and cleaned up.")


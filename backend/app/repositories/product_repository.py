"""
AI-Powered Multilingual Shopping Intelligence Platform
Product & Retailer Offer Repository.
Encapsulates all database query logic for catalog searching, filtering, and comparisons.
"""

from datetime import datetime, timezone
from typing import Sequence, Any
from collections import defaultdict
import math
import random
from sqlalchemy import select, func, or_, delete
from sqlalchemy.orm import Session, joinedload
from backend.app.models.shopping import Product, RetailerOffer, PriceSnapshot
from backend.app.services.channel3_normalizer import NormalizedProduct


class ProductRepository:
    """Repository handling Product and RetailerOffer persistence and queries."""

    @staticmethod
    def get_by_id(db: Session, product_id: str) -> Product | None:
        """Fetch a single product by ID including all associated retailer offers."""
        stmt = (
            select(Product)
            .options(joinedload(Product.offers))
            .where(Product.id == product_id)
        )
        return db.scalars(stmt).unique().first()

    @staticmethod
    def get_by_provider_id(db: Session, provider: str, provider_product_id: str) -> Product | None:
        """Fetch a single product by provider and provider-assigned identifier."""
        stmt = (
            select(Product)
            .options(joinedload(Product.offers))
            .where(Product.provider == provider, Product.provider_product_id == provider_product_id)
        )
        return db.scalars(stmt).unique().first()

    @staticmethod
    def get_by_ids(db: Session, product_ids: Sequence[str]) -> list[Product]:
        """Fetch multiple products by IDs including their retailer offers."""
        if not product_ids:
            return []
        stmt = (
            select(Product)
            .options(joinedload(Product.offers))
            .where(Product.id.in_(product_ids))
        )
        return list(db.scalars(stmt).unique().all())

    @staticmethod
    def _build_category_clause(category: str):
        """
        Builds a comprehensive category filtering clause.
        Supports both exact provider category matching (e.g. 'Accessories', 'Home Appliances', 'Laptops')
        and high-level taxonomy categories (Audio, Wearables, Computing, Smart Home, Gaming, Electronics)
        mapping to authentic merchant category variants and product characteristics.
        """
        cat_clean = category.strip().lower()
        if cat_clean == "audio":
            return or_(
                Product.category.ilike("%audio%"),
                Product.category.ilike("%headphone%"),
                Product.category.ilike("%earbud%"),
                Product.category.ilike("%speaker%"),
                Product.name.ilike("%headphone%"),
                Product.name.ilike("%earbud%"),
                Product.name.ilike("%wh-1000xm%"),
                Product.name.ilike("%quietcomfort%"),
            )
        elif cat_clean == "wearables":
            return or_(
                Product.category.ilike("%wearable%"),
                Product.category.ilike("%smartwatch%"),
                Product.category.ilike("%fitness tracker%"),
                Product.category.ilike("%watches%"),
                Product.name.ilike("%watch%"),
                Product.name.ilike("%smartwatch%"),
                Product.name.ilike("%fitness tracker%"),
            )
        elif cat_clean == "computing":
            return or_(
                Product.category.ilike("%computing%"),
                Product.category.ilike("%laptop%"),
                Product.category.ilike("%keyboard%"),
                Product.category.ilike("%computer%"),
                Product.category.ilike("%pc%"),
                Product.name.ilike("%laptop%"),
                Product.name.ilike("%macbook%"),
                Product.name.ilike("%chromebook%"),
                Product.name.ilike("%keyboard%"),
                Product.name.ilike("%ipad%"),
                Product.name.ilike("%xps 15%"),
                Product.name.ilike("%mx master%"),
            )
        elif cat_clean == "smart home":
            return or_(
                Product.category.ilike("%smart home%"),
                Product.category.ilike("%home appliance%"),
                Product.category.ilike("%appliances%"),
                Product.name.ilike("%philips hue%"),
                Product.name.ilike("%roomba%"),
                Product.name.ilike("%vacuum%"),
                Product.name.ilike("%air fryer%"),
                Product.name.ilike("%espresso%"),
                Product.name.ilike("%barista%"),
                Product.name.ilike("%stand mixer%"),
                Product.name.ilike("%dyson%"),
            )
        elif cat_clean == "gaming":
            return or_(
                Product.category.ilike("%gaming%"),
                Product.name.ilike("%gaming%"),
                Product.name.ilike("%switch oled%"),
                Product.name.ilike("%nintendo%"),
                Product.name.ilike("%playstation%"),
                Product.name.ilike("%xbox%"),
            )
        else:
            return Product.category.ilike(f"%{category.strip()}%")

    @staticmethod
    def list_products(
        db: Session,
        query: str | None = None,
        category: str | None = None,
        brand: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        source: str | None = None,
        order_by: str | None = None,
        seed: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Product], int]:
        """
        Search and filter products with pagination and dynamic discovery ordering.
        Supports fuzzy name/description matching, category/brand equality, and price range filtering.
        Optionally filters by source: 'synthetic', 'live' / 'channel3', or all.
        Supports order_by:
          - 'random': dynamic randomized discovery with category and brand stratification and optional seed.
          - 'price_asc': sort by lowest offer price ascending.
          - 'price_desc': sort by lowest offer price descending.
          - 'name' or None: alphabetical ascending by name (preserves backward compatibility).
        """
        stmt = select(Product).options(joinedload(Product.offers))
        count_stmt = select(func.count(Product.id))

        filters = []
        if query:
            clean_q = f"%{query.strip()}%"
            filters.append(
                or_(
                    Product.name.ilike(clean_q),
                    Product.description.ilike(clean_q),
                    Product.brand.ilike(clean_q),
                )
            )

        if category and category.strip().lower() != "all":
            filters.append(ProductRepository._build_category_clause(category))

        if brand:
            filters.append(Product.brand.ilike(brand.strip()))

        if min_price is not None:
            filters.append(Product.offers.any(RetailerOffer.price >= min_price))

        if max_price is not None:
            filters.append(Product.offers.any(RetailerOffer.price <= max_price))

        if source:
            source_lower = source.strip().lower()
            if source_lower == "synthetic":
                filters.append(Product.is_synthetic == True)
            elif source_lower in ("live", "channel3"):
                filters.append(Product.is_synthetic == False)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        # Total count before pagination
        total = db.scalar(count_stmt) or 0
        if total == 0:
            return [], 0

        offset = max(0, (page - 1) * page_size)

        if order_by == "random":
            # Retrieve all matching candidate products with offers
            all_products = list(db.scalars(stmt).unique().all())
            if not all_products:
                return [], 0

            # Deterministic or fresh PRNG based on seed
            rng = random.Random(seed) if seed is not None else random.Random()

            # Stratify by category to promote varied category representation
            category_buckets: dict[str, list[Product]] = defaultdict(list)
            for p in all_products:
                cat_key = (p.category or "Other").strip()
                category_buckets[cat_key].append(p)

            # Shuffle items inside each category bucket
            for cat_key in category_buckets:
                rng.shuffle(category_buckets[cat_key])

            # Randomize category rotation order
            bucket_keys = list(category_buckets.keys())
            rng.shuffle(bucket_keys)

            # Round-robin interleave across categories
            interleaved: list[Product] = []
            max_depth = max(len(b) for b in category_buckets.values())
            for depth in range(max_depth):
                for cat in bucket_keys:
                    if depth < len(category_buckets[cat]):
                        interleaved.append(category_buckets[cat][depth])

            # Secondary brand diversification: swap adjacent same-brand items if alternative brand exists
            for i in range(len(interleaved) - 1):
                if interleaved[i].brand and interleaved[i].brand == interleaved[i + 1].brand:
                    for j in range(i + 2, min(i + 6, len(interleaved))):
                        if interleaved[j].brand != interleaved[i].brand:
                            interleaved[i + 1], interleaved[j] = interleaved[j], interleaved[i + 1]
                            break

            paged_products = interleaved[offset : offset + page_size]
            return paged_products, total

        elif order_by == "price_asc":
            all_products = list(db.scalars(stmt).unique().all())
            all_products.sort(
                key=lambda p: (
                    p.lowest_price() if p.lowest_price() is not None else float("inf"),
                    p.name,
                )
            )
            paged_products = all_products[offset : offset + page_size]
            return paged_products, total

        elif order_by == "price_desc":
            all_products = list(db.scalars(stmt).unique().all())
            all_products.sort(
                key=lambda p: (
                    -(p.lowest_price() if p.lowest_price() is not None else -float("inf")),
                    p.name,
                )
            )
            paged_products = all_products[offset : offset + page_size]
            return paged_products, total

        else:
            # Default / name ordering: strictly maintain SQL order_by(Product.name.asc())
            stmt = stmt.order_by(Product.name.asc()).offset(offset).limit(page_size)
            products = list(db.scalars(stmt).unique().all())
            return products, total

    @staticmethod
    def upsert_channel3_product(db: Session, norm: NormalizedProduct) -> Product:
        """
        Persist or update a normalized Channel3 product and its competing offers.
        Prevents duplicate entries on repeated searches and refreshes offer prices/freshness.
        """
        existing = ProductRepository.get_by_provider_id(
            db, provider="channel3", provider_product_id=norm.provider_product_id
        )

        now_utc = datetime.now(timezone.utc)

        if existing:
            # Update product details
            existing.name = norm.name
            existing.brand = norm.brand
            existing.category = norm.category
            existing.description = norm.description
            existing.specifications = norm.specifications
            if norm.image_url:
                existing.image_url = norm.image_url
            existing.updated_at = now_utc

            # Refresh retailer offers: clear existing and re-attach fresh offers
            existing.offers.clear()
            for offer in norm.offers:
                ro = RetailerOffer(
                    product_id=existing.id,
                    retailer_name=offer.retailer_name,
                    price=offer.price,
                    currency=offer.currency,
                    product_url=offer.product_url,
                    availability=offer.availability,
                    compare_at_price=offer.compare_at_price,
                    provider_offer_id=offer.provider_offer_id,
                    is_synthetic=False,
                    retrieved_at=offer.retrieved_at or now_utc,
                )
                existing.offers.append(ro)

            if not norm.is_synthetic and existing.provider == "channel3":
                ProductRepository.record_snapshots_for_product(db, existing, norm.offers)
            db.commit()
            db.refresh(existing)
            return existing

        # Create new product
        new_prod = Product(
            name=norm.name,
            brand=norm.brand,
            category=norm.category,
            description=norm.description,
            specifications=norm.specifications,
            image_url=norm.image_url,
            provider="channel3",
            provider_product_id=norm.provider_product_id,
            is_synthetic=False,
            created_at=now_utc,
            updated_at=now_utc,
        )

        for offer in norm.offers:
            ro = RetailerOffer(
                retailer_name=offer.retailer_name,
                price=offer.price,
                currency=offer.currency,
                product_url=offer.product_url,
                availability=offer.availability,
                compare_at_price=offer.compare_at_price,
                provider_offer_id=offer.provider_offer_id,
                is_synthetic=False,
                retrieved_at=offer.retrieved_at or now_utc,
            )
            new_prod.offers.append(ro)

        db.add(new_prod)
        db.flush()
        if not norm.is_synthetic and new_prod.provider == "channel3":
            ProductRepository.record_snapshots_for_product(db, new_prod, norm.offers)
        db.commit()
        db.refresh(new_prod)
        return new_prod

    @staticmethod
    def create(db: Session, product: Product) -> Product:
        """Persist a new product record."""
        db.add(product)
        db.commit()
        db.refresh(product)
        return product

    @staticmethod
    def count(db: Session) -> int:
        """Return total count of products in the catalog."""
        return db.scalar(select(func.count(Product.id))) or 0

    @staticmethod
    def is_valid_price(price: Any) -> bool:
        """Verify that a price is a non-null, finite, positive numeric value."""
        if price is None:
            return False
        if not isinstance(price, (int, float)):
            return False
        if not math.isfinite(price):
            return False
        return price > 0

    @staticmethod
    def record_price_snapshot(db: Session, product: Product, offer_data: Any) -> PriceSnapshot | None:
        """
        Record an authentic point-in-time price snapshot for a product offer.
        Enforces strict data integrity:
        - Rejects synthetic products and offers (is_synthetic=True).
        - Rejects invalid, non-finite, zero, or negative prices.
        - Avoids duplicate snapshots when price, currency, retailer, and offer identity are unchanged.
        - Records new snapshot when price changes or for baseline first observation.
        """
        if product.is_synthetic:
            return None

        # Extract offer fields whether NormalizedOffer, RetailerOffer, or dict
        if isinstance(offer_data, dict):
            retailer_name = offer_data.get("retailer_name")
            price = offer_data.get("price")
            currency = (offer_data.get("currency") or "USD").strip().upper()
            availability = offer_data.get("availability") or "IN_STOCK"
            provider_offer_id = offer_data.get("provider_offer_id")
            is_synth = offer_data.get("is_synthetic", False)
            recorded_at = offer_data.get("retrieved_at") or offer_data.get("created_at") or datetime.now(timezone.utc)
        else:
            retailer_name = getattr(offer_data, "retailer_name", None)
            price = getattr(offer_data, "price", None)
            currency = (getattr(offer_data, "currency", "USD") or "USD").strip().upper()
            availability = getattr(offer_data, "availability", "IN_STOCK") or "IN_STOCK"
            provider_offer_id = getattr(offer_data, "provider_offer_id", None)
            is_synth = getattr(offer_data, "is_synthetic", False)
            recorded_at = getattr(offer_data, "retrieved_at", None) or getattr(offer_data, "created_at", None) or datetime.now(timezone.utc)

        if is_synth:
            return None

        if not retailer_name or not str(retailer_name).strip():
            return None

        if not ProductRepository.is_valid_price(price):
            return None

        float_price = round(float(price), 2)
        clean_retailer = str(retailer_name).strip()

        # Query latest recorded snapshot for this specific product, retailer, and currency
        stmt = (
            select(PriceSnapshot)
            .where(
                PriceSnapshot.product_id == product.id,
                PriceSnapshot.retailer_name == clean_retailer,
                PriceSnapshot.currency == currency,
            )
        )
        if provider_offer_id:
            stmt = stmt.where(
                or_(
                    PriceSnapshot.provider_offer_id == provider_offer_id,
                    PriceSnapshot.provider_offer_id.is_(None),
                )
            )

        stmt = stmt.order_by(PriceSnapshot.recorded_at.desc()).limit(1)
        latest = db.scalars(stmt).first()

        # If identical snapshot exists with same price and availability, check 6-hour periodic interval
        if latest is not None:
            if abs(latest.price - float_price) < 1e-4 and latest.availability == availability:
                if provider_offer_id is None or latest.provider_offer_id == provider_offer_id:
                    # Enforce periodic observation interval:
                    # At most one unchanged-price observation per 6-hour interval.
                    latest_dt = (
                        latest.recorded_at.replace(tzinfo=timezone.utc)
                        if latest.recorded_at.tzinfo is None
                        else latest.recorded_at
                    )
                    current_dt = (
                        recorded_at.replace(tzinfo=timezone.utc)
                        if recorded_at.tzinfo is None
                        else recorded_at
                    )
                    elapsed_seconds = (current_dt - latest_dt).total_seconds()
                    if elapsed_seconds < 6 * 3600:
                        return latest
                    # 6+ hours elapsed: fall through to record authentic periodic observation

        # Price changed, availability changed, or 6+ hours elapsed since last unchanged observation: create new persistent snapshot
        snapshot = PriceSnapshot(
            product_id=product.id,
            retailer_name=clean_retailer,
            provider_offer_id=provider_offer_id,
            price=float_price,
            currency=currency,
            availability=availability,
            recorded_at=recorded_at,
            is_synthetic=False,
        )
        db.add(snapshot)
        return snapshot

    @staticmethod
    def record_snapshots_for_product(db: Session, product: Product, offers: Sequence[Any]) -> list[PriceSnapshot]:
        """Record price snapshots for all eligible offers of a live product."""
        if product.is_synthetic:
            return []

        recorded: list[PriceSnapshot] = []
        for o in offers:
            snap = ProductRepository.record_price_snapshot(db, product, o)
            if snap:
                recorded.append(snap)
        return recorded

    @staticmethod
    def get_price_snapshots(
        db: Session,
        product_id: str,
        currency: str | None = None,
        retailer_name: str | None = None,
    ) -> list[PriceSnapshot]:
        """Retrieve historical price snapshots for a product ordered chronologically."""
        stmt = (
            select(PriceSnapshot)
            .where(PriceSnapshot.product_id == product_id)
        )
        if currency:
            stmt = stmt.where(PriceSnapshot.currency == currency.strip().upper())
        if retailer_name:
            stmt = stmt.where(PriceSnapshot.retailer_name == retailer_name.strip())

        stmt = stmt.order_by(PriceSnapshot.recorded_at.asc())
        return list(db.scalars(stmt).all())


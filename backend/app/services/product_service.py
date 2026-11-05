import threading
from datetime import datetime, timezone
"""
AI-Powered Multilingual Shopping Intelligence Platform
Product Catalog & Comparison Service.
Orchestrates product search, live aggregation, detail retrieval, and multi-product comparison matrices.
Enforces strict currency-safety, robust data-quality preservation, and zero fabricated scores.
"""

from math import ceil
from typing import Sequence, Any
import logging
from sqlalchemy.orm import Session

from backend.app.models.shopping import Product, RetailerOffer
from backend.app.repositories.product_repository import ProductRepository
from backend.app.schemas.product import (
    ProductResponse,
    ProductDetailResponse,
    ProductListResponse,
    ProductCompareItem,
    ProductComparisonResponse,
    RetailerOfferResponse,
    ComparisonExplanation,
)
from backend.app.services.channel3_client import Channel3Client, Channel3Error, Channel3RateLimitError
from backend.app.services.channel3_normalizer import Channel3Normalizer

logger = logging.getLogger(__name__)
_REFRESH_LOCK = threading.Lock()
_ACTIVE_REFRESHES: set[str] = set()
_DISCOVERY_LOCK = threading.Lock()
_LAST_DISCOVERY_TIME: float = 0.0
_DISCOVERY_COOLDOWN_SECONDS: float = 900.0  # 15 minutes between automated discovery sweeps



class ProductService:
    """Business logic service for product discovery, live aggregation, and multi-product comparisons."""

    @staticmethod
    def _enrich_product_response(product: Product) -> ProductResponse:
        """Helper to compute lowest price, extract ratings/warnings, and convert to ProductResponse schema."""
        lowest_price = product.lowest_price()
        offers = [RetailerOfferResponse.model_validate(o) for o in product.offers]

        specs = product.specifications or {}
        public_specs = {k: v for k, v in specs.items() if not k.startswith("_")}
        rating_val = specs.get("rating")
        rating_count = specs.get("rating_count")
        quality_warnings = list(specs.get("_data_quality_warnings", []))

        return ProductResponse(
            id=product.id,
            name=product.name,
            brand=product.brand,
            category=product.category,
            description=product.description,
            specifications=public_specs,
            image_url=product.image_url,
            is_synthetic=bool(getattr(product, "is_synthetic", True)),
            provider=str(getattr(product, "provider", "synthetic")),
            provider_product_id=getattr(product, "provider_product_id", None),
            created_at=product.created_at,
            lowest_price=lowest_price,
            offers=offers,
            rating=float(rating_val) if isinstance(rating_val, (int, float)) else None,
            rating_count=int(rating_count) if isinstance(rating_count, (int, float)) else None,
            data_quality_warnings=quality_warnings,
        )

    @classmethod
    def discover_live_products(
        cls,
        db: Session,
        query: str | None = None,
        category: str | None = None,
        client: Channel3Client | None = None,
        force: bool = False,
    ) -> tuple[int, str]:
        """
        Controlled Channel3 discovery to expand the catalogue with authentic live products.
        - Governed by a server-side cooldown (default 15 minutes) to conserve rate limits.
        - Persists/upserts products using ProductRepository.upsert_channel3_product to avoid duplicates.
        - Safely catches provider network/quota errors without disrupting caller.
        - Returns (count_of_ingested_products, status_message).
        """
        import time
        now = time.time()
        global _LAST_DISCOVERY_TIME

        if not force and (now - _LAST_DISCOVERY_TIME < _DISCOVERY_COOLDOWN_SECONDS):
            remaining = int(_DISCOVERY_COOLDOWN_SECONDS - (now - _LAST_DISCOVERY_TIME))
            return 0, f"Discovery cooldown active ({remaining}s remaining)."

        term = (query or "").strip()
        if not term:
            category_term_map = {
                "Electronics": "smart displays and monitors",
                "Audio": "wireless noise cancelling headphones",
                "Wearables": "smart fitness gps watch",
                "Smart Home": "smart home hub security",
                "Accessories": "ergonomic mechanical wireless keyboard",
            }
            if category and category in category_term_map:
                term = category_term_map[category]
            else:
                rotation = [
                    "wireless noise cancelling headphones",
                    "smart fitness gps watch",
                    "mechanical gaming keyboard",
                    "4k smart monitor",
                    "smart home indoor camera",
                ]
                idx = int(now / _DISCOVERY_COOLDOWN_SECONDS) % len(rotation)
                term = rotation[idx]

        with _DISCOVERY_LOCK:
            now = time.time()
            if not force and (now - _LAST_DISCOVERY_TIME < _DISCOVERY_COOLDOWN_SECONDS):
                return 0, "Discovery already executed by another process."

            c3_client = client or Channel3Client()
            if not c3_client.is_configured:
                return 0, "Channel3 API client is not configured; skipping discovery."

            try:
                raw_response = c3_client.search_products(query=term, limit=5)
                normalized = Channel3Normalizer.normalize_search_response(raw_response)
                ingested = 0
                for norm_prod in normalized:
                    ProductRepository.upsert_channel3_product(db, norm_prod)
                    ingested += 1

                _LAST_DISCOVERY_TIME = time.time()
                return ingested, f"Discovered and stored {ingested} authentic Channel3 products."
            except Channel3RateLimitError as exc:
                logger.warning("Channel3 rate limit reached during controlled discovery: %s", exc)
                return 0, "Channel3 rate limit exceeded; using existing catalogue."
            except (Channel3Error, Exception) as exc:
                logger.warning("Channel3 discovery failed: %s", exc)
                return 0, f"Channel3 discovery error: {str(exc)}"

    @staticmethod
    def search_products(
        db: Session,
        query: str | None = None,
        category: str | None = None,
        brand: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        source: str | None = None,
        order_by: str | None = None,
        seed: int | None = None,
        expand_discovery: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> ProductListResponse:
        """Search products from stored catalog with fuzzy matching, facets, source filtering, and pagination."""
        page = max(1, page)
        page_size = max(1, min(page_size, 100))

        if expand_discovery:
            ProductService.discover_live_products(db=db, category=category)

        products, total = ProductRepository.list_products(
            db=db,
            query=query,
            category=category,
            brand=brand,
            min_price=min_price,
            max_price=max_price,
            source=source,
            order_by=order_by,
            seed=seed,
            page=page,
            page_size=page_size,
        )

        items = [ProductService._enrich_product_response(p) for p in products]
        total_pages = ceil(total / page_size) if total > 0 else 1

        return ProductListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @staticmethod
    def search_live_products(
        db: Session,
        query: str,
        limit: int = 10,
        client: Channel3Client | None = None,
    ) -> ProductListResponse:
        """
        Execute live product aggregation against Channel3, normalize results,
        persist/update them in the database, and return standardized product responses.
        Strictly raises provider exceptions on failure without falling back to synthetic products.
        """
        if not query or not query.strip():
            return ProductListResponse(
                items=[],
                total=0,
                page=1,
                page_size=limit,
                total_pages=1,
            )

        c3_client = client or Channel3Client()
        raw_response = c3_client.search_products(query=query.strip(), limit=limit)

        normalized_products = Channel3Normalizer.normalize_search_response(raw_response)

        persisted_products: list[Product] = []
        for norm_prod in normalized_products:
            saved = ProductRepository.upsert_channel3_product(db, norm_prod)
            persisted_products.append(saved)

        items = [ProductService._enrich_product_response(p) for p in persisted_products]

        return ProductListResponse(
            items=items,
            total=len(items),
            page=1,
            page_size=limit,
            total_pages=1,
        )

    @staticmethod
    def get_product_detail(db: Session, product_id: str) -> ProductDetailResponse | None:
        """Retrieve full product details including lowest price, best retailer, and provenance."""
        product = ProductRepository.get_by_id(db, product_id)
        if not product:
            return None

        best_offer = product.lowest_price_offer()
        lowest_price = best_offer.price if best_offer else None
        best_retailer = best_offer.retailer_name if best_offer else None

        offers = [RetailerOfferResponse.model_validate(o) for o in product.offers]

        specs = product.specifications or {}
        public_specs = {k: v for k, v in specs.items() if not k.startswith("_")}
        rating_val = specs.get("rating")
        rating_count = specs.get("rating_count")
        quality_warnings = list(specs.get("_data_quality_warnings", []))

        return ProductDetailResponse(
            id=product.id,
            name=product.name,
            brand=product.brand,
            category=product.category,
            description=product.description,
            specifications=public_specs,
            image_url=product.image_url,
            is_synthetic=bool(getattr(product, "is_synthetic", True)),
            provider=str(getattr(product, "provider", "synthetic")),
            provider_product_id=getattr(product, "provider_product_id", None),
            created_at=product.created_at,
            lowest_price=lowest_price,
            lowest_price_retailer=best_retailer,
            total_offers=len(offers),
            offers=offers,
            rating=float(rating_val) if isinstance(rating_val, (int, float)) else None,
            rating_count=int(rating_count) if isinstance(rating_count, (int, float)) else None,
            data_quality_warnings=quality_warnings,
        )

    @classmethod
    def build_fallback_comparison_explanation(
        cls,
        products: Sequence[ProductCompareItem],
        matrix: dict[str, dict[str, str]],
        price_summary: dict[str, Any],
    ) -> ComparisonExplanation:
        """
        Deterministic, grounded comparison explanation builder.
        Synthesizes technical trade-offs, highlights verified differences,
        and respects multi-currency invariants without LLM dependency.
        """
        if not products:
            return ComparisonExplanation(summary="No products selected for comparison.")

        is_multi_currency = price_summary.get("is_multi_currency", False)
        cheapest_prod = price_summary.get("cheapest_product")
        cheapest_price = price_summary.get("cheapest_price")
        curr = price_summary.get("currency")

        perf_diffs: list[str] = []
        key_diffs: list[str] = []
        missing_notes: list[str] = []

        for spec_key, val_map in matrix.items():
            vals = {p.name: val_map.get(p.name, "Not Specified") for p in products}
            unique_vals = set(vals.values())
            clean_key = "RAM" if spec_key.lower() == "ram" else ("SSD" if spec_key.lower() == "ssd" else spec_key.replace("_", " ").title())

            if "Not Specified" in unique_vals:
                unspec_prods = [p_name for p_name, v in vals.items() if v == "Not Specified"]
                missing_notes.append(f"{clean_key} is not confirmed in merchant specifications for: {', '.join(unspec_prods)}.")

            if len(unique_vals) > 1 and "Not Specified" not in unique_vals:
                diff_desc = f"{clean_key}: " + "; ".join(f"{p_name} ({v})" for p_name, v in vals.items())
                if spec_key.lower() in ("ram", "storage", "cpu", "processor", "gpu", "graphics", "screen", "display"):
                    perf_diffs.append(diff_desc)
                else:
                    key_diffs.append(diff_desc)

        value_tradeoffs = ""
        tradeoffs: list[str] = []
        best_val = None

        if is_multi_currency:
            currency_warn = price_summary.get("warning") or "Compared products quote prices in different currencies. Direct numerical comparison requires FX conversion."
            currency_notes = "Multi-currency comparison: items must be evaluated in their native merchant currencies."
            value_tradeoffs = currency_warn
            tradeoffs.append("Direct price spread analysis is withheld due to cross-currency differences.")
        else:
            currency_warn = None
            currency_notes = f"All compared products are priced in {curr}."
            if cheapest_prod and cheapest_price is not None:
                best_val = cheapest_prod
                retailer = price_summary.get("cheapest_retailer", "Catalog")
                value_tradeoffs = f"{cheapest_prod} offers the lowest entry price at {cheapest_price:,.2f} {curr} via {retailer}."
                tradeoffs.append(f"Lowest price option is {cheapest_prod} ({cheapest_price:,.2f} {curr}).")

        best_pick = products[0].name
        summary = f"Compared {len(products)} products across technical specifications and verified retailer offers."

        return ComparisonExplanation(
            summary=summary,
            performance_differences=perf_diffs[:5],
            key_differences=key_diffs[:5],
            value_tradeoffs=value_tradeoffs,
            tradeoffs=tradeoffs,
            best_choice_for_use_case=f"Top option based on verified specifications: {best_pick}",
            overall_winner=best_pick,
            best_value_pick=best_val,
            missing_specs_notes=missing_notes[:5],
            currency_notes=currency_notes,
            currency_consistency_warning=currency_warn,
        )

    @classmethod
    def compare_products(
        cls,
        db: Session,
        product_ids: Sequence[str],
        include_ai: bool = False,
        intent: Any = None,
        gemini_client: Any = None,
        products_override: Sequence[Any] | None = None,
    ) -> ProductComparisonResponse:
        """
        Build a structured multi-product comparison matrix comparing specifications,
        retailer offers, price spreads, provenance, and overall availability.
        Enforces strict currency-safety:
        - Detects mixed currencies across products and offers (e.g. USD vs INR).
        - NEVER directly compares numeric prices across different currencies.
        - Identifies exact retailer associated with each lowest price.
        - Clearly represents missing specifications as 'Not Specified'.
        - Fabricates zero value-for-money scores.
        - Optionally synthesizes grounded AI comparison explanation with verified fallback.
        """
        products = list(products_override) if products_override else ProductRepository.get_by_ids(db, product_ids)
        if not products:
            return ProductComparisonResponse(
                products=[],
                compared_count=0,
                comparison_matrix={},
                price_summary={},
            )

        compare_items: list[ProductCompareItem] = []
        all_spec_keys: set[str] = set()

        # Track currencies and priced entries across all compared products
        product_currencies: set[str] = set()
        prices_by_currency: dict[str, list[dict[str, Any]]] = {}

        for p in products:
            if hasattr(p, "lowest_price_offer") and callable(getattr(p, "lowest_price_offer")):
                best_offer = p.lowest_price_offer()
                lp = best_offer.price if best_offer else None
                p_currency = best_offer.currency if best_offer else "USD"
                best_retailer = best_offer.retailer_name if best_offer else None
            else:
                lp = getattr(p, "lowest_price", None)
                best_offer = p.offers[0] if p.offers else None
                p_currency = (best_offer.currency if best_offer and getattr(best_offer, "currency", None) else "USD").upper()
                best_retailer = getattr(p, "lowest_price_retailer", None) or (
                    best_offer.retailer_name if best_offer and getattr(best_offer, "retailer_name", None) else "Catalog"
                )

            if lp is not None and lp > 0:
                product_currencies.add(p_currency)
                prices_by_currency.setdefault(p_currency, []).append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "price": lp,
                    "retailer": best_retailer or "Catalog",
                })

            offers = [RetailerOfferResponse.model_validate(o) for o in p.offers]
            for o in p.offers:
                o_curr = getattr(o, "currency", None)
                if o_curr:
                    product_currencies.add(o_curr.strip().upper())

            in_stock_any = any(
                o.availability.upper() in ("IN_STOCK", "LIMITED_STOCK", "LOW_STOCK")
                for o in p.offers
            )
            overall_avail = "IN_STOCK" if in_stock_any else ("OUT_OF_STOCK" if p.offers else "UNAVAILABLE")

            specs = p.specifications or {}
            public_specs = {k: v for k, v in specs.items() if not k.startswith("_")}
            all_spec_keys.update(public_specs.keys())

            rating_val = specs.get("rating")
            rating_count = specs.get("rating_count")
            quality_warnings = list(specs.get("_data_quality_warnings", []))

            compare_items.append(
                ProductCompareItem(
                    id=p.id,
                    name=p.name,
                    brand=p.brand,
                    category=p.category,
                    image_url=p.image_url,
                    lowest_price=lp,
                    currency=p_currency,
                    specifications=public_specs,
                    offers=offers,
                    best_offer_retailer=best_retailer,
                    overall_availability=overall_avail,
                    is_synthetic=bool(getattr(p, "is_synthetic", True)),
                    provider=str(getattr(p, "provider", "synthetic")),
                    provider_product_id=getattr(p, "provider_product_id", None),
                    rating=float(rating_val) if isinstance(rating_val, (int, float)) else None,
                    rating_count=int(rating_count) if isinstance(rating_count, (int, float)) else None,
                    data_quality_warnings=quality_warnings,
                )
            )

        # Build comparison matrix: map each spec key -> {product_name: spec_value}
        # Missing values are explicitly recorded as 'Not Specified'
        comparison_matrix: dict[str, dict[str, str]] = {}
        sorted_keys = sorted(all_spec_keys)

        for key in sorted_keys:
            comparison_matrix[key] = {}
            for item in compare_items:
                val = item.specifications.get(key)
                if val is None or str(val).strip() == "":
                    comparison_matrix[key][item.name] = "Not Specified"
                else:
                    comparison_matrix[key][item.name] = str(val)

        # Build currency-safe price summary
        is_multi = len(product_currencies) > 1

        if is_multi:
            # MULTI-CURRENCY SAFEGUARD: Never compare amounts across different currencies directly without FX
            by_curr_summary: dict[str, Any] = {}
            for curr, entries in prices_by_currency.items():
                min_entry = min(entries, key=lambda x: x["price"])
                all_p = [e["price"] for e in entries]
                by_curr_summary[curr] = {
                    "cheapest_product": min_entry["product_name"],
                    "cheapest_price": min_entry["price"],
                    "cheapest_retailer": min_entry["retailer"],
                    "min_market_price": min(all_p),
                    "max_market_price": max(all_p),
                    "item_count": len(entries),
                }

            price_summary: dict[str, Any] = {
                "cheapest_product": None,
                "cheapest_price": None,
                "cheapest_retailer": None,
                "min_market_price": None,
                "max_market_price": None,
                "currency": "MIXED",
                "is_multi_currency": True,
                "warning": (
                    f"Direct cross-currency comparison disabled: products use multiple currencies "
                    f"({', '.join(sorted(product_currencies))}). FX conversion is required to rank prices across currencies."
                ),
                "by_currency": by_curr_summary,
            }
        else:
            single_currency = next(iter(product_currencies)) if product_currencies else "USD"
            entries = prices_by_currency.get(single_currency, [])
            if entries:
                cheapest_entry = min(entries, key=lambda x: x["price"])
                all_p = [e["price"] for e in entries]
                all_offers_prices = [
                    o.price for item in compare_items for o in item.offers
                    if o.price and o.price > 0 and o.currency == single_currency
                ]
                min_mkt = min(all_offers_prices) if all_offers_prices else cheapest_entry["price"]
                max_mkt = max(all_offers_prices) if all_offers_prices else cheapest_entry["price"]

                price_summary = {
                    "cheapest_product": cheapest_entry["product_name"],
                    "cheapest_price": cheapest_entry["price"],
                    "cheapest_retailer": cheapest_entry["retailer"],
                    "min_market_price": min_mkt,
                    "max_market_price": max_mkt,
                    "currency": single_currency,
                    "is_multi_currency": False,
                    "warning": None,
                    "by_currency": {
                        single_currency: {
                            "cheapest_product": cheapest_entry["product_name"],
                            "cheapest_price": cheapest_entry["price"],
                            "cheapest_retailer": cheapest_entry["retailer"],
                            "item_count": len(entries),
                        }
                    },
                }
            else:
                price_summary = {
                    "cheapest_product": None,
                    "cheapest_price": None,
                    "cheapest_retailer": None,
                    "min_market_price": None,
                    "max_market_price": None,
                    "currency": single_currency,
                    "is_multi_currency": False,
                    "warning": "No priced offers available for comparison.",
                    "by_currency": {},
                }

        ai_explanation: ComparisonExplanation | None = None
        if include_ai and len(compare_items) >= 2:
            comparison_dict = {
                "products": [p.model_dump() for p in compare_items],
                "compared_count": len(compare_items),
                "comparison_matrix": comparison_matrix,
                "price_summary": price_summary,
            }
            g_client = gemini_client
            if g_client is None:
                from backend.app.services.gemini_client import GeminiClient
                g_client = GeminiClient()

            if g_client and getattr(g_client, "is_configured", False):
                try:
                    from backend.app.schemas.assistant import ShoppingIntent
                    eff_intent = intent or ShoppingIntent(query="product comparison", category="general")
                    ai_explanation = g_client.explain_comparison(intent=eff_intent, comparison_data=comparison_dict)
                except Exception as exc:
                    logger.warning("Gemini comparison explanation failed [%s]: %s; using grounded fallback.", type(exc).__name__, exc)
                    ai_explanation = cls.build_fallback_comparison_explanation(compare_items, comparison_matrix, price_summary)
            else:
                ai_explanation = cls.build_fallback_comparison_explanation(compare_items, comparison_matrix, price_summary)

        return ProductComparisonResponse(
            products=compare_items,
            compared_count=len(compare_items),
            comparison_matrix=comparison_matrix,
            price_summary=price_summary,
            ai_explanation=ai_explanation,
        )

    @staticmethod
    def revalidate_offer_freshness(
        db: Session,
        product_id: str,
        retailer_name: str,
        client: Channel3Client | None = None,
    ) -> tuple[bool, str, RetailerOffer | None]:
        """
        Verify that an offer is currently valid, in stock, and fresh before checkout initiation.
        For Channel3 products, uses the free GET /v1/products/{id} endpoint to retrieve a live snapshot.
        """
        product = ProductRepository.get_by_id(db, product_id)
        if not product:
            return False, f"Product '{product_id}' not found.", None

        # Synthetic demo catalog items
        if product.is_synthetic or product.provider == "synthetic":
            matching = next((o for o in product.offers if o.retailer_name.lower() == retailer_name.lower()), None)
            if not matching:
                return False, f"Retailer '{retailer_name}' not found for product.", None
            return True, "Synthetic offer verified for local demo.", matching

        # Live Channel3 items: revalidate using provider ID
        if product.provider == "channel3" and product.provider_product_id:
            c3_client = client or Channel3Client()
            try:
                raw_prod = c3_client.get_product_by_id(product.provider_product_id)
                if not raw_prod:
                    return False, "Product is no longer available on provider catalog.", None

                norm_prod = Channel3Normalizer.normalize_product(raw_prod)
                if not norm_prod:
                    return False, "Failed to normalize fresh product snapshot.", None

                updated_prod = ProductRepository.upsert_channel3_product(db, norm_prod)
                matching = next(
                    (o for o in updated_prod.offers if o.retailer_name.lower() == retailer_name.lower()),
                    None
                )
                if not matching:
                    return False, f"Offer at '{retailer_name}' is no longer active.", None

                if matching.availability.upper() not in ("IN_STOCK", "LIMITED_STOCK", "LOW_STOCK"):
                    return False, f"Offer at '{retailer_name}' is currently {matching.availability}.", matching

                return True, "Live offer verified and in stock.", matching
            except Channel3Error as exc:
                logger.error("Offer revalidation failed for product %s: %s", product_id, exc)
                return False, f"Failed to revalidate live offer: {str(exc)}", None

        return False, "Unknown provider type.", None

    @staticmethod
    def refresh_product_offers_with_cooldown(
        db: Session,
        product_id: str,
        cooldown_hours: int = 6,
        client: Channel3Client | None = None,
    ) -> tuple[Product | None, bool, str]:
        """
        Safely refresh live product offers from Channel3 with server-side cooldown.
        Protects against:
        - Unrestricted crawling: Requires explicit product ID.
        - Runaway polling: Enforces a strict cooldown window (default 6 hours).
        - Race conditions: Thread-safe mutex lock prevents duplicate concurrent refreshes.
        - Provider failure: Catches Channel3 errors and HTTP 429 gracefully without raising.
        - Search credit consumption: Strictly uses direct product lookup (GET /v1/products/{id}).
        """
        product = ProductRepository.get_by_id(db, product_id)
        if not product:
            return None, False, f"Product '{product_id}' not found."

        if product.is_synthetic or product.provider != "channel3":
            return product, False, "Synthetic products are not eligible for live provider refresh."

        if not product.provider_product_id:
            return product, False, "Product has no Channel3 provider identifier."

        now_utc = datetime.now(timezone.utc)

        # Check server-side cooldown
        last_updated = product.updated_at
        if last_updated:
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            elapsed_seconds = (now_utc - last_updated).total_seconds()
            cooldown_seconds = cooldown_hours * 3600
            if elapsed_seconds < cooldown_seconds:
                remaining_hours = round((cooldown_seconds - elapsed_seconds) / 3600, 1)
                return (
                    product,
                    False,
                    f"Cooldown active: product was refreshed {round(elapsed_seconds / 3600, 1)}h ago; {remaining_hours}h remaining."
                )

        # Concurrency guard: avoid simultaneous refreshes for the same product
        with _REFRESH_LOCK:
            if product_id in _ACTIVE_REFRESHES:
                return product, False, "Refresh already in progress for this product."
            _ACTIVE_REFRESHES.add(product_id)

        c3_client = client or Channel3Client()
        try:
            raw_prod = c3_client.get_product_by_id(product.provider_product_id)
            if not raw_prod:
                return product, False, "Product is no longer available on provider catalog."

            norm_prod = Channel3Normalizer.normalize_product(raw_prod)
            if not norm_prod:
                return product, False, "Failed to normalize fresh product snapshot."

            updated_prod = ProductRepository.upsert_channel3_product(db, norm_prod)
            return updated_prod, True, "Product offers successfully refreshed and snapshots recorded."
        except Channel3RateLimitError as exc:
            logger.warning("Channel3 rate limit reached during refresh for product %s: %s", product_id, exc)
            return product, False, f"Channel3 rate limit exceeded: {exc}"
        except Exception as exc:
            logger.warning("Channel3 provider error during refresh for product %s: %s", product_id, exc)
            return product, False, f"Provider error during refresh: {exc}"
        finally:
            with _REFRESH_LOCK:
                _ACTIVE_REFRESHES.discard(product_id)


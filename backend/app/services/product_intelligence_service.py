"""
AI-Powered Multilingual Shopping Intelligence Platform
Product Intelligence & Historical Price Analysis Service (Phase 3B).
Calculates deterministic price intelligence metrics using authentic PriceSnapshot records
and active RetailerOffer listings:
- Best current price by currency (available stock only)
- Historical extremes (lowest and highest recorded prices)
- Price changes per retailer, currency, and provider offer identity
- Cross-retailer comparisons and authentic savings potential
- Statistical price stability assessment (coefficient of variation)
- Chronological price history points
Enforces zero fabricated data, currency isolation, and synthetic exclusion.
"""

from datetime import datetime, timezone
import math
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.shopping import Product, RetailerOffer, PriceSnapshot
from backend.app.repositories.product_repository import ProductRepository
from backend.app.schemas.product import (
    CurrentBestPrice,
    HistoricalPricePoint,
    OfferPriceChange,
    CurrencyRetailerComparison,
    PriceStability,
    PriceSnapshotPoint,
    ProductIntelligenceResponse,
    RetailerOfferResponse,
)

logger = logging.getLogger(__name__)

AVAILABLE_STOCK_STATUSES = {"IN_STOCK", "LIMITED_STOCK", "LOW_STOCK", "AVAILABLE"}


class ProductIntelligenceService:
    """Deterministic business logic for product price intelligence and historical analytics."""

    @staticmethod
    def is_offer_available(availability: str | None) -> bool:
        """Check whether an offer is in stock and available for immediate purchase."""
        if not availability:
            return False
        return availability.strip().upper() in AVAILABLE_STOCK_STATUSES

    @staticmethod
    def get_product_intelligence(db: Session, product_id: str) -> ProductIntelligenceResponse | None:
        """
        Build an authoritative ProductIntelligenceResponse for a product.
        Returns None if product does not exist in the database.
        """
        product = ProductRepository.get_by_id(db, product_id)
        if not product:
            return None

        # Base product identity
        is_synthetic = bool(getattr(product, "is_synthetic", True))
        provider = str(getattr(product, "provider", "synthetic"))
        provider_product_id = getattr(product, "provider_product_id", None)

        # ---------------------------------------------------------------------
        # 1. Current Retailer Offers & Best Current Prices
        # ---------------------------------------------------------------------
        raw_offers = list(db.scalars(select(RetailerOffer).where(RetailerOffer.product_id == product.id)).all())
        valid_current_offers = [
            o for o in raw_offers
            if ProductRepository.is_valid_price(o.price)
        ]
        current_offers_response = [
            RetailerOfferResponse.model_validate(o) for o in valid_current_offers
        ]

        # Group current offers by normalized currency
        current_offers_by_currency: dict[str, list[RetailerOffer]] = {}
        for o in valid_current_offers:
            curr = (o.currency or "USD").strip().upper()
            current_offers_by_currency.setdefault(curr, []).append(o)

        best_current_prices: list[CurrentBestPrice] = []
        retailer_comparisons: list[CurrencyRetailerComparison] = []

        for curr, offers in sorted(current_offers_by_currency.items()):
            avail_offers = [o for o in offers if ProductIntelligenceService.is_offer_available(o.availability)]
            unavail_offers = [o for o in offers if not ProductIntelligenceService.is_offer_available(o.availability)]

            # Best Current Available Price
            if avail_offers:
                best_o = min(avail_offers, key=lambda o: o.price)
                best_current_prices.append(
                    CurrentBestPrice(
                        currency=curr,
                        price=round(float(best_o.price), 2),
                        retailer_name=best_o.retailer_name,
                        availability=best_o.availability,
                        provider_offer_id=getattr(best_o, "provider_offer_id", None),
                        product_url=getattr(best_o, "product_url", None),
                    )
                )

            # Retailer Comparison (only compare amongst available current offers)
            avail_retailers = list(dict.fromkeys(o.retailer_name for o in avail_offers))
            unavail_retailers = list(dict.fromkeys(o.retailer_name for o in unavail_offers))

            if avail_offers:
                cheapest_o = min(avail_offers, key=lambda o: o.price)
                highest_o = max(avail_offers, key=lambda o: o.price)
                cheapest_p = round(float(cheapest_o.price), 2)
                highest_p = round(float(highest_o.price), 2)
                diff = round(highest_p - cheapest_p, 2)
                savings_pct = (
                    round(((highest_p - cheapest_p) / highest_p) * 100.0, 2)
                    if highest_p > 0 else 0.0
                )
                retailer_comparisons.append(
                    CurrencyRetailerComparison(
                        currency=curr,
                        cheapest_retailer=cheapest_o.retailer_name,
                        cheapest_price=cheapest_p,
                        highest_retailer=highest_o.retailer_name,
                        highest_price=highest_p,
                        price_difference=diff,
                        potential_savings_percentage=savings_pct,
                        available_offers_count=len(avail_offers),
                        available_retailers=avail_retailers,
                        unavailable_retailers=unavail_retailers,
                    )
                )
            else:
                retailer_comparisons.append(
                    CurrencyRetailerComparison(
                        currency=curr,
                        cheapest_retailer=None,
                        cheapest_price=None,
                        highest_retailer=None,
                        highest_price=None,
                        price_difference=None,
                        potential_savings_percentage=None,
                        available_offers_count=0,
                        available_retailers=[],
                        unavailable_retailers=unavail_retailers,
                    )
                )

        # ---------------------------------------------------------------------
        # 2. Historical Price Snapshots Handling
        # ---------------------------------------------------------------------
        # Synthetic products or demo fixtures never contain genuine historical snapshots
        if is_synthetic:
            # Synthetic exclusion: return empty history and insufficient stability
            stability_entries: list[PriceStability] = []
            eval_currencies = sorted(current_offers_by_currency.keys()) or ["USD"]
            for curr in eval_currencies:
                stability_entries.append(
                    PriceStability(
                        currency=curr,
                        status="INSUFFICIENT_DATA",
                        observation_count=0,
                        volatility_score=None,
                        min_recorded_price=None,
                        max_recorded_price=None,
                        price_range=None,
                        explanation="Synthetic demo products do not collect authentic historical price snapshots.",
                    )
                )

            return ProductIntelligenceResponse(
                product_id=product.id,
                name=product.name,
                brand=product.brand,
                category=product.category,
                image_url=product.image_url,
                is_synthetic=True,
                provider=provider,
                provider_product_id=provider_product_id,
                tracking_start_date=None,
                total_genuine_observations=0,
                is_tracking_active=False,
                current_offers=current_offers_response,
                best_current_prices=best_current_prices,
                historical_lowest_prices=[],
                historical_highest_prices=[],
                price_changes=[],
                retailer_comparisons=retailer_comparisons,
                price_stability=stability_entries,
                price_history=[],
            )

        # Genuine product: load authentic snapshots from repository
        raw_snapshots = ProductRepository.get_price_snapshots(db, product.id)
        if not raw_snapshots and not is_synthetic and provider == "channel3":
            # For genuine, non-synthetic Channel3 products with zero historical snapshots,
            # record baseline snapshots using existing valid, genuine retailer offers.
            genuine_offers = [
                o for o in valid_current_offers
                if not getattr(o, "is_synthetic", False) and ProductRepository.is_valid_price(o.price)
            ]
            if genuine_offers:
                # Deduplicate offers by (retailer, currency, provider_offer_id, price, availability)
                seen_keys = set()
                deduped_offers = []
                for o in genuine_offers:
                    key = (
                        (o.retailer_name or "").strip(),
                        (o.currency or "USD").strip().upper(),
                        getattr(o, "provider_offer_id", None),
                        round(float(o.price), 2),
                        getattr(o, "availability", "IN_STOCK") or "IN_STOCK",
                    )
                    if key not in seen_keys:
                        seen_keys.add(key)
                        deduped_offers.append(o)

                recorded = ProductRepository.record_snapshots_for_product(db, product, deduped_offers)
                new_snapshots = [s for s in recorded if s in db.new]
                if new_snapshots:
                    db.commit()
                    raw_snapshots = ProductRepository.get_price_snapshots(db, product.id)

        snapshots = [
            s for s in raw_snapshots
            if not s.is_synthetic and ProductRepository.is_valid_price(s.price)
        ]
        snapshots.sort(key=lambda s: s.recorded_at)

        total_genuine_observations = len(snapshots)
        tracking_start_date = snapshots[0].recorded_at if snapshots else None
        is_tracking_active = (provider == "channel3" and provider_product_id is not None)

        # Group snapshots by currency
        snapshots_by_currency: dict[str, list[PriceSnapshot]] = {}
        for s in snapshots:
            curr = (s.currency or "USD").strip().upper()
            snapshots_by_currency.setdefault(curr, []).append(s)

        # ---------------------------------------------------------------------
        # 3. Historical Extremes (All-time Low & High per Currency)
        # ---------------------------------------------------------------------
        historical_lowest_prices: list[HistoricalPricePoint] = []
        historical_highest_prices: list[HistoricalPricePoint] = []

        for curr, snaps in sorted(snapshots_by_currency.items()):
            if not snaps:
                continue
            # All-time lowest: min price; if tie, earliest recorded date
            lowest_s = min(snaps, key=lambda s: (s.price, s.recorded_at))
            historical_lowest_prices.append(
                HistoricalPricePoint(
                    currency=curr,
                    price=round(float(lowest_s.price), 2),
                    retailer_name=lowest_s.retailer_name,
                    recorded_at=lowest_s.recorded_at,
                    provider_offer_id=lowest_s.provider_offer_id,
                )
            )

            # All-time highest: max price; if tie, earliest recorded date
            highest_s = max(snaps, key=lambda s: (s.price, -s.recorded_at.timestamp()))
            historical_highest_prices.append(
                HistoricalPricePoint(
                    currency=curr,
                    price=round(float(highest_s.price), 2),
                    retailer_name=highest_s.retailer_name,
                    recorded_at=highest_s.recorded_at,
                    provider_offer_id=highest_s.provider_offer_id,
                )
            )

        # ---------------------------------------------------------------------
        # 4. Price Changes by Retailer & Provider Offer Identity
        # ---------------------------------------------------------------------
        # Compare latest vs previous snapshot for identical (retailer, currency, provider_offer_id)
        offers_history: dict[tuple[str, str, str | None], list[PriceSnapshot]] = {}
        for s in snapshots:
            key = (s.retailer_name, (s.currency or "USD").strip().upper(), s.provider_offer_id)
            offers_history.setdefault(key, []).append(s)

        price_changes: list[OfferPriceChange] = []
        for (ret_name, curr, prov_id), snaps in sorted(offers_history.items(), key=lambda x: (x[0][0], x[0][1], str(x[0][2]))):
            snaps.sort(key=lambda s: s.recorded_at)
            obs_count = len(snaps)
            if obs_count < 2:
                # Strictly report INSUFFICIENT_DATA; never fabricate 0% price change
                price_changes.append(
                    OfferPriceChange(
                        retailer_name=ret_name,
                        currency=curr,
                        provider_offer_id=prov_id,
                        current_price=round(float(snaps[-1].price), 2),
                        previous_price=None,
                        absolute_change=None,
                        percentage_change=None,
                        status="INSUFFICIENT_DATA",
                        previous_recorded_at=None,
                        latest_recorded_at=snaps[-1].recorded_at,
                        observation_count=obs_count,
                    )
                )
            else:
                prev = snaps[-2]
                latest = snaps[-1]
                prev_p = round(float(prev.price), 2)
                curr_p = round(float(latest.price), 2)
                abs_diff = round(curr_p - prev_p, 2)
                pct_diff = round(((curr_p - prev_p) / prev_p) * 100.0, 2) if prev_p > 0 else 0.0

                if abs(abs_diff) < 1e-4:
                    status = "UNCHANGED"
                elif curr_p > prev_p:
                    status = "INCREASED"
                else:
                    status = "DECREASED"

                price_changes.append(
                    OfferPriceChange(
                        retailer_name=ret_name,
                        currency=curr,
                        provider_offer_id=prov_id,
                        current_price=curr_p,
                        previous_price=prev_p,
                        absolute_change=abs_diff,
                        percentage_change=pct_diff,
                        status=status,
                        previous_recorded_at=prev.recorded_at,
                        latest_recorded_at=latest.recorded_at,
                        observation_count=obs_count,
                    )
                )

        # ---------------------------------------------------------------------
        # 5. Price Stability Assessment per Currency
        # ---------------------------------------------------------------------
        price_stability: list[PriceStability] = []
        eval_currencies = sorted(set(list(snapshots_by_currency.keys()) + list(current_offers_by_currency.keys())))
        if not eval_currencies:
            eval_currencies = ["USD"]

        for curr in eval_currencies:
            snaps = snapshots_by_currency.get(curr, [])
            if len(snaps) < 3:
                min_p = round(min(s.price for s in snaps), 2) if snaps else None
                max_p = round(max(s.price for s in snaps), 2) if snaps else None
                rng = round(max_p - min_p, 2) if (min_p is not None and max_p is not None) else None
                price_stability.append(
                    PriceStability(
                        currency=curr,
                        status="INSUFFICIENT_DATA",
                        observation_count=len(snaps),
                        volatility_score=None,
                        min_recorded_price=min_p,
                        max_recorded_price=max_p,
                        price_range=rng,
                        explanation=f"Insufficient observations ({len(snaps)} recorded; minimum 3 required for stability calculation).",
                    )
                )
            else:
                prices = [float(s.price) for s in snaps]
                min_p = round(min(prices), 2)
                max_p = round(max(prices), 2)
                rng = round(max_p - min_p, 2)
                mean_p = sum(prices) / len(prices)

                if mean_p <= 0:
                    status = "INSUFFICIENT_DATA"
                    cv = None
                    explanation = "Mean price is zero or invalid."
                else:
                    variance = sum((p - mean_p) ** 2 for p in prices) / len(prices)
                    stdev = math.sqrt(variance)
                    cv = round((stdev / mean_p) * 100.0, 2)

                    if cv < 5.0:
                        status = "STABLE"
                        explanation = f"Price is highly stable with low fluctuation ({cv:.1f}% coefficient of variation across {len(snaps)} observations)."
                    elif cv <= 15.0:
                        status = "MODERATE"
                        explanation = f"Price exhibits moderate fluctuation ({cv:.1f}% coefficient of variation across {len(snaps)} observations)."
                    else:
                        status = "VOLATILE"
                        explanation = f"Price exhibits high volatility ({cv:.1f}% coefficient of variation across {len(snaps)} observations)."

                price_stability.append(
                    PriceStability(
                        currency=curr,
                        status=status,
                        observation_count=len(snaps),
                        volatility_score=cv,
                        min_recorded_price=min_p,
                        max_recorded_price=max_p,
                        price_range=rng,
                        explanation=explanation,
                    )
                )

        # ---------------------------------------------------------------------
        # 6. Chronological Price History Points
        # ---------------------------------------------------------------------
        price_history = [
            PriceSnapshotPoint(
                id=s.id,
                retailer_name=s.retailer_name,
                provider_offer_id=s.provider_offer_id,
                price=round(float(s.price), 2),
                currency=(s.currency or "USD").strip().upper(),
                availability=s.availability,
                recorded_at=s.recorded_at,
            )
            for s in snapshots
        ]

        return ProductIntelligenceResponse(
            product_id=product.id,
            name=product.name,
            brand=product.brand,
            category=product.category,
            image_url=product.image_url,
            is_synthetic=False,
            provider=provider,
            provider_product_id=provider_product_id,
            tracking_start_date=tracking_start_date,
            total_genuine_observations=total_genuine_observations,
            is_tracking_active=is_tracking_active,
            current_offers=current_offers_response,
            best_current_prices=best_current_prices,
            historical_lowest_prices=historical_lowest_prices,
            historical_highest_prices=historical_highest_prices,
            price_changes=price_changes,
            retailer_comparisons=retailer_comparisons,
            price_stability=price_stability,
            price_history=price_history,
        )

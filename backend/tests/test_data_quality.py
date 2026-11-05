"""
AI-Powered Multilingual Shopping Intelligence Platform
Data Quality, Taxonomy Consistency, and Comparison Validation Tests.
All tests run with mocked fixtures — ZERO live Channel3 credits consumed.
"""

from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy.orm import Session

from backend.app.models.shopping import Product, RetailerOffer, ShoppingSession, SpendingMandate
from backend.app.repositories.product_repository import ProductRepository
from backend.app.services.channel3_client import Channel3Client
from backend.app.services.channel3_normalizer import Channel3Normalizer
from backend.app.services.product_service import ProductService
from backend.app.services.policy_engine import SpendingPolicyEngine


# -------------------------------------------------------------------------
# 1. Suspicious Brand Detection (Anker / Nebula Robotics)
# -------------------------------------------------------------------------

def test_suspicious_brand_detection():
    """
    Verify upstream brand misattribution is detected and flagged as a data quality warning
    while faithfully preserving the provider's raw brand value.
    """
    raw_data = {
        "id": "c3_anker_space_one",
        "title": "Anker Soundcore Space One Wireless Headphones - Black",
        "brands": [{"id": "KDuu", "name": "Nebula Robotics"}],
        "category": {"title": "Over-Ear Headphones"},
        "description": "Anker Soundcore Space One with Adaptive Noise Cancelling.",
        "offers": [
            {
                "domain": "bestbuy.com",
                "price": {"price": 79.99, "currency": "USD"},
                "availability": "InStock",
            }
        ],
    }

    norm = Channel3Normalizer.normalize_product(raw_data)
    assert norm is not None
    # Source provider brand must be preserved (not overwritten by heuristic guess)
    assert norm.brand == "Nebula Robotics"
    # Warning must be emitted
    assert len(norm.data_quality_warnings) >= 1
    assert any("Brand discrepancy" in w and "Anker" in w for w in norm.data_quality_warnings)
    assert "_data_quality_warnings" in norm.specifications


# -------------------------------------------------------------------------
# 2. Taxonomy Contradiction Detection (Over-Ear vs Ear hooks)
# -------------------------------------------------------------------------

def test_taxonomy_contradiction_detection():
    """
    Verify category vs specification contradiction is detected and flagged
    without mutating the raw category or specification values.
    """
    raw_data = {
        "id": "c3_jvc_has59wa",
        "title": "JVC HAS59WA Wireless Bluetooth Headphones",
        "brands": [{"name": "JVC"}],
        "category": {"title": "Over-Ear Headphones"},
        "description": "JVC wireless headset with lightweight design.",
        "structured_attributes": {
            "headphone-style": ["Ear hooks"],
        },
        "offers": [
            {
                "domain": "microcenter.com",
                "price": {"price": 19.65, "currency": "USD"},
                "availability": "InStock",
            }
        ],
    }

    norm = Channel3Normalizer.normalize_product(raw_data)
    assert norm is not None
    assert norm.category == "Over-Ear Headphones"
    assert norm.specifications["headphone-style"] == "Ear hooks"
    assert any("Taxonomy discrepancy" in w for w in norm.data_quality_warnings)


# -------------------------------------------------------------------------
# 3. Model Identifier Discrepancy (HAS59WA vs HAS59W)
# -------------------------------------------------------------------------

def test_model_variant_suffix_detection():
    """
    Verify title SKU variant (HAS59WA) vs description base model (HAS59W) is noted.
    """
    raw_data = {
        "id": "c3_jvc_model_var",
        "title": "JVC HAS59WA Wireless Bluetooth Headphones",
        "brands": [{"name": "JVC"}],
        "category": {"title": "Audio"},
        "description": "JVC HAS59W wireless headphones featuring 50 hours battery life.",
        "offers": [],
    }

    norm = Channel3Normalizer.normalize_product(raw_data)
    assert norm is not None
    assert any("Model variant discrepancy" in w for w in norm.data_quality_warnings)


# -------------------------------------------------------------------------
# 4. Valid Numeric Rating Validation
# -------------------------------------------------------------------------

def test_valid_numeric_rating_representation():
    """
    Verify ratings are parsed into validated float and integer types,
    resolving the stringified rating defect.
    """
    raw_data = {
        "id": "c3_rating_test",
        "title": "Sennheiser Accentum Wireless Headphones",
        "brands": [{"name": "Sennheiser"}],
        "category": {"title": "Over-Ear Headphones"},
        "ratings": {"value": 4.14, "count": 108},
        "offers": [],
    }

    norm = Channel3Normalizer.normalize_product(raw_data)
    assert norm is not None
    # Must be float, NOT string "4.14"
    assert isinstance(norm.rating, float)
    assert norm.rating == 4.14
    assert isinstance(norm.rating_count, int)
    assert norm.rating_count == 108
    assert norm.specifications["rating"] == 4.14


# -------------------------------------------------------------------------
# 5. Invalid Rating Formats Handled Safely
# -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_ratings, expected_rating",
    [
        ({"value": "five stars", "count": 10}, None),  # Non-numeric
        ({"value": -1.5, "count": 5}, None),           # Negative out of bounds
        ({"value": 15.0, "count": 2}, None),           # Far out of bounds
        ({"value": 8.0, "count": 50}, 4.0),            # 10-point scale normalized to 5-point
        (None, None),                                  # Missing
        ({}, None),                                    # Empty dict
    ],
)
def test_invalid_rating_formats_handled_safely(raw_ratings, expected_rating):
    """Verify malformed ratings never crash normalization and do not produce fabricated numbers."""
    raw_data = {
        "id": "c3_rating_edge",
        "title": "Generic Audio Device",
        "brands": [{"name": "Generic"}],
        "category": {"title": "Audio"},
        "ratings": raw_ratings,
        "offers": [],
    }

    norm = Channel3Normalizer.normalize_product(raw_data)
    assert norm is not None
    assert norm.rating == expected_rating


# -------------------------------------------------------------------------
# 6. Missing Specifications Clearly Represented as 'Not Specified'
# -------------------------------------------------------------------------

def test_missing_specifications_in_comparison(test_session: Session):
    """
    Verify comparison matrix represents missing specifications as 'Not Specified'
    rather than omitting or fabricating values.
    """
    prod1 = Product(
        name="Device Alpha",
        brand="BrandA",
        category="Tech",
        specifications={"battery_life": "20h", "bluetooth_version": "5.3"},
    )
    prod2 = Product(
        name="Device Beta",
        brand="BrandB",
        category="Tech",
        specifications={"battery_life": "30h"},  # Missing bluetooth_version
    )
    test_session.add_all([prod1, prod2])
    test_session.commit()

    comparison = ProductService.compare_products(test_session, [prod1.id, prod2.id])
    assert comparison.compared_count == 2
    matrix = comparison.comparison_matrix

    assert "battery_life" in matrix
    assert matrix["battery_life"]["Device Alpha"] == "20h"
    assert matrix["battery_life"]["Device Beta"] == "30h"

    assert "bluetooth_version" in matrix
    assert matrix["bluetooth_version"]["Device Alpha"] == "5.3"
    assert matrix["bluetooth_version"]["Device Beta"] == "Not Specified"


# -------------------------------------------------------------------------
# 7. Distinct Product Variants Retained
# -------------------------------------------------------------------------

def test_distinct_product_variants_retained(test_session: Session):
    """Verify different SKUs/variants of a model family remain distinct in the database."""
    variant_blue = Channel3Normalizer.normalize_product({
        "id": "c3_has59wa_blue",
        "title": "JVC HAS59WA Wireless Bluetooth Headphones - Blue",
        "brands": [{"name": "JVC"}],
        "category": {"title": "Headphones"},
        "offers": [{"domain": "store.com", "price": {"price": 19.99, "currency": "USD"}}],
    })
    variant_black = Channel3Normalizer.normalize_product({
        "id": "c3_has59wb_black",
        "title": "JVC HAS59WB Wireless Bluetooth Headphones - Black",
        "brands": [{"name": "JVC"}],
        "category": {"title": "Headphones"},
        "offers": [{"domain": "store.com", "price": {"price": 19.99, "currency": "USD"}}],
    })

    saved_blue = ProductRepository.upsert_channel3_product(test_session, variant_blue)
    saved_black = ProductRepository.upsert_channel3_product(test_session, variant_black)

    assert saved_blue.id != saved_black.id
    assert saved_blue.provider_product_id == "c3_has59wa_blue"
    assert saved_black.provider_product_id == "c3_has59wb_black"
    assert ProductRepository.get_by_provider_id(test_session, "channel3", "c3_has59wa_blue") is not None
    assert ProductRepository.get_by_provider_id(test_session, "channel3", "c3_has59wb_black") is not None


# -------------------------------------------------------------------------
# 8. Multiple Offers Associated with Lowest Price & Retailer Identified
# -------------------------------------------------------------------------

def test_multiple_offers_with_retailer_identified(test_session: Session):
    """
    Verify multiple offers are preserved on a product and lowest price explicitly
    identifies the winning retailer.
    """
    norm = Channel3Normalizer.normalize_product({
        "id": "c3_multi_offer_prod",
        "title": "Soundcore Space One",
        "brands": [{"name": "Anker"}],
        "category": {"title": "Audio"},
        "offers": [
            {
                "domain": "crutchfield.com",
                "price": {"price": 99.99, "currency": "USD"},
                "availability": "InStock",
            },
            {
                "domain": "bestbuy.com",
                "price": {"price": 79.99, "currency": "USD", "compare_at_price": 99.99},
                "availability": "InStock",
            },
        ],
    })

    prod = ProductRepository.upsert_channel3_product(test_session, norm)
    detail = ProductService.get_product_detail(test_session, prod.id)
    assert detail is not None
    assert detail.total_offers == 2
    assert detail.lowest_price == 79.99
    assert detail.lowest_price_retailer == "bestbuy.com"


# -------------------------------------------------------------------------
# 9. Mixed-Currency Comparison Safeguard (USD vs INR)
# -------------------------------------------------------------------------

def test_mixed_currency_comparison_safeguard(test_session: Session):
    """
    CRITICAL FINANCIAL SAFEGUARD:
    Verify that USD and INR prices are NEVER directly compared numerically as if
    they were the same currency. Cross-currency ranking is disabled with a clear warning.
    """
    # Product A: US Headphones at $79.99 USD
    prod_usd = Product(
        name="Soundcore Space One",
        brand="Anker",
        category="Audio",
    )
    prod_usd.offers.append(
        RetailerOffer(retailer_name="BestBuy", price=79.99, currency="USD", product_url="https://bestbuy.com/deal")
    )

    # Product B: Laptop in India at ₹70,000 INR
    prod_inr = Product(
        name="AI Workstation Laptop",
        brand="Lenovo",
        category="Computers",
    )
    prod_inr.offers.append(
        RetailerOffer(retailer_name="Croma", price=70000.0, currency="INR", product_url="https://croma.com/deal")
    )

    test_session.add_all([prod_usd, prod_inr])
    test_session.commit()

    comparison = ProductService.compare_products(test_session, [prod_usd.id, prod_inr.id])
    assert comparison.compared_count == 2

    price_summary = comparison.price_summary
    # Cross-currency direct ranking must be DISABLED
    assert price_summary["is_multi_currency"] is True
    assert price_summary["currency"] == "MIXED"
    assert price_summary["cheapest_product"] is None
    assert price_summary["cheapest_price"] is None
    assert "multiple currencies" in price_summary["warning"].lower()

    # Must provide safe per-currency breakdowns
    by_currency = price_summary["by_currency"]
    assert "USD" in by_currency
    assert by_currency["USD"]["cheapest_price"] == 79.99
    assert by_currency["USD"]["cheapest_product"] == "Soundcore Space One"

    assert "INR" in by_currency
    assert by_currency["INR"]["cheapest_price"] == 70000.0
    assert by_currency["INR"]["cheapest_product"] == "AI Workstation Laptop"


# -------------------------------------------------------------------------
# 10. Missing Prices Do Not Become Zero
# -------------------------------------------------------------------------

def test_missing_prices_do_not_become_zero(test_session: Session):
    """Verify products without pricing remain None rather than 0.0."""
    prod_no_offers = Product(name="Unpriced Prototype", brand="Acme", category="R&D")
    test_session.add(prod_no_offers)
    test_session.commit()

    assert prod_no_offers.lowest_price() is None
    assert prod_no_offers.lowest_price_offer() is None

    detail = ProductService.get_product_detail(test_session, prod_no_offers.id)
    assert detail is not None
    assert detail.lowest_price is None
    assert detail.lowest_price_retailer is None


# -------------------------------------------------------------------------
# 11. Offer Freshness Revalidation
# -------------------------------------------------------------------------

def test_offer_freshness_revalidation_synthetic(test_session: Session):
    """Verify synthetic offers validate cleanly for development testing."""
    synth_prods, _ = ProductRepository.list_products(test_session, page=1, page_size=1)
    target = synth_prods[0]
    retailer = target.offers[0].retailer_name

    valid, reason, offer = ProductService.revalidate_offer_freshness(test_session, target.id, retailer)
    assert valid is True
    assert "Synthetic offer verified" in reason
    assert offer is not None


def test_offer_freshness_revalidation_live(test_session: Session):
    """Verify live Channel3 offer revalidation updates snapshot from provider."""
    norm = Channel3Normalizer.normalize_product({
        "id": "c3_refresh_prod",
        "title": "Anker Soundcore Headphones",
        "brands": [{"name": "Anker"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "bestbuy.com", "price": {"price": 79.99, "currency": "USD"}, "availability": "InStock"}],
    })
    saved = ProductRepository.upsert_channel3_product(test_session, norm)

    mock_client = MagicMock(spec=Channel3Client)
    # Simulate fresh snapshot where price changed to 74.99
    mock_client.get_product_by_id.return_value = {
        "id": "c3_refresh_prod",
        "title": "Anker Soundcore Headphones",
        "brands": [{"name": "Anker"}],
        "category": {"title": "Audio"},
        "offers": [{"domain": "bestbuy.com", "price": {"price": 74.99, "currency": "USD"}, "availability": "InStock"}],
    }

    valid, reason, offer = ProductService.revalidate_offer_freshness(
        test_session, saved.id, "bestbuy.com", client=mock_client
    )
    assert valid is True
    assert "Live offer verified" in reason
    assert offer is not None
    assert offer.price == 74.99


# -------------------------------------------------------------------------
# 12. Existing Spending-Policy Compatibility
# -------------------------------------------------------------------------

def test_spending_policy_compatibility_with_quality_checked_offers(test_session: Session):
    """
    Verify deterministic spending-policy engine operates flawlessly on
    quality-audited products and prevents currency mixing.
    """
    session = ShoppingSession(session_identifier="quality-audit-session")
    test_session.add(session)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=session.id,
        total_budget=500.0,
        maximum_item_price=300.0,
        maximum_quantity=3,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    # 1. USD item within budget: $79.99 -> ALLOWED
    dec1 = SpendingPolicyEngine.evaluate(mandate=mandate, unit_price=79.99, quantity=1, currency="USD")
    assert dec1.is_allowed is True
    assert dec1.decision == "ALLOWED"

    # 2. USD item exceeding max item ceiling: $350.00 -> BLOCKED
    dec2 = SpendingPolicyEngine.evaluate(mandate=mandate, unit_price=350.00, quantity=1, currency="USD")
    assert dec2.is_allowed is False
    assert dec2.decision in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
    assert any("item price" in r for r in dec2.reasons)

    # 3. Currency mismatch: ₹70,000 INR against USD mandate -> BLOCKED
    dec3 = SpendingPolicyEngine.evaluate(mandate=mandate, unit_price=70000.0, quantity=1, currency="INR")
    assert dec3.is_allowed is False
    assert dec3.decision == "BLOCKED"
    assert any("does not match your active mandate currency" in r for r in dec3.reasons)

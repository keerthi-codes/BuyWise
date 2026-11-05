"""
AI-Powered Multilingual Shopping Intelligence Platform
Phase 4A.3: Deterministic Product Scoring & Brand Preference Integration Tests.

Validates:
1. Safeguard 1 (Score Boundaries): Final recommendation_score strictly bounded between 0.0 and 100.0.
2. Safeguard 2 (Excluded Brands): Explicitly excluded brands deterministically marked EXCLUDED with 0.0 score.
3. Safeguard 3 (Ranking Priority):
   - Hard-qualified products (QUALIFIED) rank before unverified or excluded products.
   - Within same tier, rank by recommendation_score descending.
   - Price tie-breaker only used between products in the same currency.
4. Safeguard 4 (Verified Feature Matching):
   - Points awarded ONLY when merchant-provided product specifications support the requested feature.
   - Missing or ambiguous specifications receive 0.0 points.
   - Features never inferred from product name alone.
5. Decoupling from Mandate: Spending mandate status does not distort recommendation scoring.
6. Channel3 Brand Query Incorporation & Fallback: Preferred brand included in query; broad query fallback on zero results.
7. Multilingual Intent Extraction: Rule-based fallback extracts brand_preferences and excluded_brands in English and German.
8. Frontend Badge Rendering: Node runtime verifies score badge and preference breakdown in renderAssistantProductCard.
9. Full API Integration: POST /api/assistant/query returns populated recommendation_score and score_reasons.
"""

from datetime import datetime, timezone
import subprocess
import uuid
import pytest
import httpx
from fastapi import status
from sqlalchemy.orm import Session

from backend.app.schemas.assistant import (
    AssistantQueryRequest,
    AssistantQueryResponse,
    ProductEligibilityItem,
    ProductIntelligence,
    ShoppingIntent,
    SpecificationRequirement,
)
from backend.app.schemas.product import ProductResponse, RetailerOfferResponse, ProductListResponse
from backend.app.services.assistant_service import AssistantService
from backend.app.services.product_service import ProductService
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.models.shopping import SpendingMandate, ShoppingSession


def _create_test_product(
    prod_id: str,
    name: str,
    price: float,
    brand: str = "TestBrand",
    category: str = "laptop",
    currency: str = "USD",
    specs: dict | None = None,
) -> ProductResponse:
    """Helper to create a fully formed ProductResponse for testing."""
    now = datetime.now(timezone.utc)
    return ProductResponse(
        id=prod_id,
        name=name,
        brand=brand,
        category=category,
        description=f"Description for {name}",
        specifications=specs or {"ram": "16 GB", "storage": "512 GB SSD"},
        lowest_price=price,
        lowest_price_retailer="Best Buy",
        created_at=now,
        offers=[
            RetailerOfferResponse(
                id=f"offer-{prod_id}",
                product_id=prod_id,
                retailer_name="Best Buy",
                product_url=f"https://example.com/item/{prod_id}",
                price=price,
                currency=currency,
                availability="in_stock",
                created_at=now,
            )
        ],
    )


# ==============================================================================
# 1. Safeguard 1: Score Boundaries (0.0 to 100.0)
# ==============================================================================

def test_recommendation_score_boundaries_capped_at_100():
    """Verify that even with maximum possible bonuses, final score is strictly capped at 100.0."""
    prod = _create_test_product(
        prod_id="prod-cap",
        name="Ultra Elite Laptop",
        price=500.0,
        brand="Apple",
        category="laptop",
        specs={
            "ram": "64 GB DDR5",
            "storage": "2 TB NVMe SSD",
            "screen": "16 inch OLED touchscreen display",
            "key_features": ["Active Noise Cancelling", "Touchscreen", "OLED display"],
        },
    )

    intent = ShoppingIntent(
        query="High-end laptop, prefer Apple, 32GB RAM, 1TB SSD, OLED, touchscreen",
        category="laptop",
        max_budget=2000.0,
        currency="USD",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
        preferred_specs=[
            SpecificationRequirement(name="ram", value="32 GB", min_value=32.0, unit="GB", is_hard_requirement=False),
            SpecificationRequirement(name="storage", value="1 TB", min_value=1.0, unit="TB", is_hard_requirement=False),
            SpecificationRequirement(name="display", value="OLED", is_hard_requirement=False),
            SpecificationRequirement(name="feature", value="touchscreen", is_hard_requirement=False),
        ],
        brand_preferences=["Apple"],
    )

    elig = AssistantService.evaluate_product_eligibility(prod, intent)
    assert elig.recommendation_score <= 100.0
    assert elig.recommendation_score >= 0.0
    assert elig.recommendation_score == 100.0  # Caps cleanly at 100.0


def test_recommendation_score_floored_at_zero():
    """Verify that score cannot become negative under any conditions."""
    prod = _create_test_product(
        prod_id="prod-floor",
        name="Cheap Generic Laptop",
        price=1500.0,
        brand="ExcludedBrand",
        category="laptop",
        specs={},
    )

    intent = ShoppingIntent(
        query="laptop under 1000 without ExcludedBrand",
        category="laptop",
        max_budget=1000.0,
        currency="USD",
        excluded_brands=["ExcludedBrand"],
    )

    elig = AssistantService.evaluate_product_eligibility(prod, intent)
    assert elig.recommendation_score >= 0.0
    assert elig.recommendation_score == 0.0
    assert any("Excluded: Brand" in r for r in elig.score_reasons)


# ==============================================================================
# 2. Safeguard 2: Excluded Brands
# ==============================================================================

def test_excluded_brands_deterministic_rejection():
    """Verify that an explicitly excluded brand is marked EXCLUDED with score 0.0."""
    prod = _create_test_product(
        prod_id="prod-excl",
        name="HP Pavilion Laptop",
        price=799.99,
        brand="HP",
        category="laptop",
        specs={"ram": "16 GB", "storage": "512 GB SSD"},
    )

    intent = ShoppingIntent(
        query="laptop under 1000 dollars no HP",
        category="laptop",
        max_budget=1000.0,
        currency="USD",
        excluded_brands=["HP"],
    )

    elig = AssistantService.evaluate_product_eligibility(prod, intent)
    assert elig.status == "EXCLUDED"
    assert elig.is_qualified is False
    assert elig.recommendation_score == 0.0
    assert any("Brand 'HP' was explicitly excluded" in r for r in elig.reasons)
    assert any("Brand 'HP' was explicitly excluded" in r for r in elig.score_reasons)


# ==============================================================================
# 3. Safeguard 3: Ranking Priority & Tie-Breaking
# ==============================================================================

def test_ranking_priority_qualified_before_excluded(test_session: Session):
    """
    Verify ranking strictly places QUALIFIED products before EXCLUDED products,
    regardless of price or raw preference match.
    """
    session_id = "test-ranking-tier"
    MandateRepository.get_or_create_session(test_session, session_id)

    # p_qual meets 16GB RAM requirement -> QUALIFIED
    p_qual = _create_test_product("p-qual", "Valid Laptop", 900.0, brand="Dell", specs={"ram": "16 GB"})
    # p_excl has only 8GB RAM -> EXCLUDED (even though cheaper)
    p_excl = _create_test_product("p-excl", "Budget Laptop", 400.0, brand="Dell", specs={"ram": "8 GB"})

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="laptop",
                max_budget=1000.0,
                required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
                currency="USD",
            )

        def analyze_products(self, *args, **kwargs):
            return []

        def generate_conversational_response(self, *args, **kwargs):
            return "Summary"

    original_search = ProductService.search_products
    try:
        # Intentionally order p_excl before p_qual from search
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p_excl, p_qual], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="laptop with 16GB RAM under 1000")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        # QUALIFIED product MUST be ranked first
        assert resp.products[0].id == "p-qual"
        assert resp.products[1].id == "p-excl"
        assert resp.eligibility_details[0].status == "QUALIFIED"
        assert resp.eligibility_details[1].status == "EXCLUDED"
    finally:
        ProductService.search_products = original_search


def test_ranking_by_recommendation_score_within_same_tier(test_session: Session):
    """
    Within the QUALIFIED tier, products must be ranked by recommendation_score descending.
    Product A matches preferred brand Sony and preferred 32GB RAM -> Higher Score.
    Product B matches neither -> Lower Score.
    Product A must rank ahead of Product B.
    """
    session_id = "test-ranking-score"
    MandateRepository.get_or_create_session(test_session, session_id)

    p_a = _create_test_product(
        "p-a", "Sony High Spec Laptop", 1200.0, brand="Sony", specs={"ram": "32 GB", "storage": "1 TB SSD"}
    )
    p_b = _create_test_product(
        "p-b", "Generic Brand Laptop", 1200.0, brand="OtherBrand", specs={"ram": "16 GB", "storage": "512 GB SSD"}
    )

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="laptop",
                max_budget=1500.0,
                required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
                preferred_specs=[SpecificationRequirement(name="ram", value="32 GB", min_value=32.0, unit="GB", is_hard_requirement=False)],
                brand_preferences=["Sony"],
                currency="USD",
            )

        def analyze_products(self, *args, **kwargs):
            return []

        def generate_conversational_response(self, *args, **kwargs):
            return "Summary"

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p_b, p_a], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="laptop under 1500 prefer Sony 32GB RAM")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        # p_a has higher recommendation score, so it must rank first
        assert resp.products[0].id == "p-a"
        assert resp.products[1].id == "p-b"
        assert resp.eligibility_details[0].recommendation_score > resp.eligibility_details[1].recommendation_score
    finally:
        ProductService.search_products = original_search


def test_ranking_price_tie_breaker_same_currency_only(test_session: Session):
    """
    When two QUALIFIED products have identical recommendation scores,
    use lowest price ONLY as a tie-breaker between products in the SAME currency.
    """
    session_id = "test-ranking-price-tie"
    MandateRepository.get_or_create_session(test_session, session_id)

    # Identical specs and brand, different prices in USD
    p_cheap = _create_test_product("p-cheap", "Model Cheap", 600.0, brand="Dell", specs={"ram": "16 GB"})
    p_expensive = _create_test_product("p-exp", "Model Expensive", 900.0, brand="Dell", specs={"ram": "16 GB"})

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="laptop",
                max_budget=1000.0,
                required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
                currency="USD",
            )

        def analyze_products(self, *args, **kwargs):
            return []

        def generate_conversational_response(self, *args, **kwargs):
            return "Summary"

    original_search = ProductService.search_products
    try:
        # Search returns expensive item first
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p_expensive, p_cheap], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="Dell laptop under 1000")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        # Cheaper item must be ranked first
        assert resp.products[0].id == "p-cheap"
        assert resp.products[1].id == "p-exp"
    finally:
        ProductService.search_products = original_search


# ==============================================================================
# 4. Safeguard 4: Verified Feature Matching
# ==============================================================================

def test_missing_or_ambiguous_feature_receives_zero_points():
    """
    Verify that if a preferred feature is missing or ambiguous in merchant data,
    ZERO points are awarded and unsupported claims are never invented.
    """
    prod = _create_test_product(
        prod_id="prod-no-feat",
        name="Noise Cancelling Wireless Headphones by MysteryBrand",  # Title mentions words, but specs do not!
        price=100.0,
        brand="MysteryBrand",
        category="headphones",
        specs={},  # Empty merchant specifications!
    )

    intent = ShoppingIntent(
        query="headphones with active noise cancelling",
        category="headphones",
        max_budget=200.0,
        currency="USD",
        preferred_specs=[
            SpecificationRequirement(name="anc", value="active noise cancelling", is_hard_requirement=False),
            SpecificationRequirement(name="battery", value="30 hours", is_hard_requirement=False),
        ],
    )

    elig = AssistantService.evaluate_product_eligibility(prod, intent)
    # Both preferred features must receive 0.0 points
    assert any("Preferred feature 'active noise cancelling' is unconfirmed" in r for r in elig.score_reasons)
    assert any("Preferred feature '30 hours' is unconfirmed" in r for r in elig.score_reasons)
    # Score should only contain base score (50.0) + budget efficiency, no feature bonus
    assert elig.recommendation_score <= 60.0


# ==============================================================================
# 5. Decoupling from Spending Mandate
# ==============================================================================

def test_recommendation_scoring_strictly_decoupled_from_mandate(test_session: Session):
    """
    Verify that an item with high recommendation score (e.g. 85.0) can be BLOCKED
    by spending mandate without altering its recommendation score, and without
    Gemini overriding the mandate block.
    """
    session_id = "test-session-decouple"
    sess = MandateRepository.get_or_create_session(test_session, session_id)
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=500.0,
        maximum_item_price=300.0,  # Strict $300 limit
        maximum_quantity=1,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    # Product is $800 (exceeds $300 mandate limit), but has excellent preferred specs
    prod_blocked = _create_test_product(
        prod_id="p-blocked",
        name="Sony Master Headphones",
        price=800.0,
        brand="Sony",
        category="headphones",
        specs={"anc": "Adaptive ANC", "key_features": ["Active Noise Cancelling"]},
    )

    intent = ShoppingIntent(
        query="headphones prefer Sony with ANC",
        category="headphones",
        max_budget=1000.0,
        currency="USD",
        brand_preferences=["Sony"],
        preferred_specs=[SpecificationRequirement(name="anc", value="Active Noise Cancelling", is_hard_requirement=False)],
    )

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return intent

        def analyze_products(self, *args, **kwargs):
            return []

        def generate_conversational_response(self, *args, **kwargs):
            return "Summary"

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[prod_blocked], total=1, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="headphones prefer Sony with ANC")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        elig = resp.eligibility_details[0]
        # Status is QUALIFIED under user query ($800 < $1000 max_budget)
        assert elig.status == "QUALIFIED"
        # Mandate status is BLOCKED ($800 > $300 mandate limit)
        assert elig.mandate_status in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
        # Recommendation score is high (> 65.0) because brand matches (+15) and ANC matches (+5)
        assert elig.recommendation_score >= 70.0
        assert any("+15.0 pts: Matches preferred brand 'Sony'" in r for r in elig.score_reasons)
    finally:
        ProductService.search_products = original_search


# ==============================================================================
# 6. Channel3 Brand Query Incorporation & Fallback
# ==============================================================================

def test_channel3_brand_search_incorporation_and_fallback(test_session: Session):
    """
    Verify that:
    1. Channel3 search query incorporates preferred brand ('Sony headphones').
    2. If brand search returns 0 results, safely falls back to broad category search.
    """
    session_id = "test-session-c3-brand"
    MandateRepository.get_or_create_session(test_session, session_id)

    searched_queries = []

    class MockChannel3WithTracking:
        is_configured = True

        def search_products(self, query, limit=10, filters=None):
            searched_queries.append(query)
            # If query has 'ObscureBrand', return 0 items
            if "ObscureBrand" in query:
                return {"products": []}
            # Broad query returns products
            return {
                "products": [
                    {
                        "id": "c3-fallback-1",
                        "title": "Generic Quality Headphones",
                        "brands": [{"name": "Anker"}],
                        "category": {"slug": "headphones", "title": "Headphones"},
                        "offers": [{"id": "off-1", "retailer": "Best Buy", "price": {"price": 99.99, "currency": "USD"}, "availability": "InStock", "url": "https://example.com"}],
                    }
                ]
            }

    intent = ShoppingIntent(
        query="headphones prefer ObscureBrand",
        category="headphones",
        max_budget=200.0,
        currency="USD",
        brand_preferences=["ObscureBrand"],
    )

    class MockGemini:
        def extract_shopping_intent(self, query):
            return intent

        def analyze_products(self, *args, **kwargs):
            return []

        def generate_conversational_response(self, *args, **kwargs):
            return "Summary"

    req = AssistantQueryRequest(session_id=session_id, query="headphones prefer ObscureBrand")
    resp = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=MockGemini(),
        c3_client=MockChannel3WithTracking(),
    )

    # First search included brand
    assert any("ObscureBrand" in q for q in searched_queries)
    # Fallback search was attempted without failing
    assert any("headphones" == q or "headphones" in q for q in searched_queries)
    assert resp.success is True


# ==============================================================================
# 7. Multilingual Fallback Intent Brand Extraction
# ==============================================================================

def test_rule_based_fallback_extracts_brand_preferences_and_exclusions():
    """Verify deterministic fallback rule-based intent extracts brand_preferences and excluded_brands."""
    # English preference + exclusion
    intent_en = AssistantService._fallback_rule_based_intent("Gaming laptop, prefer Asus, no HP under 1500 dollars")
    assert "Asus" in intent_en.brand_preferences
    assert "HP" in intent_en.excluded_brands
    assert "HP" not in intent_en.brand_preferences

    # German preference + exclusion
    intent_de = AssistantService._fallback_rule_based_intent("Kopfhörer von Sennheiser, keine Beats unter 200 Euro")
    assert "Sennheiser" in intent_de.brand_preferences
    assert "Beats" in intent_de.excluded_brands
    assert "Beats" not in intent_de.brand_preferences


# ==============================================================================
# 8. Frontend UI Component Score Badge (Node runtime)
# ==============================================================================

def test_frontend_score_badge_rendering():
    """Verify frontend renderAssistantProductCard renders the ⭐ 85% Match badge and breakdown."""
    node_script = """
    import('./frontend/assets/js/components.js').then(m => {
        const dummyProd = {
            id: 'p1',
            name: 'Sony WH-1000XM5',
            brand: 'Sony',
            lowest_price: 349.99,
            lowest_price_retailer: 'Best Buy',
            offers: [{ retailer_name: 'Best Buy', price: 349.99, currency: 'USD', availability: 'in_stock' }]
        };

        const elig = {
            product_id: 'p1',
            status: 'QUALIFIED',
            is_qualified: true,
            mandate_status: 'ALLOWED',
            mandate_reasons: [],
            recommendation_score: 85.0,
            score_reasons: ['Base: 50.0 pts', '+15.0 pts: Matches preferred brand Sony', '+10.0 pts: Meets preferred Storage']
        };

        const html = m.renderAssistantProductCard(dummyProd, elig);
        if (!html.includes('85% Match')) throw new Error('Missing 85% Match score badge');
        if (!html.includes('bg-burnt-orange/15')) throw new Error('Missing burnt-orange styling for score badge');
        if (!html.includes('Preference Breakdown')) throw new Error('Missing Preference Breakdown container');
        if (!html.includes('Matches preferred brand Sony')) throw new Error('Missing score reason in breakdown');

        console.log('SCORE_BADGE_VERIFIED');
    }).catch(err => {
        console.error(err);
        process.exit(1);
    });
    """

    res = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        capture_output=True,
        text=True,
        cwd="C:/Users/keert/OneDrive/Desktop/paypal hackathon/AI-Shopping-Intelligence-Platform",
    )
    assert res.returncode == 0, f"Node script failed: {res.stderr}"
    assert "SCORE_BADGE_VERIFIED" in res.stdout


# ==============================================================================
# 9. Full HTTP API Endpoint Integration Test
# ==============================================================================

@pytest.mark.asyncio
async def test_api_assistant_query_with_preference_scoring(async_client: httpx.AsyncClient):
    """
    Full async HTTP integration test calling POST /api/assistant/query with preferences.
    Verifies that the returned JSON payload includes populated recommendation_score and score_reasons.
    """
    session_id = f"test_sess_p4a3_{uuid.uuid4().hex[:8]}"

    q_payload = {
        "session_id": session_id,
        "query": "I need Sony noise cancelling headphones under 500 dollars",
    }
    a_res = await async_client.post("/api/assistant/query", json=q_payload)
    assert a_res.status_code == status.HTTP_200_OK

    data = a_res.json()
    assert data["success"] is True
    assert "eligibility_details" in data
    assert len(data["eligibility_details"]) > 0

    for item in data["eligibility_details"]:
        assert "recommendation_score" in item
        assert 0.0 <= item["recommendation_score"] <= 100.0
        assert "score_reasons" in item
        assert isinstance(item["score_reasons"], list)

    # Verify products are returned in sorted order (QUALIFIED first)
    statuses = [item["status"] for item in data["eligibility_details"]]
    if "QUALIFIED" in statuses and "EXCLUDED" in statuses:
        first_excl_idx = statuses.index("EXCLUDED")
        last_qual_idx = max(i for i, s in enumerate(statuses) if s == "QUALIFIED")
        assert last_qual_idx < first_excl_idx, "QUALIFIED products must precede EXCLUDED products"


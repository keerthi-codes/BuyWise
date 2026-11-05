"""
AI-Powered Multilingual Shopping Intelligence Platform
Phase 4A.4A: Comparison Integration & Grounded AI Conversational Synthesis Tests.

Validates:
1. Comparison Matrix Integration into Assistant Query:
   - When 2 or more products are retrieved, comparison_matrix, price_summary, and comparison_explanation are populated.
2. Grounded Gemini Conversational Evidence:
   - Conversational synthesis receives verified specifications, recommendation scores, score reasons,
     qualification statuses, mandate statuses, and comparison matrix.
3. Spec Grounding Invariant:
   - Missing specifications remain strictly 'Not Specified' and are never invented.
4. Dedicated Compare API Endpoint Integration:
   - GET /api/products/compare returns populated ai_explanation.
5. Strict Currency Separation in Multi-Product Comparisons:
   - Mixed-currency comparisons trigger warnings and withhold cross-currency numerical price comparison.
6. Deterministic Fallback Comparison Explanation:
   - When Gemini is unconfigured or raises an error, deterministic comparison explanation is generated cleanly.
7. Backend Authority Non-Negotiable Boundary:
   - Gemini comparison insights cannot alter eligibility, mandate decisions, scores, or retailer data.
8. Frontend Comparison UI Rendering:
   - Node runtime tests verifying renderComparisonAIInsights and renderAssistantMessage.
"""

from datetime import datetime, timezone
import json
import subprocess
import uuid
from unittest.mock import MagicMock, patch
import pytest
import httpx
from fastapi import status
from sqlalchemy.orm import Session

from backend.app.schemas.assistant import (
    AssistantQueryRequest,
    AssistantQueryResponse,
    ComparisonExplanation,
    ProductEligibilityItem,
    ProductIntelligence,
    ShoppingIntent,
    SpecificationRequirement,
)
from backend.app.schemas.product import (
    ProductResponse,
    ProductCompareItem,
    ProductComparisonResponse,
    RetailerOfferResponse,
    ProductListResponse,
)
from backend.app.services.assistant_service import AssistantService
from backend.app.services.product_service import ProductService
from backend.app.services.gemini_client import GeminiClient, GeminiError
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.models.shopping import SpendingMandate, Product, RetailerOffer


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
# 1. Comparison Matrix Integration into Assistant Query
# ==============================================================================

def test_comparison_matrix_integrated_into_assistant_query(test_session: Session):
    """
    Verify that when 2 or more products are returned by the assistant,
    ProductService.compare_products() is executed and comparison_matrix,
    price_summary, and comparison_explanation are populated in AssistantQueryResponse.
    """
    session_id = "test-session-comp-int"
    MandateRepository.get_or_create_session(test_session, session_id)

    p1 = _create_test_product("p1", "Laptop Alpha", 800.0, brand="Dell", specs={"ram": "16 GB", "storage": "512 GB SSD"})
    p2 = _create_test_product("p2", "Laptop Beta", 950.0, brand="Lenovo", specs={"ram": "32 GB", "storage": "1 TB SSD"})

    intent = ShoppingIntent(
        query="laptops for programming under 1000",
        category="laptop",
        max_budget=1000.0,
        currency="USD",
    )

    class MockChannel3:
        is_configured = False

    class MockGemini:
        is_configured = True

        def extract_shopping_intent(self, query):
            return intent

        def analyze_products(self, *args, **kwargs):
            return []

        def explain_comparison(self, intent, comparison_data):
            return ComparisonExplanation(
                summary="Comparing Laptop Alpha and Laptop Beta.",
                performance_differences=["Laptop Beta has 32GB RAM vs 16GB in Laptop Alpha."],
                key_differences=["RAM: 32 GB vs 16 GB", "Storage: 1 TB vs 512 GB"],
                value_tradeoffs="Laptop Alpha is $150 cheaper, while Laptop Beta offers double the RAM.",
                best_choice_for_use_case="Laptop Beta for heavy programming",
                overall_winner="Laptop Beta",
                best_value_pick="Laptop Alpha",
            )

        def generate_conversational_response(self, *args, **kwargs):
            return "Found 2 matching options. Laptop Beta has superior RAM, while Laptop Alpha is more budget-friendly."

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p1, p2], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="laptops for programming under 1000")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        assert resp.success is True
        assert len(resp.products) == 2
        assert resp.comparison_matrix is not None
        assert "ram" in resp.comparison_matrix
        assert resp.comparison_matrix["ram"]["Laptop Alpha"] == "16 GB"
        assert resp.comparison_matrix["ram"]["Laptop Beta"] == "32 GB"

        assert resp.price_summary is not None
        assert resp.price_summary["is_multi_currency"] is False

        assert resp.comparison_explanation is not None
        assert resp.comparison_explanation.overall_winner == "Laptop Beta"
        assert resp.comparison_explanation.best_value_pick == "Laptop Alpha"
    finally:
        ProductService.search_products = original_search


# ==============================================================================
# 2. Grounded Gemini Evidence & Verified Specs Context
# ==============================================================================

def test_gemini_conversational_response_receives_verified_specs_scores_and_comparison(test_session: Session):
    """
    Verify that generate_conversational_response() receives top_products containing
    verified_specifications, recommendation_score, score_reasons, qualification_status,
    mandate_status, and comparison_context.
    """
    session_id = "test-session-specs-context"
    sess = MandateRepository.get_or_create_session(test_session, session_id)
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=1500.0,
        maximum_item_price=1000.0,
        maximum_quantity=1,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    p1 = _create_test_product("p-sony", "Sony ANC Headphones", 350.0, brand="Sony", specs={"anc": "Adaptive", "battery": "30h"})
    p2 = _create_test_product("p-bose", "Bose QC Headphones", 280.0, brand="Bose", specs={"anc": "QuietMode", "battery": "24h"})

    intent = ShoppingIntent(
        query="headphones prefer Sony with ANC",
        category="headphones",
        max_budget=500.0,
        currency="USD",
        brand_preferences=["Sony"],
    )

    captured_prompt_args = {}

    class MockGeminiCapturing:
        is_configured = True

        def extract_shopping_intent(self, query):
            return intent

        def analyze_products(self, *args, **kwargs):
            return []

        def explain_comparison(self, intent, comparison_data):
            return ComparisonExplanation(
                summary="Sony vs Bose comparison.",
                performance_differences=["Sony has 30h battery vs 24h on Bose."],
                value_tradeoffs="Bose is cheaper by $70.",
                best_choice_for_use_case="Sony ANC Headphones",
            )

        def generate_conversational_response(self, **kwargs):
            captured_prompt_args.update(kwargs)
            return "Conversational reply."

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p1, p2], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="headphones prefer Sony with ANC")
        AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGeminiCapturing(),
            c3_client=MagicMock(is_configured=False),
        )

        # Inspect products passed to generate_conversational_response
        passed_prods = captured_prompt_args.get("products", [])
        assert len(passed_prods) == 2

        sony_prod = next(p for p in passed_prods if p["name"] == "Sony ANC Headphones")
        assert "verified_specifications" in sony_prod or "specifications" in sony_prod
        assert "eligibility" in sony_prod
        elig = sony_prod["eligibility"]
        assert elig["status"] == "QUALIFIED"
        assert elig["mandate_status"] == "ALLOWED"
        assert elig["recommendation_score"] >= 65.0  # Base 50 + brand 15

        # Inspect comparison_context
        comp_ctx = captured_prompt_args.get("comparison_context")
        assert comp_ctx is not None
        assert "comparison_matrix" in comp_ctx
        assert "price_summary" in comp_ctx
        assert "comparison_explanation" in comp_ctx
    finally:
        ProductService.search_products = original_search


# ==============================================================================
# 3. Spec Grounding Invariant: Missing Specs Remain 'Not Specified'
# ==============================================================================

def test_missing_specifications_remain_not_specified(test_session: Session):
    """
    Verify that when comparing products with missing attributes, the comparison matrix
    authoritatively represents missing values as 'Not Specified' and never invents specs.
    """
    # Insert two test products directly into db
    p1 = Product(
        id=f"test-p1-{uuid.uuid4().hex[:6]}",
        name="Specs Full Laptop",
        brand="Asus",
        category="laptop",
        specifications={"ram": "16 GB", "storage": "512 GB SSD", "gpu": "RTX 4060"},
    )
    p2 = Product(
        id=f"test-p2-{uuid.uuid4().hex[:6]}",
        name="Specs Empty Laptop",
        brand="Asus",
        category="laptop",
        specifications={"ram": "16 GB"},  # Missing storage & gpu
    )
    test_session.add_all([p1, p2])
    test_session.commit()

    comp = ProductService.compare_products(test_session, [p1.id, p2.id], include_ai=False)
    matrix = comp.comparison_matrix

    assert "storage" in matrix
    assert matrix["storage"][p1.name] == "512 GB SSD"
    assert matrix["storage"][p2.name] == "Not Specified"

    assert "gpu" in matrix
    assert matrix["gpu"][p1.name] == "RTX 4060"
    assert matrix["gpu"][p2.name] == "Not Specified"


# ==============================================================================
# 4. Compare API Endpoint Enriched with AI Explanation
# ==============================================================================

@pytest.mark.asyncio
async def test_compare_endpoint_returns_ai_explanation(async_client: httpx.AsyncClient):
    """
    Verify GET /api/products/compare returns populated ai_explanation when include_ai=True.
    """
    # Use existing seeded catalog items (e.g. Acer Swift Go and Apple MacBook Air)
    res_list = await async_client.get("/api/products?page=1&page_size=3")
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) >= 2

    id1, id2 = items[0]["id"], items[1]["id"]
    res_comp = await async_client.get(f"/api/products/compare?ids={id1}&ids={id2}&include_ai=true")
    assert res_comp.status_code == 200

    comp_data = res_comp.json()
    assert comp_data["compared_count"] == 2
    assert "comparison_matrix" in comp_data
    assert "price_summary" in comp_data
    assert "ai_explanation" in comp_data
    assert comp_data["ai_explanation"] is not None
    assert "summary" in comp_data["ai_explanation"]
    assert "best_choice_for_use_case" in comp_data["ai_explanation"]


# ==============================================================================
# 5. Strict Multi-Currency Separation in Multi-Product Comparisons
# ==============================================================================

def test_multi_currency_comparison_safeguard(test_session: Session):
    """
    Verify that comparing products with mixed currencies (e.g. USD vs EUR)
    sets is_multi_currency=True, flags currency_consistency_warning,
    and disables direct numerical price comparisons.
    """
    now = datetime.now(timezone.utc)
    p_usd = Product(
        id=f"p-usd-{uuid.uuid4().hex[:6]}",
        name="USD Gadget",
        brand="BrandA",
        category="electronics",
        specifications={"color": "Black"},
    )
    p_eur = Product(
        id=f"p-eur-{uuid.uuid4().hex[:6]}",
        name="EUR Gadget",
        brand="BrandB",
        category="electronics",
        specifications={"color": "White"},
    )
    test_session.add_all([p_usd, p_eur])
    test_session.flush()

    o_usd = RetailerOffer(
        id=f"off-usd-{uuid.uuid4().hex[:6]}",
        product_id=p_usd.id,
        retailer_name="Amazon US",
        product_url="https://example.com/us",
        price=100.0,
        currency="USD",
        availability="in_stock",
        created_at=now,
    )
    o_eur = RetailerOffer(
        id=f"off-eur-{uuid.uuid4().hex[:6]}",
        product_id=p_eur.id,
        retailer_name="Amazon DE",
        product_url="https://example.com/de",
        price=90.0,
        currency="EUR",
        availability="in_stock",
        created_at=now,
    )
    test_session.add_all([o_usd, o_eur])
    test_session.commit()

    comp = ProductService.compare_products(test_session, [p_usd.id, p_eur.id], include_ai=True)
    assert comp.price_summary["is_multi_currency"] is True
    assert comp.price_summary["currency"] == "MIXED"
    assert comp.price_summary["cheapest_product"] is None
    assert comp.price_summary["warning"] is not None

    expl = comp.ai_explanation
    assert expl is not None
    assert expl.currency_consistency_warning is not None
    assert "cross-currency" in expl.currency_consistency_warning.lower() or "multiple currencies" in expl.currency_consistency_warning.lower()


# ==============================================================================
# 6. Deterministic Fallback Comparison Explanation
# ==============================================================================

def test_deterministic_fallback_comparison_explanation_when_gemini_unavailable():
    """
    Verify that build_fallback_comparison_explanation() generates a complete,
    grounded ComparisonExplanation with zero LLM dependency.
    """
    now = datetime.now(timezone.utc)
    p1 = ProductCompareItem(
        id="item-1",
        name="Laptop 1",
        brand="BrandA",
        category="laptop",
        image_url=None,
        lowest_price=500.0,
        currency="USD",
        specifications={"ram": "8 GB", "storage": "256 GB"},
        offers=[],
        best_offer_retailer="Best Buy",
        overall_availability="IN_STOCK",
        is_synthetic=True,
        provider="synthetic",
    )
    p2 = ProductCompareItem(
        id="item-2",
        name="Laptop 2",
        brand="BrandB",
        category="laptop",
        image_url=None,
        lowest_price=800.0,
        currency="USD",
        specifications={"ram": "16 GB", "storage": "Not Specified"},
        offers=[],
        best_offer_retailer="Amazon",
        overall_availability="IN_STOCK",
        is_synthetic=True,
        provider="synthetic",
    )

    matrix = {
        "ram": {"Laptop 1": "8 GB", "Laptop 2": "16 GB"},
        "storage": {"Laptop 1": "256 GB", "Laptop 2": "Not Specified"},
    }

    price_summary = {
        "is_multi_currency": False,
        "currency": "USD",
        "cheapest_product": "Laptop 1",
        "cheapest_price": 500.0,
        "cheapest_retailer": "Best Buy",
    }

    expl = ProductService.build_fallback_comparison_explanation([p1, p2], matrix, price_summary)
    assert isinstance(expl, ComparisonExplanation)
    assert expl.best_value_pick == "Laptop 1"
    assert "Lowest price option is Laptop 1" in " ".join(expl.tradeoffs)
    assert any("Storage is not confirmed" in m for m in expl.missing_specs_notes)
    assert any("RAM: Laptop 1 (8 GB); Laptop 2 (16 GB)" in d for d in expl.performance_differences)


# ==============================================================================
# 7. Backend Authority Non-Negotiable Boundary
# ==============================================================================

def test_gemini_comparison_cannot_override_backend_authority(test_session: Session):
    """
    Verify that even if Gemini returns a comparison explanation favoring an EXCLUDED
    or BLOCKED product, the backend preserves deterministic qualification status,
    mandate block status, and recommendation scores.
    """
    session_id = "test-session-authority-boundary"
    sess = MandateRepository.get_or_create_session(test_session, session_id)
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=500.0,
        maximum_item_price=300.0,
        maximum_quantity=1,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    # Product is $800 (exceeds $300 limit)
    p_blocked = _create_test_product("p-block", "Expensive Laptop", 800.0, brand="Dell", specs={"ram": "32 GB"})
    p_allowed = _create_test_product("p-allow", "Affordable Laptop", 250.0, brand="Dell", specs={"ram": "16 GB"})

    intent = ShoppingIntent(query="laptops under 1000", category="laptop", max_budget=1000.0, currency="USD")

    class MockGeminiClaimingWinner:
        is_configured = True

        def extract_shopping_intent(self, query):
            return intent

        def analyze_products(self, *args, **kwargs):
            return []

        def explain_comparison(self, intent, comparison_data):
            # Gemini claims the $800 blocked product is the overall winner
            return ComparisonExplanation(
                summary="Comparing laptops.",
                best_choice_for_use_case="Expensive Laptop",
                overall_winner="Expensive Laptop",
            )

        def generate_conversational_response(self, *args, **kwargs):
            return "I recommend the Expensive Laptop."

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[p_blocked, p_allowed], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="laptops under 1000")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGeminiClaimingWinner(),
            c3_client=MagicMock(is_configured=False),
        )

        elig_map = {e.product_id: e for e in resp.eligibility_details}
        # p_blocked must remain BLOCKED despite Gemini's praise
        assert elig_map["p-block"].mandate_status in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
        assert elig_map["p-allow"].mandate_status == "ALLOWED"

        # Ranking priority: p-allow is ALLOWED and QUALIFIED, so it ranks first or holds priority
        assert resp.mandate_evaluation["decision"] in ("ALLOWED", "BLOCKED")
        assert ("p-block" in resp.mandate_evaluation.get("warning_override_product_ids", []) or "p-block" in resp.mandate_evaluation["blocked_product_ids"])
        assert "p-allow" in resp.mandate_evaluation["allowed_product_ids"]
    finally:
        ProductService.search_products = original_search


# ==============================================================================
# 8. Frontend UI Component Rendering (via Node runtime)
# ==============================================================================

def test_frontend_comparison_ai_insights_rendering():
    """Verify frontend renderComparisonAIInsights renders winner, differences, and trade-offs."""
    node_script = """
    import('./frontend/assets/js/components.js').then(m => {
        const dummyExplanation = {
            summary: 'Grounded comparison between Sony WH-1000XM5 and Bose QuietComfort 45.',
            overall_winner: 'Sony WH-1000XM5',
            best_value_pick: 'Bose QuietComfort 45',
            performance_differences: ['Sony has 30h battery life vs 24h on Bose', 'Sony features LDAC high-res codec'],
            value_tradeoffs: 'Bose offers a $70 savings while Sony provides higher fidelity audio.',
            missing_specs_notes: ['Water resistance is not confirmed in merchant specifications for Bose.'],
            currency_consistency_warning: null
        };

        const html = m.renderComparisonAIInsights(dummyExplanation, false);
        if (!html.includes('AI Technical Comparison')) throw new Error('Missing title');
        if (!html.includes('Top Pick:')) throw new Error('Missing Top Pick banner');
        if (!html.includes('Sony WH-1000XM5')) throw new Error('Missing winner name');
        if (!html.includes('Best Value:')) throw new Error('Missing Best Value banner');
        if (!html.includes('Key Technical Differences:')) throw new Error('Missing Key Technical Differences header');
        if (!html.includes('Sony has 30h battery life')) throw new Error('Missing performance diff point');
        if (!html.includes('Price Spread')) throw new Error('Missing Price Spread header');

        console.log('COMPARISON_INSIGHTS_VERIFIED');
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
    assert "COMPARISON_INSIGHTS_VERIFIED" in res.stdout


def test_frontend_assistant_renders_comparison_insights_when_present():
    """Verify renderAssistantMessage renders comparison explanation inside the stream when available."""
    node_script = """
    import('./frontend/assets/js/components.js').then(m => {
        const dummyData = {
            assistant_message: 'Here are two excellent options.',
            products: [
                { id: 'p1', name: 'Laptop A', lowest_price: 900.0, brand: 'Dell', offers: [{ price: 900.0, currency: 'USD', retailer_name: 'Best Buy' }] },
                { id: 'p2', name: 'Laptop B', lowest_price: 1100.0, brand: 'Apple', offers: [{ price: 1100.0, currency: 'USD', retailer_name: 'Apple' }] }
            ],
            comparison_explanation: {
                summary: 'Side-by-side spec comparison.',
                overall_winner: 'Laptop B',
                performance_differences: ['Laptop B has M2 chip vs Intel i7 on Laptop A']
            }
        };

        const html = m.renderAssistantMessage(dummyData);
        if (!html.includes('AI Technical Comparison')) throw new Error('Missing comparison insights in assistant message');
        if (!html.includes('Top Pick:')) throw new Error('Missing Top Pick pill');
        if (!html.includes('Laptop B has M2 chip')) throw new Error('Missing performance difference point');

        console.log('ASSISTANT_COMPARISON_STREAM_VERIFIED');
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
    assert "ASSISTANT_COMPARISON_STREAM_VERIFIED" in res.stdout

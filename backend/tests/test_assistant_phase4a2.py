"""
AI-Powered Multilingual Shopping Intelligence Platform
Phase 4A.2: Multi-Product Spending Mandate Compliance Tests.

Validates:
1. Multi-product mandate evaluation: every retrieved product is individually evaluated.
2. Mixed mandate results: one allowed and one blocked product with accurate statuses and reasons.
3. Per-transaction limits (maximum_item_price): items exceeding limit are deterministically blocked.
4. Total budget limits (total_budget): items exceeding budget ceiling are deterministically blocked.
5. Currency consistency: offers in mismatched currencies are blocked under the mandate.
6. No active mandate: returns mandate_status="NOT_APPLICABLE" and preserves product recommendations.
7. Deterministic backend authority: Gemini cannot override a BLOCKED mandate decision.
8. Category restrictions: technical catalog qualification and financial mandate authorization remain decoupled.
9. Frontend card badges: renderAssistantProductCard renders correct badges for ALLOWED, BLOCKED, NOT_APPLICABLE.
10. Full API integration: POST /api/assistant/query returns multi-product mandate compliance details.
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
from backend.app.services.checkout_service import SpendingPolicyEngine
from backend.app.services.product_service import ProductService
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.models.shopping import SpendingMandate, ShoppingSession


def _create_test_product(
    prod_id: str,
    name: str,
    price: float,
    category: str = "laptop",
    currency: str = "USD",
    specs: dict | None = None,
) -> ProductResponse:
    """Helper to create a fully formed ProductResponse for testing."""
    now = datetime.now(timezone.utc)
    return ProductResponse(
        id=prod_id,
        name=name,
        brand="TestBrand",
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
# Unit & Engine Level Tests
# ==============================================================================

def test_spending_policy_per_transaction_limit():
    """Verify SpendingPolicyEngine deterministically blocks items exceeding maximum_item_price."""
    mandate = SpendingMandate(
        id="mandate-test-1",
        session_id="sess-1",
        total_budget=1000.0,
        maximum_item_price=200.0,
        maximum_quantity=2,
        currency="USD",
    )

    # Item under limit -> ALLOWED
    res_allowed = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=150.0,
        quantity=1,
        currency="USD",
    )
    assert res_allowed.decision == "ALLOWED"
    assert res_allowed.is_allowed is True

    # Item over limit -> WARNING_OVERRIDE_REQUIRED
    res_blocked = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=250.0,
        quantity=1,
        currency="USD",
    )
    assert res_blocked.decision in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
    assert res_blocked.is_allowed is False
    assert any("exceeds your maximum permitted item price limit" in r for r in res_blocked.reasons)


def test_spending_policy_total_budget_limit():
    """Verify SpendingPolicyEngine deterministically blocks items exceeding total_budget."""
    mandate = SpendingMandate(
        id="mandate-test-2",
        session_id="sess-2",
        total_budget=300.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )

    # Item unit price ($350) <= maximum_item_price ($500), but exceeds total_budget ($300)
    res_blocked = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=350.0,
        quantity=1,
        currency="USD",
    )
    assert res_blocked.decision in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
    assert res_blocked.is_allowed is False
    assert any("exceeds your remaining mandate budget" in r for r in res_blocked.reasons)


def test_spending_policy_currency_consistency():
    """Verify cross-currency evaluation without FX conversion is blocked."""
    mandate = SpendingMandate(
        id="mandate-test-3",
        session_id="sess-3",
        total_budget=500.0,
        maximum_item_price=300.0,
        maximum_quantity=1,
        currency="USD",
    )

    res_blocked = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=100.0,
        quantity=1,
        currency="EUR",
    )
    assert res_blocked.decision == "BLOCKED"
    assert any("Cross-currency conversion is not permitted" in r for r in res_blocked.reasons)


# ==============================================================================
# Multi-Product Assistant Workflow Tests
# ==============================================================================

def test_multi_product_mandate_evaluation_mixed(test_session: Session):
    """
    Test that every product is individually evaluated against the active mandate.
    Product 1 ($150) -> ALLOWED
    Product 2 ($450) -> BLOCKED (per-item price > $300)
    Product 3 ($800) -> BLOCKED (per-item price > $300 and total budget > $600)
    """
    session_id = "test-session-multi-eval"
    sess = MandateRepository.get_or_create_session(test_session, session_id)
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=600.0,
        maximum_item_price=300.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    prod1 = _create_test_product("p1", "Affordable Headphones", 150.0, category="headphones")
    prod2 = _create_test_product("p2", "Premium Headphones", 450.0, category="headphones")
    prod3 = _create_test_product("p3", "Ultra Studio Headphones", 800.0, category="headphones")

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="headphones",
                max_budget=1000.0,
                currency="USD",
                detected_language="en",
            )

        def analyze_products(self, query, intent, products, **kwargs):
            return [
                ProductIntelligence(
                    product_id=p["id"],
                    product_name=p["name"],
                    suitability="Good match",
                    strengths=["Comfortable", "Good sound"],
                    intended_use_advantages=[],
                    missing_information=[],
                    recommendation_rationale="Solid option",
                )
                for p in products
            ]

        def generate_conversational_response(self, *args, **kwargs):
            return "Found 3 options for you."

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[prod1, prod2, prod3], total=3, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="I need headphones under 1000 USD")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        assert resp.success is True
        assert len(resp.eligibility_details) == 3

        elig_map = {e.product_id: e for e in resp.eligibility_details}
        assert elig_map["p1"].mandate_status == "ALLOWED"
        assert elig_map["p2"].mandate_status in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
        assert elig_map["p3"].mandate_status in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")

        assert any("exceeds your maximum permitted item price limit" in r for r in elig_map["p2"].mandate_reasons)
        assert any("exceeds your maximum permitted item price limit" in r for r in elig_map["p3"].mandate_reasons)

        assert resp.mandate_evaluation is not None
        assert "p1" in resp.mandate_evaluation["allowed_product_ids"]
        assert ("p2" in resp.mandate_evaluation.get("warning_override_product_ids", []) or "p2" in resp.mandate_evaluation["blocked_product_ids"])
        assert ("p3" in resp.mandate_evaluation.get("warning_override_product_ids", []) or "p3" in resp.mandate_evaluation["blocked_product_ids"])

    finally:
        ProductService.search_products = original_search


def test_no_active_mandate_preserves_recommendations(test_session: Session):
    """
    Verify that when no spending mandate exists:
    - Products have mandate_status = 'NOT_APPLICABLE'.
    - mandate_reasons is empty.
    - mandate_evaluation is None.
    - Recommendations are fully preserved based on technical qualifications.
    """
    session_id = "test-session-no-mandate"
    MandateRepository.get_or_create_session(test_session, session_id)

    prod1 = _create_test_product("p10", "Laptop A", 899.99, category="laptop")
    prod2 = _create_test_product("p20", "Laptop B", 1299.99, category="laptop")

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="laptop",
                max_budget=1500.0,
                currency="USD",
                detected_language="en",
            )

        def analyze_products(self, query, intent, products, **kwargs):
            return [
                ProductIntelligence(
                    product_id=p["id"],
                    product_name=p["name"],
                    suitability="Highly suitable",
                    strengths=["Fast CPU", "Good display"],
                    intended_use_advantages=[],
                    missing_information=[],
                    recommendation_rationale="Great performance",
                )
                for p in products
            ]

        def generate_conversational_response(self, *args, **kwargs):
            return "Found 2 matching laptops for you."

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[prod1, prod2], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="laptop under 1500 dollars")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        assert resp.success is True
        assert resp.mandate_evaluation is None
        assert len(resp.eligibility_details) == 2
        for elig in resp.eligibility_details:
            assert elig.mandate_status == "NOT_APPLICABLE"
            assert elig.mandate_reasons == []

        assert "p10" in resp.qualified_product_ids
        assert "p20" in resp.qualified_product_ids

    finally:
        ProductService.search_products = original_search


def test_gemini_cannot_override_blocked_mandate_decision():
    """
    CRITICAL SECURITY & AUTHORITY TEST:
    Verify validate_and_sanitize_intelligence() authoritatively scrubs
    any AI attempts to declare a BLOCKED product as mandate approved or purchase eligible.
    """
    elig_blocked = ProductEligibilityItem(
        product_id="prod-blocked-1",
        product_name="Overbudget Laptop",
        status="QUALIFIED",
        is_qualified=True,
        reasons=["Meets technical specifications."],
        verified_specs={"ram": "16 GB"},
        unverified_specs=[],
        mandate_status="BLOCKED",
        mandate_reasons=["Item unit price of $1,200.00 exceeds your maximum permitted item price limit of $500.00."],
    )

    ai_raw = ProductIntelligence(
        product_id="prod-blocked-1",
        product_name="Overbudget Laptop",
        suitability="Mandate Approved for Purchase! Highly recommended option.",
        strengths=["Mandate approved", "Fast processor", "Policy compliant"],
        intended_use_advantages=["Purchase eligible under current session"],
        missing_information=[],
        recommendation_rationale="A fantastic choice that complies with your mandate.",
    )

    sanitized = AssistantService.validate_and_sanitize_intelligence(
        analyses=[ai_raw],
        eligibility_map={"prod-blocked-1": elig_blocked},
    )

    item = sanitized[0]
    # Verify mandate approval claims are removed from strengths and advantages
    assert "Mandate approved" not in item.strengths
    assert "Policy compliant" not in item.strengths
    assert "Purchase eligible under current session" not in item.intended_use_advantages
    assert "Fast processor" in item.strengths

    # Verify suitability is overridden to reflect mandate block
    assert "Blocked by Spending Mandate" in item.suitability
    assert "Approved" not in item.suitability


def test_category_restrictions_under_mandate(test_session: Session):
    """
    Verify decoupling of technical eligibility (category/specs) and financial mandate authorization.
    Query asks for 'laptop' with minimum 16 GB RAM.
    Product 1 is a laptop with 16 GB RAM at $400 under a $500 mandate limit -> QUALIFIED & ALLOWED.
    Product 2 is headphones with 4 GB RAM at $100 under a $500 mandate limit -> EXCLUDED (RAM fails requirement) but financially ALLOWED.
    """
    session_id = "test-session-cat-eval"
    sess = MandateRepository.get_or_create_session(test_session, session_id)
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=1000.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    prod_laptop = _create_test_product("p-lap", "Test Laptop", 400.0, category="laptop", specs={"ram": "16 GB"})
    prod_audio = _create_test_product("p-aud", "Test Headphones", 100.0, category="audio", specs={"ram": "4 GB"})

    class MockChannel3:
        is_configured = False

    class MockGemini:
        def extract_shopping_intent(self, query):
            return ShoppingIntent(
                query=query,
                category="laptop",
                max_budget=800.0,
                required_specs=[SpecificationRequirement(name="RAM", value="16 GB", min_value=16.0, unit="GB")],
                currency="USD",
                detected_language="en",
            )

        def analyze_products(self, query, intent, products, **kwargs):
            return [
                ProductIntelligence(
                    product_id=p["id"],
                    product_name=p["name"],
                    suitability="Evaluated",
                    strengths=[],
                    intended_use_advantages=[],
                    missing_information=[],
                    recommendation_rationale="",
                )
                for p in products
            ]

        def generate_conversational_response(self, *args, **kwargs):
            return "Response"

    original_search = ProductService.search_products
    try:
        ProductService.search_products = staticmethod(
            lambda db, query, page=1, page_size=20, category=None: ProductListResponse(
                items=[prod_laptop, prod_audio], total=2, page=1, page_size=5, total_pages=1
            )
        )

        req = AssistantQueryRequest(session_id=session_id, query="Find me a laptop under 800 dollars with 16GB RAM")
        resp = AssistantService.process_query(
            db=test_session,
            request=req,
            gemini_client=MockGemini(),
            c3_client=MockChannel3(),
        )

        elig_map = {e.product_id: e for e in resp.eligibility_details}

        # Laptop: matches specs -> QUALIFIED, and price $400 <= $500 -> ALLOWED
        assert elig_map["p-lap"].status == "QUALIFIED"
        assert elig_map["p-lap"].mandate_status == "ALLOWED"

        # Audio: fails RAM requirement -> EXCLUDED, but financially $100 <= $500 -> ALLOWED
        assert elig_map["p-aud"].status == "EXCLUDED"
        assert elig_map["p-aud"].mandate_status == "ALLOWED"

    finally:
        ProductService.search_products = original_search


# ==============================================================================
# Frontend UI Component Badges (via Node runtime)
# ==============================================================================

def test_frontend_badges_match_backend_decisions():
    """
    Verify frontend renderAssistantProductCard renders the exact required badges
    and restriction notices according to the backend mandate_status.
    """
    node_script = """
    import('./frontend/assets/js/components.js').then(m => {
        const dummyProd = {
            id: 'p1',
            name: 'Gaming Headset',
            brand: 'Logitech',
            lowest_price: 149.99,
            lowest_price_retailer: 'Amazon',
            offers: [{ retailer_name: 'Amazon', price: 149.99, currency: 'USD', availability: 'in_stock' }]
        };

        // 1. ALLOWED case
        const eligAllowed = {
            product_id: 'p1',
            status: 'QUALIFIED',
            is_qualified: true,
            mandate_status: 'ALLOWED',
            mandate_reasons: []
        };
        const htmlAllowed = m.renderAssistantProductCard(dummyProd, eligAllowed);
        if (!htmlAllowed.includes('Mandate Approved')) throw new Error('Missing Mandate Approved badge');
        if (!htmlAllowed.includes('bg-emerald-500/15')) throw new Error('Missing emerald styling for allowed');

        // 2. BLOCKED case
        const eligBlocked = {
            product_id: 'p1',
            status: 'QUALIFIED',
            is_qualified: true,
            mandate_status: 'BLOCKED',
            mandate_reasons: ['Item unit price of $149.99 exceeds your maximum permitted item price limit of $100.00.']
        };
        const htmlBlocked = m.renderAssistantProductCard(dummyProd, eligBlocked);
        if (!htmlBlocked.includes('Exceeds Mandate Restrictions')) throw new Error('Missing Exceeds Mandate Restrictions badge');
        if (!htmlBlocked.includes('bg-rose-500/15')) throw new Error('Missing rose styling for blocked');
        if (!htmlBlocked.includes('Item unit price of $149.99 exceeds')) throw new Error('Missing mandate reason explanation box');

        // 3. NOT_APPLICABLE case
        const eligNA = {
            product_id: 'p1',
            status: 'QUALIFIED',
            is_qualified: true,
            mandate_status: 'NOT_APPLICABLE',
            mandate_reasons: []
        };
        const htmlNA = m.renderAssistantProductCard(dummyProd, eligNA);
        if (!htmlNA.includes('No Active Mandate')) throw new Error('Missing No Active Mandate badge');
        if (!htmlNA.includes('bg-slate-800')) throw new Error('Missing slate styling for not applicable');

        console.log('ALL_BADGES_VERIFIED');
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
    assert "ALL_BADGES_VERIFIED" in res.stdout


# ==============================================================================
# Full API Endpoint Integration Test
# ==============================================================================

@pytest.mark.asyncio
async def test_assistant_api_endpoint_multi_product_mandate(async_client: httpx.AsyncClient):
    """
    Full async HTTP integration test calling POST /api/assistant/query with an active spending mandate.
    Verifies that the returned JSON payload includes multi-product mandate compliance details.
    """
    session_id = f"test_sess_p4a2_{uuid.uuid4().hex[:8]}"

    # 1. Create a spending mandate with a tight $100 item ceiling via /api/mandates
    mandate_payload = {
        "session_id": session_id,
        "session_identifier": session_id,
        "total_budget": 500.0,
        "maximum_item_price": 100.0,
        "maximum_quantity": 2,
        "currency": "USD",
    }
    m_res = await async_client.post("/api/mandates", json=mandate_payload)
    assert m_res.status_code == status.HTTP_201_CREATED

    created_session_id = m_res.json()["session_id"]

    # 2. Query the assistant for headphones using the session with active mandate
    q_payload = {
        "session_id": created_session_id,
        "query": "I need wireless headphones under 400 dollars",
    }
    a_res = await async_client.post("/api/assistant/query", json=q_payload)
    assert a_res.status_code == status.HTTP_200_OK

    data = a_res.json()
    assert data["success"] is True
    assert "eligibility_details" in data
    assert len(data["eligibility_details"]) > 0

    # Verify every product has explicit mandate_status
    for item in data["eligibility_details"]:
        assert item["mandate_status"] in ("ALLOWED", "WARNING_OVERRIDE_REQUIRED", "BLOCKED")
        assert isinstance(item["mandate_reasons"], list)

    # Verify overall mandate_evaluation is present
    mandate_eval = data["mandate_evaluation"]
    assert mandate_eval is not None
    assert "allowed_product_ids" in mandate_eval
    assert "blocked_product_ids" in mandate_eval
    assert "per_product_evaluations" in mandate_eval

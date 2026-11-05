"""
BUYWISE AI — User-Controlled Budget Override Test Suite.
Verifies that user budgets function as soft spending preferences rather than hard purchase blocks:
1. Within-budget evaluation -> ALLOWED
2. Soft budget overrun -> WARNING_OVERRIDE_REQUIRED (with exact excess and currency)
3. Soft per-item ceiling overrun -> WARNING_OVERRIDE_REQUIRED
4. Hard currency mismatch -> BLOCKED (non-overridable)
5. Hard quantity violation -> BLOCKED (non-overridable)
6. Missing mandate -> BLOCKED (non-overridable)
7. Assistant ranking preference -> within-budget qualified ranks above over-budget qualified
8. Excluded brand non-overridability -> cannot qualify even with budget override
9. Checkout rejection without confirmation -> HTTP 400 / BudgetOverrideRequiredError
10. Checkout success with explicit confirmation -> PaymentOrder persisted with audit fields
11. Mandate preservation -> spending mandate in DB remains untouched
12. PayPal capture on overridden order -> captures authorized amount cleanly
13. Gemini explanation bounds -> AI cannot alter eligibility or mandate decisions
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from backend.app.models.payment import PaymentOrder, PaymentOrderStatus
from backend.app.models.shopping import Product, RetailerOffer, ShoppingSession, SpendingMandate
from backend.app.models.user import User
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.schemas.assistant import (
    ProductEligibilityItem,
    ShoppingIntent,
    SpecificationRequirement,
)
from backend.app.schemas.checkout import CheckoutOrderCreate, OrderItemCreate
from backend.app.schemas.product import ProductResponse, RetailerOfferResponse
from backend.app.services.assistant_service import AssistantService
from backend.app.services.checkout_service import (
    BudgetOverrideRequiredError,
    CheckoutService,
    MandatePolicyViolationError,
)
from backend.app.services.policy_engine import SpendingPolicyEngine


def _create_mock_product(
    prod_id: str,
    name: str,
    price: float,
    currency: str = "USD",
    brand: str = "TestBrand",
    ram_gb: int | None = 16,
) -> ProductResponse:
    now = datetime.now(timezone.utc)
    specs = {"brand": brand}
    if ram_gb is not None:
        specs["ram"] = f"{ram_gb} GB"

    offer = RetailerOfferResponse(
        id=f"off-{prod_id}",
        product_id=prod_id,
        retailer_name="Demo Store",
        price=price,
        currency=currency,
        availability="IN_STOCK",
        product_url=f"https://example.com/p/{prod_id}",
        is_synthetic=True,
        created_at=now,
    )
    return ProductResponse(
        id=prod_id,
        name=name,
        brand=brand,
        category="laptop",
        description=f"Description for {name}",
        specifications=specs,
        offers=[offer],
        lowest_price=price,
        lowest_price_retailer="Demo Store",
        is_synthetic=True,
        created_at=now,
    )


# -------------------------------------------------------------------------
# Test 1: Within-budget product evaluation
# -------------------------------------------------------------------------
def test_1_within_budget_evaluation_allowed():
    mandate = SpendingMandate(
        id="m-1",
        session_id="s-1",
        total_budget=1000.0,
        maximum_item_price=800.0,
        maximum_quantity=2,
        currency="USD",
    )
    res = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=500.0,
        quantity=1,
        currency="USD",
    )
    assert res.decision == "ALLOWED"
    assert res.is_allowed is True
    assert res.is_budget_exceeded is False
    assert res.is_override_eligible is False
    assert res.budget_exceeded_amount is None
    assert res.remaining_budget_after_purchase == 500.0


# -------------------------------------------------------------------------
# Test 2: Soft budget overrun
# -------------------------------------------------------------------------
def test_2_soft_budget_overrun_warning_override_required():
    mandate = SpendingMandate(
        id="m-2",
        session_id="s-2",
        total_budget=50000.0,
        maximum_item_price=80000.0,
        maximum_quantity=2,
        currency="INR",
    )
    res = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=75000.0,
        quantity=1,
        currency="INR",
    )
    assert res.decision == "WARNING_OVERRIDE_REQUIRED"
    assert res.is_allowed is False
    assert res.is_budget_exceeded is True
    assert res.is_override_eligible is True
    assert res.budget_exceeded_amount == 25000.0
    assert res.currency == "INR"
    assert any("exceeds your remaining mandate budget" in r for r in res.reasons)


# -------------------------------------------------------------------------
# Test 3: Soft per-item ceiling overrun
# -------------------------------------------------------------------------
def test_3_soft_item_ceiling_overrun_warning_override_required():
    mandate = SpendingMandate(
        id="m-3",
        session_id="s-3",
        total_budget=100000.0,
        maximum_item_price=40000.0,
        maximum_quantity=2,
        currency="INR",
    )
    res = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=45000.0,
        quantity=1,
        currency="INR",
    )
    assert res.decision == "WARNING_OVERRIDE_REQUIRED"
    assert res.is_allowed is False
    assert res.is_budget_exceeded is True
    assert res.is_override_eligible is True
    assert res.budget_exceeded_amount == 5000.0
    assert any("exceeds your maximum permitted item price limit" in r for r in res.reasons)


# -------------------------------------------------------------------------
# Test 4: Hard currency mismatch
# -------------------------------------------------------------------------
def test_4_hard_currency_mismatch_blocked_non_overridable():
    mandate = SpendingMandate(
        id="m-4",
        session_id="s-4",
        total_budget=50000.0,
        maximum_item_price=50000.0,
        maximum_quantity=2,
        currency="INR",
    )
    res = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=500.0,
        quantity=1,
        currency="USD",
    )
    assert res.decision == "BLOCKED"
    assert res.is_allowed is False
    assert res.is_override_eligible is False
    assert any("Cross-currency conversion is not permitted" in r for r in res.reasons)


# -------------------------------------------------------------------------
# Test 5: Hard quantity violation
# -------------------------------------------------------------------------
def test_5_hard_quantity_violation_blocked_non_overridable():
    mandate = SpendingMandate(
        id="m-5",
        session_id="s-5",
        total_budget=1000.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )
    res = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=50.0,
        quantity=5,
        currency="USD",
    )
    assert res.decision == "BLOCKED"
    assert res.is_allowed is False
    assert res.is_override_eligible is False
    assert any("exceeds your maximum permitted quantity limit" in r for r in res.reasons)


# -------------------------------------------------------------------------
# Test 6: Missing mandate
# -------------------------------------------------------------------------
def test_6_missing_mandate_blocked_non_overridable():
    res = SpendingPolicyEngine.evaluate(
        mandate=None,
        unit_price=100.0,
        quantity=1,
        currency="USD",
    )
    assert res.decision == "BLOCKED"
    assert res.is_allowed is False
    assert res.is_override_eligible is False
    assert any("No active spending mandate found" in r for r in res.reasons)


# -------------------------------------------------------------------------
# Test 7: Assistant ranking preference
# -------------------------------------------------------------------------
def test_7_assistant_ranking_preference_in_budget_before_over_budget(test_session: Session):
    intent = ShoppingIntent(
        query="laptop with 16GB RAM under 1000 USD",
        category="laptop",
        max_budget=1000.0,
        currency="USD",
        required_specs=[
            SpecificationRequirement(
                name="ram",
                value="16 GB",
                raw_spec="16GB RAM",
                min_value=16.0,
                unit="GB",
                is_hard_requirement=True,
            )
        ],
    )

    # Product A: $900 (within budget, 16GB RAM) -> QUALIFIED, within budget
    prod_a = _create_mock_product("prod-a", "In-Budget Laptop", 900.0, "USD", ram_gb=16)
    # Product B: $1200 (over budget, 16GB RAM) -> QUALIFIED, is_budget_exceeded=True
    prod_b = _create_mock_product("prod-b", "Over-Budget Laptop", 1200.0, "USD", ram_gb=16)

    elig_a = AssistantService.evaluate_product_eligibility(prod_a, intent)
    elig_b = AssistantService.evaluate_product_eligibility(prod_b, intent)

    assert elig_a.status == "QUALIFIED"
    assert elig_a.is_budget_exceeded is False

    assert elig_b.status == "QUALIFIED"
    assert elig_b.is_budget_exceeded is True
    assert elig_b.budget_exceeded_amount == 200.0
    assert elig_b.is_override_eligible is True

    # Test sorting key
    elig_map = {"prod-a": elig_a, "prod-b": elig_b}

    def _rank_sort_key(p: ProductResponse):
        e = elig_map.get(p.id)
        tier_rank = {"QUALIFIED": 0, "UNVERIFIED_SPECS": 1, "CURRENCY_MISMATCH": 2, "EXCLUDED": 3}.get(
            e.status if e else "EXCLUDED", 3
        )
        budget_tier = 1 if (e and e.is_budget_exceeded) else 0
        score_inv = -round(e.recommendation_score if e else 0.0, 1)
        return (tier_rank, budget_tier, score_inv, p.lowest_price or float("inf"), p.id)

    ranked = sorted([prod_b, prod_a], key=_rank_sort_key)
    assert ranked[0].id == "prod-a"
    assert ranked[1].id == "prod-b"


# -------------------------------------------------------------------------
# Test 8: Excluded brand non-overridability
# -------------------------------------------------------------------------
def test_8_excluded_brand_cannot_qualify():
    intent = ShoppingIntent(
        query="laptop excluding Dell",
        category="laptop",
        max_budget=2000.0,
        currency="USD",
        excluded_brands=["Dell"],
    )

    prod_dell = _create_mock_product("prod-dell", "Dell XPS 15", 1500.0, "USD", brand="Dell", ram_gb=16)
    elig = AssistantService.evaluate_product_eligibility(prod_dell, intent)

    assert elig.status == "EXCLUDED"
    assert elig.is_qualified is False
    assert elig.is_override_eligible is False
    assert elig.recommendation_score == 0.0
    assert any("Brand 'Dell' was explicitly excluded" in r for r in elig.reasons)


# -------------------------------------------------------------------------
# Test 9: Checkout rejection without confirmation
# -------------------------------------------------------------------------
def test_9_checkout_rejected_without_override_confirmation(test_session: Session):
    user = User(id="user-override-1", email="user-override-1@example.com", name="User 1", hashed_password="fake")
    test_session.add(user)
    session = MandateRepository.get_or_create_session(test_session, "sess-override-reject")
    session.user_id = user.id
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=session.id,
        total_budget=500.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    prod = Product(
        id="prod-over-1",
        name="Premium Phone",
        brand="Apple",
        category="smartphone",
        is_synthetic=True,
    )
    offer = RetailerOffer(
        id="off-over-1",
        product_id="prod-over-1",
        retailer_name="Demo Store",
        price=750.0,
        currency="USD",
        product_url="https://example.com/p/prod-over-1",
        availability="IN_STOCK",
        is_synthetic=True,
    )
    test_session.add(prod)
    test_session.add(offer)
    test_session.commit()

    order_payload = CheckoutOrderCreate(
        session_id=session.id,
        mandate_id=mandate.id,
        currency="USD",
        items=[
            OrderItemCreate(
                product_id="prod-over-1",
                retailer_offer_id="off-over-1",
                product_name="Premium Phone",
                retailer_name="Demo Store",
                quantity=1,
                unit_price=Decimal("750.00"),
                currency="USD",
            )
        ],
        budget_override_confirmed=False,
    )

    with pytest.raises(BudgetOverrideRequiredError) as exc_info:
        CheckoutService.create_checkout_order(db=test_session, request=order_payload, user_id=user.id)

    assert exc_info.value.exceeded_amount == 250.0
    assert exc_info.value.currency == "USD"
    assert exc_info.value.preferred_budget == 500.0


# -------------------------------------------------------------------------
# Test 10: Checkout success with confirmation
# -------------------------------------------------------------------------
def test_10_checkout_success_with_override_confirmation(test_session: Session):
    user = User(id="user-override-2", email="user-override-2@example.com", name="User 2", hashed_password="fake")
    test_session.add(user)
    session = MandateRepository.get_or_create_session(test_session, "sess-override-confirm")
    session.user_id = user.id
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=session.id,
        total_budget=500.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    prod = Product(
        id="prod-over-2",
        name="Flagship Tablet",
        brand="Samsung",
        category="tablet",
        is_synthetic=True,
    )
    offer = RetailerOffer(
        id="off-over-2",
        product_id="prod-over-2",
        retailer_name="Demo Store",
        price=750.0,
        currency="USD",
        product_url="https://example.com/p/prod-over-2",
        availability="IN_STOCK",
        is_synthetic=True,
    )
    test_session.add(prod)
    test_session.add(offer)
    test_session.commit()

    order_payload = CheckoutOrderCreate(
        session_id=session.id,
        mandate_id=mandate.id,
        currency="USD",
        items=[
            OrderItemCreate(
                product_id="prod-over-2",
                retailer_offer_id="off-over-2",
                product_name="Flagship Tablet",
                retailer_name="Demo Store",
                quantity=1,
                unit_price=Decimal("750.00"),
                currency="USD",
            )
        ],
        budget_override_confirmed=True,
        acknowledged_budget_excess=Decimal("250.00"),
    )

    with patch("backend.app.services.paypal_client.PayPalClient.create_order") as mock_pp_create:
        mock_pp_create.return_value = {
            "id": "PAYPAL-ORDER-OVERRIDE-123",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=TEST"}],
        }
        res = CheckoutService.create_checkout_order(db=test_session, request=order_payload, user_id=user.id)

    assert res.id is not None
    assert res.budget_override_applied is True
    assert res.original_budget_amount == Decimal("500.00")
    assert res.budget_exceeded_amount == Decimal("250.00")

    # Verify persisted in database
    db_order = test_session.query(PaymentOrder).filter_by(id=res.id).first()
    assert db_order is not None
    assert db_order.budget_override_applied is True
    assert db_order.original_budget_amount == Decimal("500.00")
    assert db_order.budget_exceeded_amount == Decimal("250.00")


# -------------------------------------------------------------------------
# Test 11: Mandate preservation
# -------------------------------------------------------------------------
def test_11_spending_mandate_preserved_after_override(test_session: Session):
    user = User(id="user-override-3", email="user-override-3@example.com", name="User 3", hashed_password="fake")
    test_session.add(user)
    session = MandateRepository.get_or_create_session(test_session, "sess-mandate-preserved")
    session.user_id = user.id
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=session.id,
        total_budget=500.0,
        maximum_item_price=500.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    prod = Product(
        id="prod-over-3",
        name="Ultra Camera",
        brand="Sony",
        category="camera",
        is_synthetic=True,
    )
    offer = RetailerOffer(
        id="off-over-3",
        product_id="prod-over-3",
        retailer_name="Demo Store",
        price=800.0,
        currency="USD",
        product_url="https://example.com/p/prod-over-3",
        availability="IN_STOCK",
        is_synthetic=True,
    )
    test_session.add(prod)
    test_session.add(offer)
    test_session.commit()

    order_payload = CheckoutOrderCreate(
        session_id=session.id,
        mandate_id=mandate.id,
        currency="USD",
        items=[
            OrderItemCreate(
                product_id="prod-over-3",
                retailer_offer_id="off-over-3",
                product_name="Ultra Camera",
                retailer_name="Demo Store",
                quantity=1,
                unit_price=Decimal("800.00"),
                currency="USD",
            )
        ],
        budget_override_confirmed=True,
    )

    with patch("backend.app.services.paypal_client.PayPalClient.create_order") as mock_pp_create:
        mock_pp_create.return_value = {
            "id": "PAYPAL-ORDER-PRESERVED",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=TEST"}],
        }
        CheckoutService.create_checkout_order(db=test_session, request=order_payload, user_id=user.id)

    # Re-fetch mandate from DB
    re_mandate = test_session.query(SpendingMandate).filter_by(id=mandate.id).first()
    assert re_mandate.total_budget == 500.0
    assert re_mandate.maximum_item_price == 500.0
    assert re_mandate.currency == "USD"
    assert re_mandate.maximum_quantity == 2


# -------------------------------------------------------------------------
# Test 12: PayPal capture on overridden order
# -------------------------------------------------------------------------
def test_12_paypal_capture_on_overridden_order(test_session: Session):
    session = MandateRepository.get_or_create_session(test_session, "sess-capture-override")
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    order = PaymentOrder(
        id=str(uuid.uuid4()),
        internal_order_number="ORD-TEST-OVERRIDE",
        session_id=session.id,
        paypal_order_id="PP-ORDER-OVERRIDE-CAPTURE",
        paypal_request_id="req-123",
        idempotency_key="idemp-123",
        status=PaymentOrderStatus.CREATED.value,
        total_amount=Decimal("750.00"),
        currency="USD",
        payment_provider="paypal_sandbox",
        order_type="SANDBOX_DEMO",
        budget_override_applied=True,
        original_budget_amount=Decimal("500.00"),
        budget_exceeded_amount=Decimal("250.00"),
        expires_at=expires,
    )
    test_session.add(order)
    test_session.commit()

    with patch("backend.app.services.paypal_client.PayPalClient.get_order_details") as mock_details, \
         patch("backend.app.services.paypal_client.PayPalClient.capture_order") as mock_capture:
        mock_details.return_value = {
            "id": "PP-ORDER-OVERRIDE-CAPTURE",
            "status": "APPROVED",
            "purchase_units": [{"amount": {"value": "750.00", "currency_code": "USD"}}],
        }
        mock_capture.return_value = {
            "id": "PP-ORDER-OVERRIDE-CAPTURE",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "CAPTURE-12345",
                                "status": "COMPLETED",
                                "amount": {"value": "750.00", "currency_code": "USD"},
                            }
                        ]
                    }
                }
            ],
            "payer": {"payer_id": "BUYER123", "email_address": "buyer@example.com"},
        }
        res = CheckoutService.capture_checkout_order(
            db=test_session,
            order_id=order.id,
            session_id=session.id,
        )

    assert res.status == "COMPLETED"
    assert res.total_amount == Decimal("750.00")
    assert res.budget_override_applied is True
    assert len(res.captures) == 1
    assert res.captures[0].captured_amount == Decimal("750.00")


# -------------------------------------------------------------------------
# Test 13: Gemini explanation bounds
# -------------------------------------------------------------------------
def test_13_gemini_cannot_override_policy_or_authorize():
    elig_warning = ProductEligibilityItem(
        product_id="prod-warning",
        product_name="Over-Budget Laptop",
        status="QUALIFIED",
        is_qualified=True,
        mandate_status="WARNING_OVERRIDE_REQUIRED",
        mandate_reasons=["Total purchase cost exceeds your preferred budget by $200.00."],
        is_budget_exceeded=True,
        budget_exceeded_amount=200.0,
        is_override_eligible=True,
    )

    intent = ShoppingIntent(
        query="laptop under 1000",
        category="laptop",
        max_budget=1000.0,
        currency="USD",
    )

    prod = _create_mock_product("prod-warning", "Over-Budget Laptop", 1200.0, "USD")

    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=[prod],
        mandate_eval={
            "decision": "WARNING_OVERRIDE_REQUIRED",
            "reasons": ["Total purchase cost exceeds your preferred budget by $200.00."],
        },
        eligibility_items=[elig_warning],
    )

    assert "Spending Policy" in summary
    assert "Exceeds preferred budget limit" in summary
    assert elig_warning.mandate_status == "WARNING_OVERRIDE_REQUIRED"
    assert elig_warning.is_budget_exceeded is True

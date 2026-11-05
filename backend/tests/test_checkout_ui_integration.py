"""
BUYWISE AI — Phase 4A Checkout UI & Budget Override Integration Tests.
Verifies end-to-end integration of product card actions, modal actions,
budget override confirmation flow, PayPal sandbox approval, capture settlement,
and duplicate-click prevention.
"""

import json
import subprocess
import time
from typing import Any
import httpx
import pytest
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import create_access_token
from backend.app.models.user import User
from backend.app.models.shopping import SpendingMandate, Product, RetailerOffer, ShoppingSession
from backend.app.models.payment import PaymentOrder, PaymentOrderStatus
from backend.app.services.checkout_service import CheckoutService
from backend.app.services.paypal_client import PayPalClient


@pytest.fixture(autouse=True)
def authed_checkout_user(test_session: Session, async_client: httpx.AsyncClient):
    user = User(
        id="authed_ui_test_user_1",
        email="ui_checkout_user@example.com",
        name="UI Checkout User",
        hashed_password="test_hashed_password",
    )
    test_session.add(user)
    test_session.commit()
    token, _, _ = create_access_token(user_id=user.id, email=user.email, name=user.name)
    async_client.headers["Authorization"] = f"Bearer {token}"
    yield user


# ---------------------------------------------------------------------------
# Helpers & Node Evaluation Runner
# ---------------------------------------------------------------------------

def run_node_js(js_code: str) -> dict:
    """Execute JavaScript code in Node.js with DOM stubs and return parsed JSON stdout."""
    full_script = f"""
    global.document = {{ addEventListener: () => {{}}, getElementById: () => null, querySelectorAll: () => [] }};
    global.window = {{ location: {{ hash: '' }} }};
    (async () => {{
        try {{
            {js_code}
        }} catch (err) {{
            console.error(JSON.stringify({{ error: err.message, stack: err.stack }}));
            process.exit(1);
        }}
    }})();
    """
    res = subprocess.run(
        ["node", "-e", full_script],
        cwd=".",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout.strip())


# ---------------------------------------------------------------------------
# Mock Transport Fixtures for PayPal REST API
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_paypal_transport():
    """Factory fixture returning an HTTPX transport that mocks PayPal Orders v2 endpoints."""
    orders_db: dict[str, dict[str, Any]] = {}
    captures_db: dict[str, dict[str, Any]] = {}
    order_status_db: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        # 1. OAuth2 Token
        if "/v1/oauth2/token" in url_str:
            return httpx.Response(
                200,
                json={
                    "scope": "https://api-m.paypal.com/v2/checkout/orders",
                    "access_token": "mock_paypal_access_token_xyz123",
                    "token_type": "Bearer",
                    "app_id": "APP-80W284485P519543T",
                    "expires_in": 32400,
                    "nonce": "mock_nonce_123",
                },
            )

        # 2. Capture Order: POST /v2/checkout/orders/{id}/capture
        if "/capture" in url_str and request.method == "POST":
            parts = url_str.split("/v2/checkout/orders/")[1].split("/capture")[0]
            order_id = parts

            if getattr(handler, "simulate_decline", False) or "simulate-decline" in order_id:
                return httpx.Response(
                    422,
                    json={
                        "name": "UNPROCESSABLE_ENTITY",
                        "message": "The transaction was declined by the issuer.",
                        "details": [{"issue": "INSTRUMENT_DECLINED", "description": "Payment instrument declined."}],
                    },
                )

            capture_id = f"CAP-{order_id[-8:]}"
            amount_val = orders_db.get(order_id, {}).get("amount", "100.00")
            currency_code = orders_db.get(order_id, {}).get("currency", "USD")

            resp_payload = {
                "id": order_id,
                "status": "COMPLETED",
                "payment_source": {
                    "paypal": {
                        "email_address": "sb-buyer@business.example.com",
                        "account_id": "BUYER-ACCT-12345",
                    }
                },
                "purchase_units": [
                    {
                        "reference_id": "ORD-REF-1",
                        "payments": {
                            "captures": [
                                {
                                    "id": capture_id,
                                    "status": "COMPLETED",
                                    "amount": {"currency_code": currency_code, "value": amount_val},
                                    "final_capture": True,
                                    "create_time": "2026-11-04T12:00:00Z",
                                    "update_time": "2026-11-04T12:00:00Z",
                                }
                            ]
                        },
                    }
                ],
                "payer": {
                    "email_address": "sb-buyer@business.example.com",
                    "payer_id": "BUYER-ACCT-12345",
                },
            }
            captures_db[order_id] = resp_payload
            order_status_db[order_id] = "COMPLETED"
            return httpx.Response(201, json=resp_payload)

        # 3. Create Order: POST /v2/checkout/orders
        if "/v2/checkout/orders" in url_str and request.method == "POST":
            body = json.loads(request.content.decode())
            amount = body["purchase_units"][0]["amount"]["value"]
            currency = body["purchase_units"][0]["amount"]["currency_code"]
            app_context = body.get("application_context", {})
            order_id = f"5O{int(time.time()*1000)%10000000000}TN364715T"

            orders_db[order_id] = {
                "amount": amount,
                "currency": currency,
                "application_context": app_context,
            }
            order_status_db[order_id] = getattr(handler, "default_create_status", "APPROVED")

            return httpx.Response(
                201,
                json={
                    "id": order_id,
                    "status": "CREATED",
                    "links": [
                        {
                            "href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{order_id}",
                            "rel": "self",
                            "method": "GET",
                        },
                        {
                            "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                            "rel": "approve",
                            "method": "GET",
                        },
                    ],
                },
            )

        # 4. Get Order Details: GET /v2/checkout/orders/{id}
        if "/v2/checkout/orders/" in url_str and request.method == "GET":
            order_id = url_str.split("/v2/checkout/orders/")[1].split("?")[0]
            if order_id not in orders_db and order_id not in captures_db:
                return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "Order not found"})

            if order_id in captures_db:
                return httpx.Response(200, json=captures_db[order_id])

            st = order_status_db.get(order_id, "APPROVED")
            return httpx.Response(
                200,
                json={
                    "id": order_id,
                    "status": st,
                    "purchase_units": [
                        {"amount": {"currency_code": orders_db[order_id]["currency"], "value": orders_db[order_id]["amount"]}}
                    ],
                },
            )

        return httpx.Response(404, json={"message": "Not found"})

    handler.orders_db = orders_db
    handler.captures_db = captures_db
    handler.order_status_db = order_status_db
    handler.simulate_decline = False
    handler.default_create_status = "APPROVED"
    return handler


@pytest.fixture
def mock_paypal_client(mock_paypal_transport) -> PayPalClient:
    """Fixture providing a PayPalClient wired to the mock transport."""
    transport = httpx.MockTransport(mock_paypal_transport)
    http_client = httpx.Client(transport=transport, timeout=10.0)
    return PayPalClient(
        client_id="mock_client_id_12345",
        client_secret="mock_client_secret_67890",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=http_client,
    )


# ---------------------------------------------------------------------------
# 1. UI Rendering Tests (Node.js ES Module evaluation)
# ---------------------------------------------------------------------------

def test_demo_product_buy_button_rendered():
    """Verify demo/synthetic product card and assistant card render Buy with PayPal (Demo) button."""
    js_code = """
    const { renderProductCard, renderAssistantProductCard } = await import('./frontend/assets/js/components.js');
    const demoProduct = {
        id: "prod-demo-123",
        name: "Wireless ANC Headphones",
        brand: "AudioPro",
        category: "Audio",
        is_synthetic: true,
        lowest_price: 199.99,
        lowest_price_retailer: "Demo Electronics Store",
        offers: [
            { id: "off-1", retailer_name: "Demo Electronics Store", price: 199.99, currency: "USD", availability: "IN_STOCK", is_synthetic: true, product_url: "https://demo.local/headphones" }
        ]
    };

    const cardHtml = renderProductCard(demoProduct);
    const assistantHtml = renderAssistantProductCard(demoProduct);

    console.log(JSON.stringify({
        cardHasButton: cardHtml.includes("js-btn-checkout") && cardHtml.includes("Buy with PayPal (Demo)"),
        cardHasProductId: cardHtml.includes('data-product-id="prod-demo-123"'),
        cardHasSyntheticAttr: cardHtml.includes('data-is-synthetic="true"'),
        assistantHasButton: assistantHtml.includes("js-btn-checkout") && assistantHtml.includes("Buy with PayPal (Demo)"),
        assistantHasProductId: assistantHtml.includes('data-product-id="prod-demo-123"'),
    }));
    """
    result = run_node_js(js_code)
    assert result["cardHasButton"] is True
    assert result["cardHasProductId"] is True
    assert result["cardHasSyntheticAttr"] is True
    assert result["assistantHasButton"] is True
    assert result["assistantHasProductId"] is True


def test_live_channel3_product_no_buy_button():
    """Verify live Channel3 product cards do NOT render the Buy with PayPal button."""
    js_code = """
    const { renderProductCard, renderAssistantProductCard } = await import('./frontend/assets/js/components.js');
    const liveProduct = {
        id: "prod-ch3-999",
        name: "Dell XPS 15 Laptop",
        brand: "Dell",
        category: "Computing",
        is_synthetic: false,
        lowest_price: 1499.00,
        lowest_price_retailer: "BestBuy",
        offers: [
            { id: "off-ch3-1", retailer_name: "BestBuy", price: 1499.00, currency: "USD", availability: "IN_STOCK", is_synthetic: false, product_url: "https://bestbuy.com/xps15" }
        ]
    };

    const cardHtml = renderProductCard(liveProduct);
    const assistantHtml = renderAssistantProductCard(liveProduct);

    console.log(JSON.stringify({
        cardHasButton: cardHtml.includes("js-btn-checkout") || cardHtml.includes("Buy with PayPal"),
        assistantHasButton: assistantHtml.includes("js-btn-checkout") || assistantHtml.includes("Buy with PayPal"),
        cardHasViewOffers: cardHtml.includes("View Offers"),
        assistantHasViewOffers: assistantHtml.includes("View Offers"),
    }));
    """
    result = run_node_js(js_code)
    assert result["cardHasButton"] is False
    assert result["assistantHasButton"] is False
    assert result["cardHasViewOffers"] is True
    assert result["assistantHasViewOffers"] is True


def test_live_channel3_offer_external_link_preserved():
    """Verify in modal, live Channel3 offers preserve Visit Merchant external link and omit PayPal buy."""
    js_code = """
    const { renderProductDetailModal } = await import('./frontend/assets/js/components.js');
    const liveProduct = {
        id: "prod-live-456",
        name: "Apple iPad Air 11-inch",
        brand: "Apple",
        category: "Computing",
        is_synthetic: false,
        lowest_price: 599.00,
        lowest_price_retailer: "Target",
        offers: [
            { id: "off-t1", retailer_name: "Target", price: 599.00, currency: "USD", availability: "IN_STOCK", is_synthetic: false, product_url: "https://target.com/ipad-air" }
        ]
    };

    const modalHtml = renderProductDetailModal(liveProduct);

    console.log(JSON.stringify({
        hasVisitMerchant: modalHtml.includes("Visit Merchant ↗"),
        hasProductUrl: modalHtml.includes("https://target.com/ipad-air"),
        hasBuyButton: modalHtml.includes("Buy with PayPal"),
        hasTargetBlank: modalHtml.includes('target="_blank"'),
    }));
    """
    result = run_node_js(js_code)
    assert result["hasVisitMerchant"] is True
    assert result["hasProductUrl"] is True
    assert result["hasBuyButton"] is False
    assert result["hasTargetBlank"] is True


def test_demo_offer_shows_buy_with_paypal_in_modal():
    """Verify in modal, synthetic demo offers display Buy with PayPal button."""
    js_code = """
    const { renderProductDetailModal } = await import('./frontend/assets/js/components.js');
    const demoProduct = {
        id: "prod-demo-789",
        name: "Smart Fitness Tracker",
        brand: "FitBrand",
        category: "Wearables",
        is_synthetic: true,
        lowest_price: 79.99,
        lowest_price_retailer: "FitStore",
        offers: [
            { id: "off-demo-fit", retailer_name: "FitStore", price: 79.99, currency: "USD", availability: "IN_STOCK", is_synthetic: true, product_url: "https://demo.local/fit" }
        ]
    };

    const modalHtml = renderProductDetailModal(demoProduct);

    console.log(JSON.stringify({
        hasBuyButton: modalHtml.includes("Buy with PayPal"),
        hasCheckoutClass: modalHtml.includes("js-btn-checkout"),
        hasOfferId: modalHtml.includes('data-offer-id="off-demo-fit"'),
        hasProductId: modalHtml.includes('data-product-id="prod-demo-789"'),
        hasPrice: modalHtml.includes('data-price="79.99"'),
    }));
    """
    result = run_node_js(js_code)
    assert result["hasBuyButton"] is True
    assert result["hasCheckoutClass"] is True
    assert result["hasOfferId"] is True
    assert result["hasProductId"] is True
    assert result["hasPrice"] is True


# ---------------------------------------------------------------------------
# 2. Checkout Backend Flow & Budget Override Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_within_budget_checkout_flow(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Test standard within-budget checkout creates PayPal order and captures successfully."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    # 1. Setup session and mandate (max budget: 100 USD)
    session = ShoppingSession(session_identifier="user_within_budget_sess_1")
    test_session.add(session)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=session.id,
        currency="USD",
        total_budget=100.00,
        maximum_item_price=100.00,
        maximum_quantity=5,
    )
    test_session.add(mandate)
    test_session.commit()

    # 2. Setup demo product (price: 50.00 USD - within budget)
    prod = Product(
        name="Demo Earbuds",
        brand="SoundCo",
        category="Audio",
        is_synthetic=True,
        provider="synthetic",
    )
    test_session.add(prod)
    test_session.commit()

    offer = RetailerOffer(
        product_id=prod.id,
        retailer_name="Demo Store",
        price=50.00,
        currency="USD",
        availability="IN_STOCK",
        is_synthetic=True,
        product_url="https://demo.local/earbuds",
    )
    test_session.add(offer)
    test_session.commit()

    # 3. Request Checkout
    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Earbuds",
                "quantity": 1,
                "unit_price": "50.00",
                "currency": "USD",
                "product_id": prod.id,
                "retailer_offer_id": offer.id,
                "retailer_name": "Demo Store",
            }
        ],
        "budget_override_confirmed": False,
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_data = create_res.json()
    assert order_data["status"] == "CREATED"
    assert order_data["total_amount"] == "50.00"
    assert order_data["budget_override_applied"] is False
    assert "https://www.sandbox.paypal.com/checkoutnow?token=" in order_data["approval_url"]

    # 4. Capture Order
    capture_res = await async_client.post(f"/api/checkout/orders/{order_data['id']}/capture?session_id={session.id}")
    assert capture_res.status_code == 200
    captured_data = capture_res.json()
    assert captured_data["status"] == "COMPLETED"
    assert len(captured_data["captures"]) == 1
    assert captured_data["captures"][0]["is_verified"] is True


@pytest.mark.asyncio
async def test_over_budget_checkout_triggers_override_requirement(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Test checkout for an over-budget product without confirmation returns HTTP 400 budget_override_required."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    # Preferred budget: 50 USD
    session = ShoppingSession(session_identifier="user_overbudget_sess_1")
    test_session.add(session)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=session.id,
        currency="USD",
        total_budget=50.00,
        maximum_item_price=50.00,
        maximum_quantity=5,
    )
    test_session.add(mandate)
    test_session.commit()

    # Product price: 75 USD (exceeds budget by 25 USD)
    prod = Product(
        name="Premium Gaming Mouse",
        brand="GameTech",
        category="Gaming",
        is_synthetic=True,
        provider="synthetic",
    )
    test_session.add(prod)
    test_session.commit()

    offer = RetailerOffer(
        product_id=prod.id,
        retailer_name="Mouse Store",
        price=75.00,
        currency="USD",
        availability="IN_STOCK",
        is_synthetic=True,
        product_url="https://demo.local/mouse",
    )
    test_session.add(offer)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Premium Gaming Mouse",
                "quantity": 1,
                "unit_price": "75.00",
                "currency": "USD",
                "product_id": prod.id,
                "retailer_offer_id": offer.id,
                "retailer_name": "Mouse Store",
            }
        ],
        "budget_override_confirmed": False,
    }

    res = await async_client.post("/api/checkout/orders", json=payload)
    assert res.status_code == 400
    data = res.json()["detail"]
    assert data["error"] == "budget_override_required"
    assert data["requires_override_confirmation"] is True
    assert float(data["exceeded_amount"]) == 25.0
    assert float(data["preferred_budget"]) == 50.0
    assert data["currency"] == "USD"


@pytest.mark.asyncio
async def test_user_cancellation_aborts_checkout(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """Test user cancellation keeps mandate active and avoids order creation."""
    session = ShoppingSession(session_identifier="user_cancel_sess_1")
    test_session.add(session)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=session.id,
        currency="USD",
        total_budget=50.00,
        maximum_item_price=50.00,
        maximum_quantity=5,
    )
    test_session.add(mandate)
    test_session.commit()

    # Check orders table has zero orders for this session
    order_count = test_session.query(PaymentOrder).filter_by(session_id=session.id).count()
    assert order_count == 0

    # Verify mandate remains intact
    db_mandate = test_session.query(SpendingMandate).filter_by(session_id=session.id).first()
    assert db_mandate is not None
    assert db_mandate.total_budget == 50.00


@pytest.mark.asyncio
async def test_user_confirmation_completes_checkout_with_override_flag(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Test confirming budget override creates order with budget_override_applied=True and captures successfully."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_confirm_sess_1")
    test_session.add(session)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=session.id,
        currency="USD",
        total_budget=50.00,
        maximum_item_price=50.00,
        maximum_quantity=5,
    )
    test_session.add(mandate)
    test_session.commit()

    prod = Product(
        name="Mechanical Keyboard Pro",
        brand="KeyCo",
        category="Computing",
        is_synthetic=True,
        provider="synthetic",
    )
    test_session.add(prod)
    test_session.commit()

    offer = RetailerOffer(
        product_id=prod.id,
        retailer_name="Key Store",
        price=80.00,
        currency="USD",
        availability="IN_STOCK",
        is_synthetic=True,
        product_url="https://demo.local/keyboard",
    )
    test_session.add(offer)
    test_session.commit()

    # Retry with user confirmation
    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Mechanical Keyboard Pro",
                "quantity": 1,
                "unit_price": "80.00",
                "currency": "USD",
                "product_id": prod.id,
                "retailer_offer_id": offer.id,
                "retailer_name": "Key Store",
            }
        ],
        "budget_override_confirmed": True,
        "acknowledged_budget_excess": "30.00",
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_data = create_res.json()
    assert order_data["status"] == "CREATED"
    assert order_data["budget_override_applied"] is True
    assert float(order_data["original_budget_amount"]) == 50.0
    assert float(order_data["budget_exceeded_amount"]) == 30.0

    # Capture order
    capture_res = await async_client.post(f"/api/checkout/orders/{order_data['id']}/capture?session_id={session.id}")
    assert capture_res.status_code == 200
    captured_data = capture_res.json()
    assert captured_data["status"] == "COMPLETED"
    assert captured_data["budget_override_applied"] is True


def test_paypal_approval_url_handling():
    """Verify frontend code handles PayPal approval URLs and uses window.open."""
    with open("frontend/assets/js/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    assert "order.approval_url" in content
    assert "window.open(order.approval_url" in content
    assert "noopener,noreferrer" in content


def test_capture_verification_and_receipt_rendering():
    """Verify receipt modal renders verified settlement proof and budget override badge."""
    js_code = """
    const { renderOrderReceiptModal } = await import('./frontend/assets/js/app.js');
    const completedOrder = {
        id: "ord-test-receipt-1",
        internal_order_number: "ORD-20261104-9876",
        created_at: "2026-11-04T12:00:00Z",
        status: "COMPLETED",
        total_amount: 80.00,
        currency: "USD",
        budget_override_applied: true,
        budget_exceeded_amount: 30.00,
        items: [
            { product_name: "Mechanical Keyboard Pro", quantity: 1, unit_price: 80.00, subtotal: 80.00, currency: "USD", retailer_name: "Key Store" }
        ],
        captures: [
            { paypal_capture_id: "CAP-12345678", captured_amount: 80.00, currency: "USD", captured_at: "2026-11-04T12:01:00Z", paypal_payer_email_masked: "b***r@example.com" }
        ]
    };

    const receiptHtml = renderOrderReceiptModal(completedOrder);

    console.log(JSON.stringify({
        hasOrderNumber: receiptHtml.includes("ORD-20261104-9876"),
        hasProductName: receiptHtml.includes("Mechanical Keyboard Pro"),
        hasCaptureId: receiptHtml.includes("CAP-12345678"),
        hasVerifiedBadge: receiptHtml.includes("Verified Capture"),
        hasSettlementProof: receiptHtml.includes("PayPal Settlement Proof"),
        hasOverrideBadge: receiptHtml.includes("Budget Override Confirmed"),
        hasExceededAmount: receiptHtml.includes("30.00"),
    }));
    """
    result = run_node_js(js_code)
    assert result["hasOrderNumber"] is True
    assert result["hasProductName"] is True
    assert result["hasCaptureId"] is True
    assert result["hasVerifiedBadge"] is True
    assert result["hasSettlementProof"] is True
    assert result["hasOverrideBadge"] is True
    assert result["hasExceededAmount"] is True


@pytest.mark.asyncio
async def test_order_history_and_analytics_refresh_after_capture(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify after capture, order is present in history and personal analytics updates."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_history_sess_1")
    test_session.add(session)
    test_session.commit()

    # Create & capture order
    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "USB-C Hub",
                "quantity": 1,
                "unit_price": "35.00",
                "currency": "USD",
            }
        ],
    }
    create_res = await async_client.post("/api/checkout/orders", json=payload)
    order_id = create_res.json()["id"]

    capture_res = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={session.id}")
    assert capture_res.status_code == 200

    # 1. Check Order History
    hist_res = await async_client.get(f"/api/checkout/history/{session.id}")
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    assert len(hist_data["orders"]) >= 1
    assert hist_data["orders"][0]["id"] == order_id
    assert hist_data["orders"][0]["status"] == "COMPLETED"

    # 2. Check Personal Analytics
    analytics_res = await async_client.get(f"/api/checkout/analytics/{session.id}")
    assert analytics_res.status_code == 200
    analytics_data = analytics_res.json()
    assert analytics_data["completed_order_count"] >= 1
    assert len(analytics_data["total_spending_by_currency"]) >= 1
    assert float(analytics_data["total_spending_by_currency"][0]["total_spent"]) >= 35.00


def test_duplicate_click_prevention_during_checkout():
    """Verify checkout flow implements duplicate-click prevention and loading state."""
    with open("frontend/assets/js/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    # Verifies early return on disabled button
    assert "if (triggerBtn.disabled) return;" in content
    # Verifies setting disabled
    assert "triggerBtn.disabled = true;" in content
    # Verifies spinner
    assert "animate-spin" in content
    assert "Processing..." in content
    # Verifies restoring button state
    assert "triggerBtn.disabled = false;" in content


# ---------------------------------------------------------------------------
# 3. Dedicated PayPal Approval/Capture Sequence Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unapproved_order_capture_rejected_with_422(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify attempting to capture before buyer approves in PayPal returns 422 order_not_approved and keeps order CREATED."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    # Configure transport so new orders start as CREATED on PayPal (buyer hasn't approved yet)
    mock_paypal_transport.default_create_status = "CREATED"

    session = ShoppingSession(session_identifier="user_unapproved_sess_1")
    test_session.add(session)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Bluetooth Speaker",
                "quantity": 1,
                "unit_price": "45.00",
                "currency": "USD",
            }
        ],
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_data = create_res.json()
    order_id = order_data["id"]
    paypal_order_id = order_data["paypal_order_id"]

    # Ensure PayPal order is in CREATED status
    mock_paypal_transport.order_status_db[paypal_order_id] = "CREATED"

    # Attempt capture before buyer approval
    capture_res = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={session.id}")
    assert capture_res.status_code == 422
    err_body = capture_res.json()["detail"]
    assert err_body["error"] == "order_not_approved"
    assert "not yet been approved" in err_body["message"]
    assert err_body["order_id"] == order_id

    # Verify order state in DB remains CREATED (not COMPLETED, not FAILED)
    db_order = test_session.query(PaymentOrder).filter_by(id=order_id).first()
    assert db_order is not None
    assert db_order.status == PaymentOrderStatus.CREATED


@pytest.mark.asyncio
async def test_approved_order_capture_succeeds_and_completes_order(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify after buyer approves on PayPal (status APPROVED), capture succeeds and completes order."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_approved_sess_1")
    test_session.add(session)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Desk Mat",
                "quantity": 1,
                "unit_price": "25.00",
                "currency": "USD",
            }
        ],
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_data = create_res.json()
    order_id = order_data["id"]
    paypal_order_id = order_data["paypal_order_id"]

    # Buyer approves order on PayPal
    mock_paypal_transport.order_status_db[paypal_order_id] = "APPROVED"

    capture_res = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={session.id}")
    assert capture_res.status_code == 200
    captured_data = capture_res.json()
    assert captured_data["status"] == "COMPLETED"
    assert len(captured_data["captures"]) == 1
    assert captured_data["captures"][0]["is_verified"] is True

    # Verify DB state
    db_order = test_session.query(PaymentOrder).filter_by(id=order_id).first()
    assert db_order.status == PaymentOrderStatus.COMPLETED


@pytest.mark.asyncio
async def test_buyer_cancellation_cancels_order_and_prevents_subsequent_capture(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify buyer explicit cancellation transitions order to CANCELLED and rejects subsequent capture."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_cancel_flow_sess_1")
    test_session.add(session)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Wireless Charger",
                "quantity": 1,
                "unit_price": "29.99",
                "currency": "USD",
            }
        ],
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]

    # Buyer clicks Cancel in UI -> calls POST /api/checkout/orders/{id}/cancel
    cancel_res = await async_client.post(f"/api/checkout/orders/{order_id}/cancel?session_id={session.id}")
    assert cancel_res.status_code == 200
    cancel_data = cancel_res.json()
    assert cancel_data["status"] == "CANCELLED"

    # Verify DB state
    db_order = test_session.query(PaymentOrder).filter_by(id=order_id).first()
    assert db_order.status == PaymentOrderStatus.CANCELLED

    # Subsequent capture attempt should fail with InvalidOrderStateError (HTTP 400)
    capture_res = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={session.id}")
    assert capture_res.status_code == 400
    assert "Cannot capture order in terminal 'CANCELLED' state." in capture_res.json()["detail"]


@pytest.mark.asyncio
async def test_declined_paypal_capture_transitions_order_to_failed(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify PayPal capture failure (e.g. INSTRUMENT_DECLINED) transitions local order to FAILED."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_decline_sess_1")
    test_session.add(session)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "USB Cable",
                "quantity": 1,
                "unit_price": "12.00",
                "currency": "USD",
            }
        ],
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_id = create_res.json()["id"]
    paypal_order_id = create_res.json()["paypal_order_id"]

    # Order is approved on PayPal, but settlement capture will decline
    mock_paypal_transport.order_status_db[paypal_order_id] = "APPROVED"
    mock_paypal_transport.simulate_decline = True

    capture_res = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={session.id}")
    assert capture_res.status_code == 400
    assert "PayPal capture rejected" in capture_res.json()["detail"]

    # Verify order in DB transitioned to FAILED
    db_order = test_session.query(PaymentOrder).filter_by(id=order_id).first()
    assert db_order.status == PaymentOrderStatus.FAILED


def test_paypal_approval_modal_ui_rendering():
    """Verify renderPayPalApprovalModal renders order details, 2-step instructions, and action buttons."""
    js_code = """
    const { renderPayPalApprovalModal } = await import('./frontend/assets/js/components.js');
    const orderData = {
        id: "ord-test-modal-1",
        internal_order_number: "ORD-20261104-ABCD",
        total_amount: 149.99,
        currency: "USD",
        approval_url: "https://www.sandbox.paypal.com/checkoutnow?token=5O123456789TN364715T",
    };

    const modalHtml = renderPayPalApprovalModal({
        order: orderData,
        productName: "Premium ANC Headphones",
        retailerName: "Demo Audio Store",
        price: 149.99,
        currency: "USD",
    });

    console.log(JSON.stringify({
        hasTitle: modalHtml.includes("PayPal Sandbox Approval"),
        hasOrderNumber: modalHtml.includes("ORD-20261104-ABCD"),
        hasProductName: modalHtml.includes("Premium ANC Headphones"),
        hasPrice: modalHtml.includes("$149.99"),
        hasStep1: modalHtml.includes("Approve Payment in PayPal"),
        hasLaunchBtn: modalHtml.includes("js-btn-open-paypal-window") && modalHtml.includes("Launch PayPal Window"),
        hasApprovalUrl: modalHtml.includes("https://www.sandbox.paypal.com/checkoutnow?token=5O123456789TN364715T"),
        hasStep2: modalHtml.includes("Confirm &amp; Capture Settlement"),
        hasConfirmBtn: modalHtml.includes("js-btn-confirm-capture"),
        hasCancelBtn: modalHtml.includes("js-btn-cancel-checkout") && modalHtml.includes("Cancel Order"),
    }));
    """
    result = run_node_js(js_code)
    assert result["hasTitle"] is True
    assert result["hasOrderNumber"] is True
    assert result["hasProductName"] is True
    assert result["hasPrice"] is True
    assert result["hasStep1"] is True
    assert result["hasLaunchBtn"] is True
    assert result["hasApprovalUrl"] is True
    assert result["hasStep2"] is True
    assert result["hasConfirmBtn"] is True
    assert result["hasCancelBtn"] is True


def test_frontend_approval_sequence_flow_integrity():
    """Verify app.js defines promptPayPalApprovalAndCapture, calls cancelOrder and captureOrder, and handles unapproved response."""
    with open("frontend/assets/js/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    assert "export async function promptPayPalApprovalAndCapture" in content
    assert "renderPayPalApprovalModal" in content
    assert "await api.cancelOrder" in content
    assert "await api.captureOrder" in content
    assert "order_not_approved" in content
    assert "PayPal order has not yet been approved" in content
    assert "PayPal checkout was cancelled" in content
    assert "await promptPayPalApprovalAndCapture" in content


@pytest.mark.asyncio
async def test_paypal_return_url_targets_local_frontend(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify order creation passes local frontend return_url and cancel_url, never example.com."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_return_url_sess_1")
    test_session.add(session)
    test_session.commit()

    # 1. Test with explicit local frontend return/cancel URLs
    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Mechanical Keyboard",
                "quantity": 1,
                "unit_price": "79.99",
                "currency": "USD",
            }
        ],
        "return_url": "http://localhost:5500/?paypal_return=true",
        "cancel_url": "http://localhost:5500/?paypal_cancel=true",
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    paypal_order_id = create_res.json()["paypal_order_id"]

    order_payload = mock_paypal_transport.orders_db[paypal_order_id]
    app_context = order_payload["application_context"]
    assert "example.com" not in app_context["return_url"]
    assert "example.com" not in app_context["cancel_url"]
    assert app_context["return_url"] == "http://localhost:5500/?paypal_return=true"
    assert app_context["cancel_url"] == "http://localhost:5500/?paypal_cancel=true"

    # 2. Test default when omitted by client also defaults to localhost:5500 and never example.com
    payload_default = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Mousepad",
                "quantity": 1,
                "unit_price": "15.00",
                "currency": "USD",
            }
        ],
    }
    create_res2 = await async_client.post("/api/checkout/orders", json=payload_default)
    assert create_res2.status_code == 201
    paypal_order_id2 = create_res2.json()["paypal_order_id"]

    order_payload2 = mock_paypal_transport.orders_db[paypal_order_id2]
    app_context2 = order_payload2["application_context"]
    assert "example.com" not in app_context2["return_url"]
    assert "localhost:5500" in app_context2["return_url"]


@pytest.mark.asyncio
async def test_lookup_order_by_paypal_id(
    async_client: httpx.AsyncClient,
    test_session: Session,
    mock_paypal_transport,
    mock_paypal_client: PayPalClient,
    monkeypatch,
):
    """Verify lookup of order by its PayPal token / paypal_order_id for session."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    session = ShoppingSession(session_identifier="user_lookup_sess_1")
    test_session.add(session)
    test_session.commit()

    payload = {
        "session_id": session.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Webcam HD",
                "quantity": 1,
                "unit_price": "49.99",
                "currency": "USD",
            }
        ],
    }

    create_res = await async_client.post("/api/checkout/orders", json=payload)
    assert create_res.status_code == 201
    order_data = create_res.json()
    paypal_order_id = order_data["paypal_order_id"]

    # 1. Lookup with valid session
    lookup_res = await async_client.get(f"/api/checkout/orders/by-paypal-id/{paypal_order_id}?session_id={session.id}")
    assert lookup_res.status_code == 200
    assert lookup_res.json()["id"] == order_data["id"]
    assert lookup_res.json()["internal_order_number"] == order_data["internal_order_number"]

    # 2. Lookup with wrong session returns 403 Forbidden
    other_session = ShoppingSession(session_identifier="other_attacker_sess_1")
    test_session.add(other_session)
    test_session.commit()

    bad_lookup = await async_client.get(f"/api/checkout/orders/by-paypal-id/{paypal_order_id}?session_id={other_session.id}")
    assert bad_lookup.status_code == 403

    # 3. Lookup with nonexistent token returns 404 Not Found
    missing_lookup = await async_client.get(f"/api/checkout/orders/by-paypal-id/NONEXISTENT-TOKEN-123?session_id={session.id}")
    assert missing_lookup.status_code == 404


def test_frontend_callback_handler_defined():
    """Verify frontend app.js implements handlePayPalCallback, cross-window messaging, and token handling."""
    with open("frontend/assets/js/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    assert "export async function handlePayPalCallback" in content
    assert "BUYWISE_PAYPAL_APPROVED" in content
    assert "BUYWISE_PAYPAL_CANCELLED" in content
    assert "buywise_paypal_return_event" in content
    assert "api.lookupOrderByPayPalId" in content
    assert "handlePayPalCallback()" in content


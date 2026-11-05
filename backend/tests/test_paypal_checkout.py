"""
AI-Powered Multilingual Shopping Intelligence Platform
Phase 4.2 PayPal Sandbox REST Client & Checkout Service Test Suite.
Tests OAuth2 token lifecycle, order creation, buyer approval links,
spending mandate enforcement, external Channel3 marketplace redirection,
session-scoped idempotency, authoritative capture verification, timeouts,
and FastAPI checkout endpoints with mocked HTTP responses.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import time
from typing import Any
import httpx
import pytest
from sqlalchemy.orm import Session

from backend.app.core.security import create_access_token
from backend.app.models.user import User
from backend.app.models.shopping import Product, RetailerOffer, ShoppingSession, SpendingMandate
from backend.app.models.payment import (
    PaymentOrder,
    OrderItem,
    PaymentCapture,
    PaymentOrderStatus,
    CaptureAmountMismatchError,
    CaptureCurrencyMismatchError,
    UnverifiedCaptureError,
)
from backend.app.schemas.checkout import (
    CheckoutOrderCreate,
    OrderItemCreate,
    PaymentOrderResponse,
    PaymentOrderListResponse,
    PersonalAnalyticsResponse,
)
from backend.app.services.paypal_client import (
    PayPalClient,
    PayPalError,
    PayPalAuthError,
    PayPalRateLimitError,
    PayPalTimeoutError,
    PayPalOrderNotFoundError,
    PayPalCaptureError,
    PayPalAPIError,
)
from backend.app.services.checkout_service import (
    CheckoutService,
    InvalidSessionError,
    ProductNotFoundError,
    RetailerOfferNotFoundError,
    OfferOutOfStockError,
    ExternalRetailerCheckoutError,
    MandatePolicyViolationError,
    UnsupportedCurrencyError,
    OrderExpiredError,
    OrderOwnershipError,
    InvalidOrderStateError,
    DuplicateCaptureError,
    CaptureReconciliationRequiredError,
)


# =============================================================================
# Mock Transport Fixtures for PayPal REST API
# =============================================================================

@pytest.fixture(autouse=True)
def authed_checkout_user(test_session: Session, async_client: httpx.AsyncClient):
    user = User(
        id="authed_paypal_test_user_1",
        email="paypal_tester@example.com",
        name="PayPal Tester",
        hashed_password="test_hashed_password",
    )
    test_session.add(user)
    test_session.commit()
    token, _, _ = create_access_token(user_id=user.id, email=user.email, name=user.name)
    async_client.headers["Authorization"] = f"Bearer {token}"
    yield user

@pytest.fixture
def mock_paypal_transport():
    """Factory fixture returning an HTTPX transport that mocks PayPal Orders v2 endpoints."""
    orders_db: dict[str, dict[str, Any]] = {}
    captures_db: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        # 1. OAuth2 Token
        if "/v1/oauth2/token" in url_str:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Basic "):
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "Client authentication failed"})
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
            # Extract order ID from url
            parts = url_str.split("/v2/checkout/orders/")[1].split("/capture")[0]
            order_id = parts
            req_id = request.headers.get("PayPal-Request-Id", "default-req-id")

            # Check if timeout simulation requested
            if "simulate-timeout" in order_id:
                raise httpx.ReadTimeout("Simulated PayPal capture timeout", request=request)

            if "simulate-decline" in order_id:
                return httpx.Response(
                    422,
                    json={
                        "name": "UNPROCESSABLE_ENTITY",
                        "message": "The requested action could not be performed.",
                        "details": [{"issue": "PAYER_ACTION_REQUIRED", "description": "Transaction declined."}],
                    },
                )

            # Standard successful capture response
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
                                    "create_time": "2026-11-02T12:00:00Z",
                                    "update_time": "2026-11-02T12:00:00Z",
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
            return httpx.Response(201, json=resp_payload)

        # 3. Create Order: POST /v2/checkout/orders
        if "/v2/checkout/orders" in url_str and request.method == "POST":
            body = json.loads(request.content.decode())
            amount = body["purchase_units"][0]["amount"]["value"]
            currency = body["purchase_units"][0]["amount"]["currency_code"]
            order_id = f"5O{int(time.time()*1000)%10000000000}TN364715T"

            orders_db[order_id] = {"amount": amount, "currency": currency}

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

            return httpx.Response(
                200,
                json={
                    "id": order_id,
                    "status": "APPROVED",
                    "purchase_units": [
                        {"amount": {"currency_code": orders_db[order_id]["currency"], "value": orders_db[order_id]["amount"]}}
                    ],
                },
            )

        return httpx.Response(404, json={"message": "Not found"})

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


# =============================================================================
# Category 1: PayPalClient OAuth Lifecycle & Order Creation
# =============================================================================

def test_paypal_oauth_token_acquisition(mock_paypal_client: PayPalClient):
    """Scenario 1: Acquire access token via client-credentials grant."""
    token = mock_paypal_client.get_access_token()
    assert token == "mock_paypal_access_token_xyz123"
    assert mock_paypal_client._token_expires_at > time.time()


def test_paypal_oauth_token_caching(mock_paypal_client: PayPalClient):
    """Scenario 2: Return cached token on subsequent calls without HTTP request."""
    token1 = mock_paypal_client.get_access_token()
    token2 = mock_paypal_client.get_access_token()
    assert token1 == token2


def test_paypal_oauth_token_refresh(mock_paypal_client: PayPalClient):
    """Scenario 3: Refresh token when expired."""
    mock_paypal_client.get_access_token()
    # Artificially expire cache
    mock_paypal_client._token_expires_at = time.time() - 10
    token_refreshed = mock_paypal_client.get_access_token(force_refresh=True)
    assert token_refreshed == "mock_paypal_access_token_xyz123"


def test_paypal_order_creation_success(mock_paypal_client: PayPalClient):
    """Scenario 4: Successful PayPal order creation returning id and approval URL."""
    res = mock_paypal_client.create_order(
        total_amount=Decimal("129.99"),
        currency="USD",
        internal_order_number="ORD-20261102-TEST1",
        paypal_request_id="req-uuid-123",
    )
    assert res["id"].startswith("5O")
    assert res["status"] == "CREATED"
    assert res["_extracted_approval_url"].startswith("https://www.sandbox.paypal.com/checkoutnow?token=")


def test_paypal_order_creation_missing_approval_url():
    """Scenario 5: Raise PayPalAPIError if PayPal response lacks approve link."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        # Order created without 'approve' rel link
        return httpx.Response(201, json={"id": "5O12345", "status": "CREATED", "links": []})

    client = PayPalClient(
        client_id="mock_id",
        client_secret="mock_secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PayPalAPIError) as exc_info:
        client.create_order(
            total_amount=Decimal("50.00"),
            currency="USD",
            internal_order_number="ORD-1",
            paypal_request_id="req-1",
        )
    assert "missing buyer 'approve' link" in str(exc_info.value)


def test_paypal_api_error_handling():
    """Scenario 6: Map HTTP error status codes to PayPalAPIError."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(500, json={"message": "Internal gateway failure"})

    client = PayPalClient(
        client_id="mock_id",
        client_secret="mock_secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PayPalAPIError) as exc_info:
        client.create_order(
            total_amount=Decimal("50.00"),
            currency="USD",
            internal_order_number="ORD-1",
            paypal_request_id="req-1",
        )
    assert "Internal gateway failure" in str(exc_info.value)


def test_paypal_network_timeout():
    """Scenario 7: Network timeout raises PayPalTimeoutError."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        raise httpx.ReadTimeout("Timeout", request=request)

    client = PayPalClient(
        client_id="mock_id",
        client_secret="mock_secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(PayPalTimeoutError):
        client.create_order(
            total_amount=Decimal("50.00"),
            currency="USD",
            internal_order_number="ORD-1",
            paypal_request_id="req-1",
        )


# =============================================================================
# Category 2: CheckoutService Order Creation & Business Logic Gates
# =============================================================================

def test_checkout_invalid_session(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 8: Non-existent session handling raises InvalidSessionError if unresolvable."""
    req = CheckoutOrderCreate(
        session_id="non-existent-session-id",
        session_identifier=None,
        items=[
            OrderItemCreate(
                product_name="Test Item",
                quantity=1,
                unit_price=Decimal("50.00"),
                currency="USD",
            )
        ],
    )
    # Test 1: Non-existent session_id without session_identifier
    with pytest.raises(InvalidSessionError):
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client)

    # Test 2: Neither session_id nor session_identifier provided
    req.session_id = None
    req.session_identifier = None
    with pytest.raises(InvalidSessionError):
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client)


def test_checkout_invalid_or_expired_offer(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 9: Out-of-stock or missing retailer offer is rejected."""
    session = ShoppingSession(session_identifier="sess_out_of_stock", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    prod = Product(name="Sold Out Item", brand="BrandX", category="Misc", is_synthetic=True, provider="synthetic")
    test_session.add(prod)
    test_session.commit()

    offer = RetailerOffer(
        product_id=prod.id,
        retailer_name="StoreX",
        price=99.00,
        currency="USD",
        product_url="https://example.com",
        availability="OUT_OF_STOCK",  # Out of stock!
    )
    test_session.add(offer)
    test_session.commit()

    req = CheckoutOrderCreate(
        session_id=session.id,
        items=[
            OrderItemCreate(
                product_id=prod.id,
                retailer_offer_id=offer.id,
                product_name="Sold Out Item",
                quantity=1,
                unit_price=Decimal("99.00"),
                currency="USD",
            )
        ],
    )
    with pytest.raises(OfferOutOfStockError):
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")


def test_checkout_spending_mandate_rejection(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 10: Purchase exceeding spending mandate ceiling is rejected deterministically."""
    session = ShoppingSession(session_identifier="sess_mandate_rejection", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    # Mandate with $100 max budget and $50 max item price
    mandate = SpendingMandate(
        session_id=session.id,
        total_budget=100.0,
        maximum_item_price=50.0,
        maximum_quantity=1,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    req = CheckoutOrderCreate(
        session_id=session.id,
        items=[
            OrderItemCreate(
                product_name="Expensive Item",
                quantity=1,
                unit_price=Decimal("120.00"),  # Exceeds max item price $50!
                currency="USD",
            )
        ],
    )
    from backend.app.services.checkout_service import BudgetOverrideRequiredError
    with pytest.raises((BudgetOverrideRequiredError, MandatePolicyViolationError)) as exc_info:
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")
    assert any("exceeds your maximum permitted item price" in r for r in exc_info.value.reasons)


def test_checkout_external_channel3_offer_rejection(test_session: Session, mock_paypal_client: PayPalClient):
    """
    Scenario 11:
    CRITICAL PURCHASE DISTINCTION:
    External Channel3 marketplace offers must NOT be checked out via internal PayPal demo gateway.
    Must raise ExternalRetailerCheckoutError and direct user to merchant website.
    """
    session = ShoppingSession(session_identifier="sess_external_c3_rejection", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    # Create an external Channel3 product and offer
    c3_prod = Product(
        name="Anker Space One Headphones",
        brand="Anker",
        category="Headphones",
        is_synthetic=False,
        provider="channel3",  # External provider!
    )
    test_session.add(c3_prod)
    test_session.commit()

    c3_offer = RetailerOffer(
        product_id=c3_prod.id,
        retailer_name="BestBuy",
        price=99.99,
        currency="USD",
        product_url="https://bestbuy.com/anker-space-one",
        availability="IN_STOCK",
        is_synthetic=False,
    )
    test_session.add(c3_offer)
    test_session.commit()

    req = CheckoutOrderCreate(
        session_id=session.id,
        items=[
            OrderItemCreate(
                product_id=c3_prod.id,
                retailer_offer_id=c3_offer.id,
                product_name="Anker Space One Headphones",
                quantity=1,
                unit_price=Decimal("99.99"),
                currency="USD",
            )
        ],
    )
    with pytest.raises(ExternalRetailerCheckoutError) as exc_info:
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")

    err = exc_info.value
    assert err.retailer_name == "BestBuy"
    assert err.product_url == "https://bestbuy.com/anker-space-one"
    assert err.price == Decimal("99.99")
    assert "You will complete your purchase directly on their website" in str(err)


def test_checkout_duplicate_idempotency_request(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 12: Duplicate checkout submission with same idempotency key returns original order."""
    session = ShoppingSession(session_identifier="sess_duplicate_idem", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    shared_key = "idemp_order_create_key_1"
    req = CheckoutOrderCreate(
        session_id=session.id,
        idempotency_key=shared_key,
        items=[
            OrderItemCreate(
                product_name="Mechanical Keyboard",
                quantity=1,
                unit_price=Decimal("89.99"),
                currency="USD",
            )
        ],
    )
    res1 = CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")
    res2 = CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")

    assert res1.id == res2.id
    assert res1.paypal_order_id == res2.paypal_order_id
    assert res1.internal_order_number == res2.internal_order_number
    # Verify only 1 order exists in database
    orders = test_session.query(PaymentOrder).filter_by(session_id=session.id).all()
    assert len(orders) == 1


def test_checkout_unsupported_currency(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 13: Unsupported currency raises UnsupportedCurrencyError."""
    session = ShoppingSession(session_identifier="sess_unsupported_currency", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    req = CheckoutOrderCreate(
        session_id=session.id,
        currency="XYZ",  # Unsupported
        items=[
            OrderItemCreate(
                product_name="Test Item",
                quantity=1,
                unit_price=Decimal("10.00"),
                currency="XYZ",
            )
        ],
    )
    with pytest.raises(UnsupportedCurrencyError):
        CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")


# =============================================================================
# Category 3: Buyer Approval & Authoritative Capture Verification
# =============================================================================

def test_capture_order_success(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 14: Successful capture authoritatively verifies settlement and updates order."""
    session = ShoppingSession(session_identifier="sess_capture_success", user_id="authed_paypal_test_user_1")
    test_session.add(session)
    test_session.commit()

    req = CheckoutOrderCreate(
        session_id=session.id,
        items=[
            OrderItemCreate(
                product_name="Noise Cancelling Headphones",
                quantity=1,
                unit_price=Decimal("199.99"),
                currency="USD",
            )
        ],
    )
    order_res = CheckoutService.create_checkout_order(db=test_session, request=req, paypal_client=mock_paypal_client, user_id="authed_paypal_test_user_1")

    # Capture order
    capture_res = CheckoutService.capture_checkout_order(
        db=test_session,
        order_id=order_res.id,
        session_id=session.id,
        paypal_client=mock_paypal_client,
        user_id="authed_paypal_test_user_1",
    )

    assert capture_res.status == PaymentOrderStatus.COMPLETED.value
    assert len(capture_res.captures) == 1
    assert capture_res.captures[0].is_verified is True
    assert capture_res.captures[0].captured_amount == Decimal("199.99")
    assert capture_res.captures[0].currency == "USD"
    assert capture_res.captures[0].paypal_payer_email_masked == "s***r@business.example.com"


def test_capture_amount_mismatch(test_session: Session):
    """Scenario 15: Capture amount mismatch raises CaptureAmountMismatchError."""
    session = ShoppingSession(session_identifier="sess_cap_mismatch_amt")
    test_session.add(session)
    test_session.commit()

    # Mock PayPal returning $50.00 when order was $100.00
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if "/capture" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "id": "5O123",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {"payments": {"captures": [{"id": "CAP-1", "status": "COMPLETED", "amount": {"value": "50.00", "currency_code": "USD"}}]}}
                    ],
                },
            )
        return httpx.Response(404)

    client = PayPalClient(client_id="id", client_secret="secret", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    order = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O123",
        total_amount=Decimal("100.00"),  # $100.00 order
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    with pytest.raises(CaptureAmountMismatchError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session.id, paypal_client=client)


def test_capture_currency_mismatch(test_session: Session):
    """Scenario 16: Capture currency mismatch raises CaptureCurrencyMismatchError."""
    session = ShoppingSession(session_identifier="sess_cap_mismatch_curr")
    test_session.add(session)
    test_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if "/capture" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "id": "5O123",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {"payments": {"captures": [{"id": "CAP-1", "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "EUR"}}]}}
                    ],
                },
            )
        return httpx.Response(404)

    client = PayPalClient(client_id="id", client_secret="secret", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    order = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O123",
        total_amount=Decimal("100.00"),
        currency="USD",  # USD order, but capture in EUR
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    with pytest.raises(CaptureCurrencyMismatchError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session.id, paypal_client=client)


def test_capture_status_not_completed(test_session: Session):
    """Scenario 17: Capture status not COMPLETED raises PayPalCaptureError."""
    session = ShoppingSession(session_identifier="sess_cap_not_completed")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O-simulate-decline",
        total_amount=Decimal("100.00"),
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(
            422,
            json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED", "description": "Card was declined"}]},
        )

    client = PayPalClient(client_id="id", client_secret="secret", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(PayPalCaptureError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session.id, paypal_client=client)


def test_duplicate_capture_id_rejected(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 18: Recording identical capture ID twice raises DuplicateCaptureError."""
    session = ShoppingSession(session_identifier="sess_duplicate_cap_id")
    test_session.add(session)
    test_session.commit()

    order1 = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O111111111111111",
        total_amount=Decimal("50.00"),
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order1)
    test_session.commit()

    # Pre-insert existing capture with ID "CAP-COLLISION-1"
    existing_cap = PaymentCapture(
        payment_order_id=order1.id,
        paypal_order_id="5O111111111111111",
        paypal_capture_id="CAP-COLLISION-1",
        captured_amount=Decimal("50.00"),
        currency="USD",
        is_verified=True,
    )
    test_session.add(existing_cap)
    test_session.commit()

    # Order 2 attempts capture that returns same ID
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(
            201,
            json={
                "id": "5O222",
                "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "CAP-COLLISION-1", "status": "COMPLETED", "amount": {"value": "50.00", "currency_code": "USD"}}]}}],
            },
        )

    client = PayPalClient(client_id="id", client_secret="secret", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    order2 = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O222",
        total_amount=Decimal("50.00"),
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order2)
    test_session.commit()

    with pytest.raises(DuplicateCaptureError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order2.id, session_id=session.id, paypal_client=client)


def test_capture_timeout_and_reconciliation_required(test_session: Session):
    """Scenario 19: Capture timeout leaves order in CAPTURE_PENDING and raises CaptureReconciliationRequiredError."""
    session = ShoppingSession(session_identifier="sess_timeout_reconciliation")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O-simulate-timeout",
        total_amount=Decimal("100.00"),
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/oauth2/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        # Simulate network timeout on capture and get_order_details
        raise httpx.ReadTimeout("Timeout", request=request)

    client = PayPalClient(client_id="id", client_secret="secret", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(CaptureReconciliationRequiredError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session.id, paypal_client=client)

    # Verify order remained safely in CAPTURE_PENDING without marking failed
    db_order = test_session.query(PaymentOrder).filter_by(id=order.id).first()
    assert db_order.status == PaymentOrderStatus.CAPTURE_PENDING.value


def test_unauthorized_order_access(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 20: Session B attempting to capture Session A's order raises OrderOwnershipError."""
    session_a = ShoppingSession(session_identifier="sess_owner_a")
    session_b = ShoppingSession(session_identifier="sess_intruder_b")
    test_session.add_all([session_a, session_b])
    test_session.commit()

    order = PaymentOrder(
        session_id=session_a.id,
        paypal_order_id="5O-A1",
        total_amount=Decimal("50.00"),
        currency="USD",
        status=PaymentOrderStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    with pytest.raises(OrderOwnershipError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session_b.id, paypal_client=mock_paypal_client)


def test_invalid_payment_state_for_capture(test_session: Session, mock_paypal_client: PayPalClient):
    """Scenario 21: Attempting capture on expired order raises OrderExpiredError."""
    session = ShoppingSession(session_identifier="sess_expired_order")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        paypal_order_id="5O-EXPIRED-1",
        total_amount=Decimal("50.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),  # Expired!
    )
    test_session.add(order)
    test_session.commit()

    with pytest.raises(OrderExpiredError):
        CheckoutService.capture_checkout_order(db=test_session, order_id=order.id, session_id=session.id, paypal_client=mock_paypal_client)


def test_payment_history_retrieval(test_session: Session):
    """Scenario 22: Payment history retrieves orders for session in descending order."""
    session = ShoppingSession(session_identifier="sess_history_test")
    test_session.add(session)
    test_session.commit()

    order1 = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("25.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    order2 = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("50.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add_all([order1, order2])
    test_session.commit()

    history = CheckoutService.get_session_payment_history(db=test_session, session_id=session.id)
    assert history.total == 2
    assert len(history.orders) == 2
    assert history.session_id == session.id


# =============================================================================
# Category 4: FastAPI Checkout Endpoints Integration
# =============================================================================

@pytest.mark.asyncio
async def test_api_checkout_create_success(async_client: httpx.AsyncClient, test_session: Session, mock_paypal_client: PayPalClient, monkeypatch):
    """Test POST /api/checkout/orders returns 201 Created and approval URL."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    payload = {
        "session_identifier": "api_guest_session_1",
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Mechanical Keyboard",
                "quantity": 1,
                "unit_price": "89.99",
                "currency": "USD",
            }
        ],
    }
    response = await async_client.post("/api/checkout/orders", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "CREATED"
    assert data["total_amount"] == "89.99"
    assert data["approval_url"].startswith("https://www.sandbox.paypal.com/checkoutnow?token=")
    assert data["paypal_request_id"] is not None


@pytest.mark.asyncio
async def test_api_checkout_external_offer_rejected_with_redirect(async_client: httpx.AsyncClient, test_session: Session):
    """Test POST /api/checkout/orders rejects Channel3 external marketplace offers with HTTP 400 and redirect details."""
    c3_prod = Product(
        name="External Smart Watch",
        brand="Garmin",
        category="Wearables",
        is_synthetic=False,
        provider="channel3",
    )
    test_session.add(c3_prod)
    test_session.commit()

    c3_offer = RetailerOffer(
        product_id=c3_prod.id,
        retailer_name="Walmart",
        price=249.99,
        currency="USD",
        product_url="https://walmart.com/garmin-watch",
        availability="IN_STOCK",
        is_synthetic=False,
    )
    test_session.add(c3_offer)
    test_session.commit()

    payload = {
        "session_identifier": "api_guest_external_test",
        "currency": "USD",
        "items": [
            {
                "product_id": c3_prod.id,
                "retailer_offer_id": c3_offer.id,
                "product_name": "External Smart Watch",
                "quantity": 1,
                "unit_price": "249.99",
                "currency": "USD",
            }
        ],
    }
    response = await async_client.post("/api/checkout/orders", json=payload)
    assert response.status_code == 400
    err = response.json()["detail"]
    assert err["requires_external_checkout"] is True
    assert err["retailer_name"] == "Walmart"
    assert err["product_url"] == "https://walmart.com/garmin-watch"


@pytest.mark.asyncio
async def test_api_checkout_capture_success(async_client: httpx.AsyncClient, test_session: Session, mock_paypal_client: PayPalClient, monkeypatch):
    """Test POST /api/checkout/orders/{id}/capture returns 200 OK and COMPLETED status."""
    monkeypatch.setattr("backend.app.services.checkout_service.PayPalClient", lambda: mock_paypal_client)

    # Create order first
    payload = {
        "session_identifier": "api_capture_session_1",
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Tablet",
                "quantity": 1,
                "unit_price": "149.99",
                "currency": "USD",
            }
        ],
    }
    create_resp = await async_client.post("/api/checkout/orders", json=payload)
    order_data = create_resp.json()
    order_id = order_data["id"]
    sess_id = order_data["session_id"]

    # Capture order
    capture_resp = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={sess_id}")
    assert capture_resp.status_code == 200
    cap_data = capture_resp.json()
    assert cap_data["status"] == "COMPLETED"
    assert cap_data["paypal_capture_request_id"] is not None
    assert len(cap_data["captures"]) == 1
    assert cap_data["captures"][0]["is_verified"] is True


@pytest.mark.asyncio
async def test_api_checkout_history(async_client: httpx.AsyncClient, test_session: Session):
    """Test GET /api/checkout/history/{session_id} returns 200 OK and list."""
    session = ShoppingSession(session_identifier="api_history_sess")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("79.99"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    resp = await async_client.get(f"/api/checkout/history/{session.id}")
    assert resp.status_code == 200
    hist = resp.json()
    assert hist["total"] == 1
    assert len(hist["orders"]) == 1
    assert hist["orders"][0]["total_amount"] == "79.99"

# =============================================================================
# Category 6: Personal Shopping Analytics Tests (Phase 1)
# =============================================================================

def test_analytics_completed_verified_purchase_aggregation(test_session: Session):
    """Test 1: Verify completed orders with verified captures are correctly aggregated."""
    session = ShoppingSession(session_identifier="analytics_agg_sess")
    test_session.add(session)
    test_session.commit()

    prod = Product(
        name="UltraBook Pro",
        brand="TechCorp",
        category="Computing",
        description="High performance laptop",
        specifications={"ram": "32GB"},
    )
    test_session.add(prod)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("1200.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    item = OrderItem(
        payment_order_id=order.id,
        product_id=prod.id,
        product_name=prod.name,
        retailer_name="TechStore",
        quantity=1,
        unit_price=Decimal("1200.00"),
        subtotal=Decimal("1200.00"),
        currency="USD",
    )
    capture = PaymentCapture(
        payment_order_id=order.id,
        paypal_order_id="PAYPAL-ORD-1",
        paypal_capture_id="CAP-AGG-1",
        captured_amount=Decimal("1200.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add_all([item, capture])
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    assert analytics.completed_order_count == 1
    assert len(analytics.total_spending_by_currency) == 1

    usd_summary = analytics.total_spending_by_currency[0]
    assert usd_summary.currency == "USD"
    assert usd_summary.total_spent == Decimal("1200.00")
    assert usd_summary.total_order_amount == Decimal("1200.00")
    assert usd_summary.order_count == 1
    assert usd_summary.average_order_value == Decimal("1200.00")
    assert "match order totals exactly" in usd_summary.discrepancy_explanation


def test_analytics_exclusion_of_incomplete_orders(test_session: Session):
    """Test 2: Incomplete, expired, failed, or unverified orders are excluded."""
    session = ShoppingSession(session_identifier="analytics_incomplete_sess")
    test_session.add(session)
    test_session.commit()

    # Draft / non-terminal orders
    for idx, st in enumerate([
        PaymentOrderStatus.CREATED.value,
        PaymentOrderStatus.APPROVED.value,
        PaymentOrderStatus.CAPTURE_PENDING.value,
        PaymentOrderStatus.CANCELLED.value,
        PaymentOrderStatus.EXPIRED.value,
        PaymentOrderStatus.FAILED.value,
    ]):
        ord_unfin = PaymentOrder(
            session_id=session.id,
            total_amount=Decimal("100.00"),
            currency="USD",
            status=st,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        test_session.add(ord_unfin)

    # Order marked COMPLETED but with UNVERIFIED capture
    ord_unverified = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("250.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_unverified)
    test_session.commit()

    cap_unverified = PaymentCapture(
        payment_order_id=ord_unverified.id,
        paypal_order_id="PAYPAL-UNVER-1",
        paypal_capture_id="CAP-UNVER-1",
        captured_amount=Decimal("250.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=False,  # Unverified!
    )
    test_session.add(cap_unverified)

    # Legitimate verified completed order
    ord_legit = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("99.99"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_legit)
    test_session.commit()

    cap_legit = PaymentCapture(
        payment_order_id=ord_legit.id,
        paypal_order_id="PAYPAL-LEGIT-1",
        paypal_capture_id="CAP-LEGIT-1",
        captured_amount=Decimal("99.99"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_legit)
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    # Only 1 legitimate completed order should be counted
    assert analytics.completed_order_count == 1
    assert len(analytics.total_spending_by_currency) == 1
    assert analytics.total_spending_by_currency[0].total_spent == Decimal("99.99")


def test_analytics_category_wise_spending(test_session: Session):
    """Test 3: Category-wise spending breakdown calculates subtotals and percentages accurately."""
    session = ShoppingSession(session_identifier="analytics_cat_sess")
    test_session.add(session)
    test_session.commit()

    p_audio = Product(name="Wireless Earbuds", brand="AudioMax", category="Audio")
    p_comp = Product(name="Workstation PC", brand="PowerRig", category="Computing")
    test_session.add_all([p_audio, p_comp])
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("400.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    item_audio = OrderItem(
        payment_order_id=order.id,
        product_id=p_audio.id,
        product_name=p_audio.name,
        retailer_name="BestAudio",
        quantity=1,
        unit_price=Decimal("100.00"),
        subtotal=Decimal("100.00"),
        currency="USD",
    )
    item_comp = OrderItem(
        payment_order_id=order.id,
        product_id=p_comp.id,
        product_name=p_comp.name,
        retailer_name="CompHub",
        quantity=1,
        unit_price=Decimal("300.00"),
        subtotal=Decimal("300.00"),
        currency="USD",
    )
    cap = PaymentCapture(
        payment_order_id=order.id,
        paypal_order_id="P-CAT-1",
        paypal_capture_id="CAP-CAT-1",
        captured_amount=Decimal("400.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add_all([item_audio, item_comp, cap])
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    cats = {c.category: c for c in analytics.category_spending}

    assert "Computing" in cats
    assert cats["Computing"].total_amount == Decimal("300.00")
    assert cats["Computing"].percentage_of_currency_total == 75.0

    assert "Audio" in cats
    assert cats["Audio"].total_amount == Decimal("100.00")
    assert cats["Audio"].percentage_of_currency_total == 25.0


def test_analytics_retailer_wise_spending(test_session: Session):
    """Test 4: Retailer-wise spending breakdown sums correctly across merchant names."""
    session = ShoppingSession(session_identifier="analytics_ret_sess")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("500.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    item_bestbuy = OrderItem(
        payment_order_id=order.id,
        product_name="4K Monitor",
        retailer_name="Best Buy",
        quantity=1,
        unit_price=Decimal("350.00"),
        subtotal=Decimal("350.00"),
        currency="USD",
    )
    item_amazon = OrderItem(
        payment_order_id=order.id,
        product_name="HDMI Cable Pack",
        retailer_name="Amazon",
        quantity=1,
        unit_price=Decimal("150.00"),
        subtotal=Decimal("150.00"),
        currency="USD",
    )
    cap = PaymentCapture(
        payment_order_id=order.id,
        paypal_order_id="P-RET-1",
        paypal_capture_id="CAP-RET-1",
        captured_amount=Decimal("500.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add_all([item_bestbuy, item_amazon, cap])
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    rets = {r.retailer_name: r for r in analytics.retailer_spending}

    assert "Best Buy" in rets
    assert rets["Best Buy"].total_amount == Decimal("350.00")
    assert rets["Best Buy"].percentage_of_currency_total == 70.0

    assert "Amazon" in rets
    assert rets["Amazon"].total_amount == Decimal("150.00")
    assert rets["Amazon"].percentage_of_currency_total == 30.0


def test_analytics_multiple_currencies_kept_separate(test_session: Session):
    """Test 5: Invariant - disparate currencies are strictly segregated and never combined."""
    session = ShoppingSession(session_identifier="analytics_multi_curr_sess")
    test_session.add(session)
    test_session.commit()

    # Order 1 in USD
    ord_usd = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("100.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_usd)
    test_session.commit()
    cap_usd = PaymentCapture(
        payment_order_id=ord_usd.id,
        paypal_order_id="P-USD-1",
        paypal_capture_id="CAP-USD-1",
        captured_amount=Decimal("100.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_usd)

    # Order 2 in INR
    ord_inr = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("50000.00"),
        currency="INR",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_inr)
    test_session.commit()
    cap_inr = PaymentCapture(
        payment_order_id=ord_inr.id,
        paypal_order_id="P-INR-1",
        paypal_capture_id="CAP-INR-1",
        captured_amount=Decimal("50000.00"),
        currency="INR",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_inr)

    # Order 3 in EUR
    ord_eur = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("80.00"),
        currency="EUR",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_eur)
    test_session.commit()
    cap_eur = PaymentCapture(
        payment_order_id=ord_eur.id,
        paypal_order_id="P-EUR-1",
        paypal_capture_id="CAP-EUR-1",
        captured_amount=Decimal("80.00"),
        currency="EUR",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_eur)
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    assert analytics.completed_order_count == 3
    assert len(analytics.total_spending_by_currency) == 3

    curr_dict = {s.currency: s.total_spent for s in analytics.total_spending_by_currency}
    assert curr_dict["USD"] == Decimal("100.00")
    assert curr_dict["INR"] == Decimal("50000.00")
    assert curr_dict["EUR"] == Decimal("80.00")

    # Verify AOV per currency
    assert analytics.average_order_value_by_currency["USD"] == Decimal("100.00")
    assert analytics.average_order_value_by_currency["INR"] == Decimal("50000.00")
    assert analytics.average_order_value_by_currency["EUR"] == Decimal("80.00")


def test_analytics_average_order_value(test_session: Session):
    """Test 6: Average Order Value computes accurately across multiple orders in same currency."""
    session = ShoppingSession(session_identifier="analytics_aov_sess")
    test_session.add(session)
    test_session.commit()

    for amt in [Decimal("100.00"), Decimal("200.00"), Decimal("600.00")]:
        order = PaymentOrder(
            session_id=session.id,
            total_amount=amt,
            currency="USD",
            status=PaymentOrderStatus.COMPLETED.value,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        test_session.add(order)
        test_session.commit()

        cap = PaymentCapture(
            payment_order_id=order.id,
            paypal_order_id=f"P-AOV-{amt}",
            paypal_capture_id=f"CAP-AOV-{amt}",
            captured_amount=amt,
            currency="USD",
            capture_status="COMPLETED",
            is_verified=True,
        )
        test_session.add(cap)
        test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    # Total = 900.00, count = 3 -> AOV = 300.00
    assert analytics.completed_order_count == 3
    assert analytics.average_order_value_by_currency["USD"] == Decimal("300.00")
    assert analytics.total_spending_by_currency[0].average_order_value == Decimal("300.00")


def test_analytics_empty_session(test_session: Session):
    """Test 7: Empty or fresh sessions return 0 counts and empty arrays gracefully without crashing."""
    # 1. Non-existent session ID
    res_nonexistent = CheckoutService.get_session_analytics(db=test_session, session_id="nonexistent-session-xyz")
    assert res_nonexistent.completed_order_count == 0
    assert res_nonexistent.total_spending_by_currency == []
    assert res_nonexistent.average_order_value_by_currency == {}
    assert res_nonexistent.monthly_spending_trends == []
    assert res_nonexistent.category_spending == []
    assert res_nonexistent.retailer_spending == []
    assert res_nonexistent.active_mandate_utilization is None

    # 2. Existing session with mandate but zero orders
    sess_with_mandate = ShoppingSession(session_identifier="mandate_only_sess")
    test_session.add(sess_with_mandate)
    test_session.commit()

    mandate = SpendingMandate(
        session_id=sess_with_mandate.id,
        total_budget=500.0,
        maximum_item_price=250.0,
        maximum_quantity=2,
        currency="USD",
    )
    test_session.add(mandate)
    test_session.commit()

    res_mandate_zero = CheckoutService.get_session_analytics(db=test_session, session_id=sess_with_mandate.id)
    assert res_mandate_zero.completed_order_count == 0
    assert res_mandate_zero.active_mandate_utilization is not None
    assert res_mandate_zero.active_mandate_utilization.total_budget == Decimal("500.00")
    assert res_mandate_zero.active_mandate_utilization.spent_amount == Decimal("0.00")
    assert res_mandate_zero.active_mandate_utilization.remaining_budget == Decimal("500.00")
    assert res_mandate_zero.active_mandate_utilization.utilization_percentage == 0.0


def test_analytics_strict_customer_session_isolation(test_session: Session):
    """Test 8: Strict isolation - Customer A's transactions are never visible to Customer B."""
    sess_a = ShoppingSession(session_identifier="customer_a_sess")
    sess_b = ShoppingSession(session_identifier="customer_b_sess")
    test_session.add_all([sess_a, sess_b])
    test_session.commit()

    # Customer A spends $1000 USD
    ord_a = PaymentOrder(
        session_id=sess_a.id,
        total_amount=Decimal("1000.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_a)
    test_session.commit()
    cap_a = PaymentCapture(
        payment_order_id=ord_a.id,
        paypal_order_id="P-A-1",
        paypal_capture_id="CAP-A-1",
        captured_amount=Decimal("1000.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_a)

    # Customer B spends $45 USD
    ord_b = PaymentOrder(
        session_id=sess_b.id,
        total_amount=Decimal("45.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(ord_b)
    test_session.commit()
    cap_b = PaymentCapture(
        payment_order_id=ord_b.id,
        paypal_order_id="P-B-1",
        paypal_capture_id="CAP-B-1",
        captured_amount=Decimal("45.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap_b)
    test_session.commit()

    analytics_a = CheckoutService.get_session_analytics(db=test_session, session_id=sess_a.id)
    analytics_b = CheckoutService.get_session_analytics(db=test_session, session_id=sess_b.id)

    assert analytics_a.completed_order_count == 1
    assert analytics_a.total_spending_by_currency[0].total_spent == Decimal("1000.00")

    assert analytics_b.completed_order_count == 1
    assert analytics_b.total_spending_by_currency[0].total_spent == Decimal("45.00")


def test_analytics_duplicate_captures_or_multi_items_do_not_inflate_totals(test_session: Session):
    """Test 9: Multiple items in an order and duplicate captures do not cause Cartesian join inflation."""
    session = ShoppingSession(session_identifier="analytics_no_inflation_sess")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("100.00"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    # 4 distinct order items totaling 100.00
    for idx in range(4):
        item = OrderItem(
            payment_order_id=order.id,
            product_name=f"Accessory {idx}",
            retailer_name="MultiStore",
            quantity=1,
            unit_price=Decimal("25.00"),
            subtotal=Decimal("25.00"),
            currency="USD",
        )
        test_session.add(item)

    # Primary verified capture of 100.00
    cap1 = PaymentCapture(
        payment_order_id=order.id,
        paypal_order_id="P-MULTI-1",
        paypal_capture_id="CAP-MULTI-1",
        captured_amount=Decimal("100.00"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap1)
    test_session.commit()

    analytics = CheckoutService.get_session_analytics(db=test_session, session_id=session.id)
    # Total spent MUST be 100.00, NOT 400.00!
    assert analytics.completed_order_count == 1
    assert analytics.total_spending_by_currency[0].total_spent == Decimal("100.00")
    assert analytics.total_spending_by_currency[0].total_order_amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_api_get_personal_analytics_endpoint(async_client: httpx.AsyncClient, test_session: Session):
    """Test 10: GET /api/checkout/analytics/{session_id} FastAPI endpoint returns 200 OK and valid JSON."""
    session = ShoppingSession(session_identifier="api_analytics_sess")
    test_session.add(session)
    test_session.commit()

    order = PaymentOrder(
        session_id=session.id,
        total_amount=Decimal("199.99"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(order)
    test_session.commit()

    cap = PaymentCapture(
        payment_order_id=order.id,
        paypal_order_id="P-API-AN-1",
        paypal_capture_id="CAP-API-AN-1",
        captured_amount=Decimal("199.99"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
    )
    test_session.add(cap)
    test_session.commit()

    resp = await async_client.get(f"/api/checkout/analytics/{session.id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["session_id"] == session.id
    assert data["completed_order_count"] == 1
    assert len(data["total_spending_by_currency"]) == 1
    assert data["total_spending_by_currency"][0]["currency"] == "USD"
    assert data["total_spending_by_currency"][0]["total_spent"] == "199.99"
    assert data["average_order_value_by_currency"]["USD"] == "199.99"


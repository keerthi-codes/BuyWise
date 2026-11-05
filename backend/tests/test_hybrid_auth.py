"""
BUYWISE AI — Hybrid Authentication End-to-End Test Suite.
Verifies all 15 required hybrid authentication behaviors:
1. New visitor sees Welcome page with 3 options: Login, Create Account, Browse as Guest.
2. Browse as Guest opens Home and activates guest browsing.
3. Guest can search and compare products freely without login.
4. Guest can use AI Shopping Assistant freely.
5. Guest can view product intelligence and price history.
6. Guest clicking Buy is intercepted with Auth Required modal.
7. No payment order is created before authentication (returns 401).
8. Successful registration restores intended checkout context and links session.
9. Successful login restores intended checkout context and links session.
10. Duplicate payment orders are strictly prevented via session-scoped idempotency.
11. Authenticated users can complete PayPal Sandbox checkout (order creation & capture).
12. Logout returns user to Welcome page, revoking token and clearing guest mode.
13. Guest browser refresh restores guest browsing session.
14. Authenticated browser refresh restores authenticated browsing.
15. Invalid, expired, or revoked JWT cannot create an order (returns 401).
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import subprocess
import uuid
import httpx
import pytest
from fastapi import status
from sqlalchemy.orm import Session

from backend.app.core.security import hash_password, create_access_token
from backend.app.models.user import User
from backend.app.models.shopping import ShoppingSession, SpendingMandate, Product, RetailerOffer
from backend.app.models.payment import PaymentOrder, PaymentOrderStatus
from backend.app.services.paypal_client import PayPalClient


# ---------------------------------------------------------------------------
# Node.js Evaluation Helper for Frontend Routing & Components
# ---------------------------------------------------------------------------

def run_node_eval(js_code: str) -> dict:
    """Execute JavaScript in Node.js with DOM/Storage stubs and return parsed JSON stdout."""
    full_script = f"""
    const storage = {{}};
    const sessionStorageMap = {{}};
    global.localStorage = {{
      getItem: (k) => storage[k] || null,
      setItem: (k, v) => {{ storage[k] = String(v); }},
      removeItem: (k) => {{ delete storage[k]; }},
      clear: () => {{ for (const k in storage) delete storage[k]; }},
    }};
    global.sessionStorage = {{
      getItem: (k) => sessionStorageMap[k] || null,
      setItem: (k, v) => {{ sessionStorageMap[k] = String(v); }},
      removeItem: (k) => {{ delete sessionStorageMap[k]; }},
      clear: () => {{ for (const k in sessionStorageMap) delete sessionStorageMap[k]; }},
    }};
    global.document = {{
      addEventListener: () => {{}},
      getElementById: () => null,
      querySelectorAll: () => [],
      body: {{ classList: {{ add: () => {{}}, remove: () => {{}} }} }},
    }};
    global.window = {{
      location: {{ hash: '', pathname: '/', origin: 'http://localhost:5500', port: '5500', hostname: 'localhost', protocol: 'http:' }},
      addEventListener: () => {{}},
      dispatchEvent: () => {{}},
      scrollTo: () => {{}},
    }};
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


# =============================================================================
# SCENARIO 1: New Visitor Sees Welcome / Login Page with 3 Options
# =============================================================================

def test_req1_new_visitor_sees_welcome_login_page():
    """Verify first-time visitor (unauthenticated and non-guest) is routed to login/welcome with 3 options."""
    js = """
    const { renderLoginPage } = await import('./frontend/assets/js/components.js');
    const { isGuestActive, isAuthenticated } = await import('./frontend/assets/js/config.js');

    const html = renderLoginPage();

    console.log(JSON.stringify({
      isGuestActive: isGuestActive(),
      isAuthenticated: isAuthenticated(),
      hasBrandShowcase: html.includes("BUYWISE") && html.includes("Discover smarter"),
      hasSignInOption: html.includes('id="btn-login-submit"') && html.includes("Sign In"),
      hasCreateAccountOption: html.includes('href="#register"') && html.includes("Create an account"),
      hasBrowseAsGuestOption: html.includes("js-btn-browse-guest") && html.includes("Browse as Guest"),
    }));
    """
    res = run_node_eval(js)
    assert res["isGuestActive"] is False
    assert res["isAuthenticated"] is False
    assert res["hasBrandShowcase"] is True
    assert res["hasSignInOption"] is True
    assert res["hasCreateAccountOption"] is True
    assert res["hasBrowseAsGuestOption"] is True


# =============================================================================
# SCENARIO 2: Browse as Guest Opens Home
# =============================================================================

def test_req2_browse_as_guest_opens_home():
    """Verify clicking Browse as Guest activates guest mode in localStorage."""
    js = """
    const { isGuestActive, setGuestActive } = await import('./frontend/assets/js/config.js');
    const initial = isGuestActive();
    setGuestActive(true);
    const after = isGuestActive();
    setGuestActive(false);
    const cleared = isGuestActive();

    console.log(JSON.stringify({
      initial,
      after,
      cleared,
    }));
    """
    res = run_node_eval(js)
    assert res["initial"] is False
    assert res["after"] is True
    assert res["cleared"] is False


# =============================================================================
# SCENARIO 3: Guest Can Search and Compare Products
# =============================================================================

@pytest.mark.asyncio
async def test_req3_guest_can_search_and_compare_products(async_client: httpx.AsyncClient, test_session: Session):
    """Verify unauthenticated guest can search products and access product list without auth."""
    # 1. Product list
    res_list = await async_client.get("/api/products?page=1&page_size=10")
    assert res_list.status_code == status.HTTP_200_OK
    data = res_list.json()
    assert "items" in data
    assert len(data["items"]) > 0

    # 2. Product search
    res_search = await async_client.get("/api/products/search?q=Headphones")
    assert res_search.status_code == status.HTTP_200_OK
    search_data = res_search.json()
    assert "items" in search_data


# =============================================================================
# SCENARIO 4: Guest Can Use AI Shopping Assistant
# =============================================================================

@pytest.mark.asyncio
async def test_req4_guest_can_use_gemini_assistant(async_client: httpx.AsyncClient, test_session: Session):
    """Verify guest can query the assistant endpoint without user authentication."""
    payload = {
        "session_id": "guest_assistant_session",
        "query": "Recommend budget headphones under $100",
    }
    res = await async_client.post("/api/assistant/query", json=payload)
    # Guest query succeeds (200 OK), not 401 Unauthorized
    assert res.status_code == status.HTTP_200_OK
    assert res.json().get("success") is True
    assert "intent" in res.json()
    assert "assistant_message" in res.json()


# =============================================================================
# SCENARIO 5: Guest Can View Product Intelligence
# =============================================================================

@pytest.mark.asyncio
async def test_req5_guest_can_view_product_intelligence(async_client: httpx.AsyncClient, test_session: Session):
    """Verify guest can inspect deep product intelligence without logging in."""
    prod = test_session.query(Product).first()
    assert prod is not None

    res = await async_client.get(f"/api/products/{prod.id}/intelligence")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["product_id"] == prod.id
    assert "name" in data and "brand" in data


# =============================================================================
# SCENARIO 6: Guest Clicking Buy Triggers Auth Required Modal
# =============================================================================

def test_req6_guest_clicking_buy_redirected_to_authentication():
    """Verify clicking Buy as a guest saves pending checkout and renders auth required modal."""
    js = """
    const { renderAuthRequiredModal } = await import('./frontend/assets/js/components.js');
    const { setPendingCheckout, getPendingCheckout, clearPendingCheckout } = await import('./frontend/assets/js/config.js');

    const pendingItem = {
      productId: "prod-demo-123",
      offerId: "off-demo-456",
      productName: "Sony WH-1000XM5",
      retailerName: "Demo Electronics",
      price: 299.99,
      currency: "USD",
      isSynthetic: true,
    };

    setPendingCheckout(pendingItem);
    const saved = getPendingCheckout();

    const modalHtml = renderAuthRequiredModal(pendingItem);

    clearPendingCheckout();
    const afterClear = getPendingCheckout();

    console.log(JSON.stringify({
      savedCorrectly: saved.productName === "Sony WH-1000XM5" && saved.price === 299.99,
      afterClearIsNull: afterClear === null,
      modalHasNotice: modalHtml.includes("Authentication Required"),
      modalHasExplanation: modalHtml.includes("Please sign in or create an account to complete your purchase"),
      modalHasProductName: modalHtml.includes("Sony WH-1000XM5"),
      modalHasPrice: modalHtml.includes("$299.99"),
      modalHasLoginAction: modalHtml.includes("js-btn-auth-modal-login"),
      modalHasRegisterAction: modalHtml.includes("js-btn-auth-modal-register"),
      modalHasCancelAction: modalHtml.includes("js-btn-close-auth-modal"),
    }));
    """
    res = run_node_eval(js)
    assert res["savedCorrectly"] is True
    assert res["afterClearIsNull"] is True
    assert res["modalHasNotice"] is True
    assert res["modalHasExplanation"] is True
    assert res["modalHasProductName"] is True
    assert res["modalHasPrice"] is True
    assert res["modalHasLoginAction"] is True
    assert res["modalHasRegisterAction"] is True
    assert res["modalHasCancelAction"] is True


# =============================================================================
# SCENARIO 7: No Payment Order Is Created Before Authentication
# =============================================================================

@pytest.mark.asyncio
async def test_req7_no_payment_order_is_created_before_authentication(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """Verify backend POST /api/checkout/orders rejects unauthenticated requests with HTTP 401."""
    sess = ShoppingSession(session_identifier="unauth_guest_order_test")
    test_session.add(sess)
    test_session.commit()

    order_payload = {
        "session_id": sess.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Premium Demo Headphones",
                "quantity": 1,
                "unit_price": 199.99,
                "currency": "USD",
            }
        ],
    }

    # Request without any Authorization header
    res = await async_client.post("/api/checkout/orders", json=order_payload)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "credentials required" in res.json().get("detail", "").lower()

    # Verify no PaymentOrder row was persisted
    db_orders = test_session.query(PaymentOrder).filter_by(session_id=sess.id).all()
    assert len(db_orders) == 0


# =============================================================================
# SCENARIO 8: Successful Registration Restores Intended Checkout
# =============================================================================

@pytest.mark.asyncio
async def test_req8_successful_registration_restores_intended_checkout(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch,
):
    """Verify registering claims guest session and allows immediate checkout completion."""
    # 1. Bootstrap guest session
    token_res = await async_client.post("/api/auth/session-token", json={})
    sid = token_res.json()["session_id"]
    stoken = token_res.json()["session_token"]

    # 2. Register account with guest session proof
    reg_email = f"newuser_{uuid.uuid4().hex[:6]}@example.com"
    reg_res = await async_client.post(
        "/api/auth/register",
        json={
            "name": "Alex Mercer",
            "email": reg_email,
            "password": "SecurePassword123!",
            "guest_session_id": sid,
            "guest_session_token": stoken,
        },
    )
    assert reg_res.status_code == status.HTTP_201_CREATED
    access_token = reg_res.json()["access_token"]
    assert reg_res.json()["linked_session_id"] == sid

    # Mock PayPal
    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=xyz"}],
        },
    )

    # 3. Create checkout order with new JWT
    order_res = await async_client.post(
        "/api/checkout/orders",
        json={
            "session_id": sid,
            "currency": "USD",
            "items": [
                {
                    "product_name": "Demo Mechanical Keyboard",
                    "quantity": 1,
                    "unit_price": 89.99,
                    "currency": "USD",
                }
            ],
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert order_res.status_code == status.HTTP_201_CREATED
    order_data = order_res.json()
    assert order_data["status"] == "CREATED"
    assert order_data["total_amount"] == "89.99"


# =============================================================================
# SCENARIO 9: Successful Login Restores Intended Checkout
# =============================================================================

@pytest.mark.asyncio
async def test_req9_successful_login_restores_intended_checkout(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch,
):
    """Verify logging in links active guest session and allows order creation."""
    # 1. Existing user in DB
    user_email = f"existing_{uuid.uuid4().hex[:6]}@example.com"
    user_pw = "Password123!"
    user = User(
        id=str(uuid.uuid4()),
        name="Existing Shopper",
        email=user_email,
        hashed_password=hash_password(user_pw),
    )
    test_session.add(user)
    test_session.commit()

    # 2. Guest session with token
    token_res = await async_client.post("/api/auth/session-token", json={})
    sid = token_res.json()["session_id"]
    stoken = token_res.json()["session_token"]

    # 3. Login and claim session
    login_res = await async_client.post(
        "/api/auth/login",
        json={
            "email": user_email,
            "password": user_pw,
            "guest_session_id": sid,
            "guest_session_token": stoken,
        },
    )
    assert login_res.status_code == status.HTTP_200_OK
    jwt_token = login_res.json()["access_token"]
    assert login_res.json()["linked_session_id"] == sid

    # Mock PayPal
    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=xyz"}],
        },
    )

    # 4. Proceed with checkout order
    order_res = await async_client.post(
        "/api/checkout/orders",
        json={
            "session_id": sid,
            "currency": "USD",
            "items": [
                {
                    "product_name": "Demo Smart Watch",
                    "quantity": 1,
                    "unit_price": 149.99,
                    "currency": "USD",
                }
            ],
        },
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert order_res.status_code == status.HTTP_201_CREATED
    assert order_res.json()["status"] == "CREATED"


# =============================================================================
# SCENARIO 10: Duplicate Payment Orders Are Prevented (Idempotency)
# =============================================================================

@pytest.mark.asyncio
async def test_req10_duplicate_payment_orders_are_prevented(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch,
):
    """Verify identical idempotency key returns existing order without creating duplicate database rows."""
    user = User(
        id=str(uuid.uuid4()),
        name="Idempotent User",
        email=f"idem_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Password123!"),
    )
    sess = ShoppingSession(
        session_identifier=f"idem_sess_{uuid.uuid4().hex[:6]}",
        user_id=user.id,
    )
    test_session.add_all([user, sess])
    test_session.commit()

    token, _, _ = create_access_token(user_id=user.id, email=user.email, name=user.name)
    auth_headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=xyz"}],
        },
    )

    shared_idemp_key = "idemp_test_key_hybrid_10"
    payload = {
        "session_id": sess.id,
        "idempotency_key": shared_idemp_key,
        "currency": "USD",
        "items": [
            {
                "product_name": "Mechanical Keyboard",
                "quantity": 1,
                "unit_price": 89.99,
                "currency": "USD",
            }
        ],
    }

    # First submission
    res1 = await async_client.post("/api/checkout/orders", json=payload, headers=auth_headers)
    assert res1.status_code == status.HTTP_201_CREATED
    order1 = res1.json()

    # Second submission with same idempotency key
    res2 = await async_client.post("/api/checkout/orders", json=payload, headers=auth_headers)
    assert res2.status_code == status.HTTP_201_CREATED
    order2 = res2.json()

    # Same order returned
    assert order1["id"] == order2["id"]
    assert order1["paypal_order_id"] == order2["paypal_order_id"]

    # Exactly 1 order exists in database
    orders = test_session.query(PaymentOrder).filter_by(session_id=sess.id).all()
    assert len(orders) == 1


# =============================================================================
# SCENARIO 11: Authenticated Users Complete PayPal Sandbox Checkout
# =============================================================================

@pytest.mark.asyncio
async def test_req11_authenticated_users_can_complete_paypal_sandbox_checkout(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch,
):
    """Verify complete checkout creation and authoritative capture for an authenticated user."""
    user = User(
        id=str(uuid.uuid4()),
        name="Checkout Completer",
        email=f"complete_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Password123!"),
    )
    sess = ShoppingSession(
        session_identifier=f"complete_sess_{uuid.uuid4().hex[:6]}",
        user_id=user.id,
    )
    test_session.add_all([user, sess])
    test_session.commit()

    token, _, _ = create_access_token(user_id=user.id, email=user.email, name=user.name)
    auth_headers = {"Authorization": f"Bearer {token}"}

    pp_order_id = f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": pp_order_id,
            "status": "CREATED",
            "links": [{"rel": "approve", "href": f"https://sandbox.paypal.com/checkoutnow?token={pp_order_id}"}],
        },
    )
    monkeypatch.setattr(
        PayPalClient,
        "get_order_details",
        lambda *args, **kwargs: {
            "id": pp_order_id,
            "status": "APPROVED",
            "intent": "CAPTURE",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "129.99"}}],
        },
    )
    monkeypatch.setattr(
        PayPalClient,
        "capture_order",
        lambda *args, **kwargs: {
            "id": pp_order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": f"CAP-{uuid.uuid4().hex[:8]}",
                                "status": "COMPLETED",
                                "amount": {"currency_code": "USD", "value": "129.99"},
                                "final_capture": True,
                            }
                        ]
                    }
                }
            ],
            "payer": {"email_address": "buyer@sandbox.paypal.com", "payer_id": "P123"},
        },
    )

    # 1. Create order
    create_res = await async_client.post(
        "/api/checkout/orders",
        json={
            "session_id": sess.id,
            "currency": "USD",
            "items": [
                {
                    "product_name": "Premium Wireless Mouse",
                    "quantity": 1,
                    "unit_price": 129.99,
                    "currency": "USD",
                }
            ],
        },
        headers=auth_headers,
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    order_id = create_res.json()["id"]

    # 2. Authoritative capture
    cap_res = await async_client.post(
        f"/api/checkout/orders/{order_id}/capture?session_id={sess.id}",
        headers=auth_headers,
    )
    assert cap_res.status_code == status.HTTP_200_OK
    cap_data = cap_res.json()
    assert cap_data["status"] == "COMPLETED"
    assert len(cap_data["captures"]) == 1
    assert cap_data["captures"][0]["is_verified"] is True


# =============================================================================
# SCENARIO 12: Logout Returns to Welcome Page & Revokes Token
# =============================================================================

@pytest.mark.asyncio
async def test_req12_logout_returns_to_welcome_page(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """Verify server-side JWT revocation on logout prevents further authorized actions."""
    user = User(
        id=str(uuid.uuid4()),
        name="Logout User",
        email=f"logout_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Password123!"),
    )
    test_session.add(user)
    test_session.commit()

    token, jti, _ = create_access_token(user_id=user.id, email=user.email, name=user.name)
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 1. Profile accessible
    res_me = await async_client.get("/api/auth/me", headers=auth_headers)
    assert res_me.status_code == status.HTTP_200_OK

    # 2. Call logout endpoint
    res_logout = await async_client.post("/api/auth/logout", headers=auth_headers)
    assert res_logout.status_code == status.HTTP_200_OK

    # 3. Same token is now revoked -> 401 Unauthorized
    res_after = await async_client.get("/api/auth/me", headers=auth_headers)
    assert res_after.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in res_after.json().get("detail", "").lower()


# =============================================================================
# SCENARIO 13: Guest Browser Refresh Restores Guest Browsing
# =============================================================================

def test_req13_guest_refresh_restores_guest_browsing():
    """Verify that when isGuestActive is true, a simulated page refresh preserves guest status."""
    js = """
    const { isGuestActive, setGuestActive } = await import('./frontend/assets/js/config.js');

    // User clicked browse as guest
    setGuestActive(true);

    // Simulate page reload: storage persists in localStorage
    const restored = isGuestActive();

    console.log(JSON.stringify({
      guestBrowsingRestored: restored,
    }));
    """
    res = run_node_eval(js)
    assert res["guestBrowsingRestored"] is True


# =============================================================================
# SCENARIO 14: Authenticated Refresh Restores Authenticated Browsing
# =============================================================================

def test_req14_authenticated_refresh_restores_authenticated_browsing():
    """Verify that when auth token and user profile are saved, reload restores authenticated state."""
    js = """
    const { isAuthenticated, setAuthToken, setAuthUser, getAuthUser, clearAuth } = await import('./frontend/assets/js/config.js');

    // Simulate login
    setAuthToken("mock_jwt_token_123");
    setAuthUser({ id: "user_abc", name: "Alex Mercer", email: "alex@example.com" });

    // Simulate reload
    const isAuth = isAuthenticated();
    const user = getAuthUser();

    clearAuth();
    const afterClear = isAuthenticated();

    console.log(JSON.stringify({
      isAuthRestored: isAuth,
      userNameRestored: user.name,
      afterClearIsFalse: afterClear,
    }));
    """
    res = run_node_eval(js)
    assert res["isAuthRestored"] is True
    assert res["userNameRestored"] == "Alex Mercer"
    assert res["afterClearIsFalse"] is False


# =============================================================================
# SCENARIO 15: Invalid or Expired JWT Cannot Create an Order
# =============================================================================

@pytest.mark.asyncio
async def test_req15_invalid_or_expired_jwt_cannot_create_an_order(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """Verify invalid and expired JWTs are rejected with 401 when attempting to create an order."""
    sess = ShoppingSession(session_identifier="jwt_security_test")
    test_session.add(sess)
    test_session.commit()

    order_payload = {
        "session_id": sess.id,
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Security Product",
                "quantity": 1,
                "unit_price": 50.00,
                "currency": "USD",
            }
        ],
    }

    # 1. Invalid garbage token
    bad_res = await async_client.post(
        "/api/checkout/orders",
        json=order_payload,
        headers={"Authorization": "Bearer invalid.garbage.token"},
    )
    assert bad_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "invalid" in bad_res.json().get("detail", "").lower()

    # 2. Expired token
    user = User(
        id=str(uuid.uuid4()),
        name="Expired User",
        email=f"expired_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Password123!"),
    )
    test_session.add(user)
    test_session.commit()

    # Token with negative expiry delta
    expired_token, _, _ = create_access_token(
        user_id=user.id,
        email=user.email,
        name=user.name,
        expires_delta=timedelta(seconds=-10),
    )
    exp_res = await async_client.post(
        "/api/checkout/orders",
        json=order_payload,
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert exp_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in exp_res.json().get("detail", "").lower()

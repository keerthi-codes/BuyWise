"""
BUYWISE AI — Comprehensive Authentication & Session Security Tests.
Verifies native bcrypt hashing (72-byte safe limit), JWT generation, token expiration/revocation,
get_current_user / get_current_user_optional dependency behavior, proof-of-possession guest migration,
and strict cross-user data isolation.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import httpx
import pytest
from fastapi import status
from sqlalchemy.orm import Session

from backend.app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    validate_password_length,
)
from backend.app.models.user import User, RevokedToken
from backend.app.models.shopping import ShoppingSession, SpendingMandate, Product, RetailerOffer
from backend.app.models.payment import PaymentOrder, PaymentOrderStatus, OrderItem
from backend.app.services.paypal_client import PayPalClient


# ==============================================================================
# UNIT TESTS: Password Security & Cryptography
# ==============================================================================

def test_password_hashing_and_verification():
    """Verify native bcrypt password hashing and verification."""
    password = "CorrectHorseBattery99!"
    hashed = hash_password(password)

    # Never stores plaintext
    assert hashed != password
    assert hashed.startswith("$2b$")

    # Correct password succeeds
    assert verify_password(password, hashed) is True

    # Incorrect password fails
    assert verify_password("WrongPassword123!", hashed) is False
    assert verify_password("", hashed) is False


def test_password_72_byte_limit_enforced():
    """Verify strict 72-byte limit enforcement for bcrypt safety."""
    # Valid 72-byte password
    exact_72_bytes = "a" * 72
    assert len(exact_72_bytes.encode("utf-8")) == 72
    hashed = hash_password(exact_72_bytes)
    assert verify_password(exact_72_bytes, hashed) is True

    # 73-byte password must be rejected
    exceeded_73_bytes = "a" * 73
    with pytest.raises(ValueError, match="cannot exceed 72 bytes"):
        validate_password_length(exceeded_73_bytes)

    with pytest.raises(ValueError, match="cannot exceed 72 bytes"):
        hash_password(exceeded_73_bytes)

    assert verify_password(exceeded_73_bytes, hashed) is False

    # Under 8 characters must be rejected
    short_pw = "short7"
    with pytest.raises(ValueError, match="at least 8 characters"):
        validate_password_length(short_pw)


def test_jwt_token_creation_and_decoding():
    """Verify JWT access token issuance and signature verification."""
    user_id = str(uuid.uuid4())
    email = "shopper@buywise.ai"
    name = "Smart Shopper"

    token, jti, expires_in = create_access_token(user_id=user_id, email=email, name=name)
    assert isinstance(token, str) and len(token) > 20
    assert isinstance(jti, str) and len(jti) > 10
    assert expires_in == 86400  # 24 hours

    payload = decode_access_token(token)
    assert payload["sub"] == user_id
    assert payload["email"] == email
    assert payload["name"] == name
    assert payload["jti"] == jti
    assert payload["exp"] > payload["iat"]


# ==============================================================================
# INTEGRATION TESTS: Registration, Login, and Revocation
# ==============================================================================

@pytest.mark.asyncio
async def test_user_registration_success(async_client: httpx.AsyncClient):
    """Verify new user registration issues valid JWT and user profile."""
    unique_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "name": "Alex Mercer",
        "email": unique_email,
        "password": "SecurePassword123!",
    }

    res = await async_client.post("/api/auth/register", json=payload)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 86400
    assert data["user"]["email"] == unique_email.lower()
    assert data["user"]["name"] == "Alex Mercer"
    assert "id" in data["user"]


@pytest.mark.asyncio
async def test_user_registration_duplicate_email_rejected(async_client: httpx.AsyncClient):
    """Verify registering duplicate email (case-insensitive) returns 409 Conflict."""
    unique_email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "name": "First User",
        "email": unique_email,
        "password": "Password123!",
    }

    res1 = await async_client.post("/api/auth/register", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED

    # Attempt registration with uppercase variant of same email
    dup_payload = {
        "name": "Second User",
        "email": unique_email.upper(),
        "password": "AnotherPassword456!",
    }
    res2 = await async_client.post("/api/auth/register", json=dup_payload)
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res2.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_user_login_success_and_failure(async_client: httpx.AsyncClient):
    """Verify login authentication with correct and incorrect credentials."""
    email = f"login_{uuid.uuid4().hex[:8]}@example.com"
    password = "CorrectPassword123!"

    # Register user
    await async_client.post(
        "/api/auth/register",
        json={"name": "Login Tester", "email": email, "password": password},
    )

    # 1. Success login
    login_res = await async_client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == status.HTTP_200_OK
    assert "access_token" in login_res.json()

    # 2. Wrong password
    bad_pw_res = await async_client.post(
        "/api/auth/login",
        json={"email": email, "password": "WrongPassword999!"},
    )
    assert bad_pw_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "invalid email or password" in bad_pw_res.json().get("detail", "").lower()

    # 3. Unknown email
    unknown_res = await async_client.post(
        "/api/auth/login",
        json={"email": "non_existent_user@example.com", "password": password},
    )
    assert unknown_res.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_protected_me_endpoint_and_revocation(async_client: httpx.AsyncClient):
    """Verify /api/auth/me requires valid token and server-side logout revokes the token."""
    email = f"rev_{uuid.uuid4().hex[:8]}@example.com"
    password = "Password123!"

    reg = await async_client.post(
        "/api/auth/register",
        json={"name": "Revocation User", "email": email, "password": password},
    )
    token = reg.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # 1. Access /api/auth/me with valid token
    me_res = await async_client.get("/api/auth/me", headers=auth_header)
    assert me_res.status_code == status.HTTP_200_OK
    assert me_res.json()["email"] == email.lower()

    # 2. Access /api/auth/me without token -> 401
    unauth_res = await async_client.get("/api/auth/me")
    assert unauth_res.status_code == status.HTTP_401_UNAUTHORIZED

    # 3. Server-side logout
    logout_res = await async_client.post("/api/auth/logout", headers=auth_header)
    assert logout_res.status_code == status.HTTP_200_OK

    # 4. Immediate reuse of revoked token must return 401 Unauthorized
    revoked_res = await async_client.get("/api/auth/me", headers=auth_header)
    assert revoked_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in revoked_res.json().get("detail", "").lower()


# ==============================================================================
# INTEGRATION TESTS: Optional Auth & Invalid Token Strictness
# ==============================================================================

@pytest.mark.asyncio
async def test_invalid_or_expired_jwt_returns_401_even_on_optional_routes(async_client: httpx.AsyncClient):
    """
    Mandatory Correction 1:
    Supplied invalid or expired tokens MUST return 401 Unauthorized.
    Only requests with NO token may use guest access.
    """
    # 1. Invalid signature token supplied on optional route
    bogus_headers = {"Authorization": "Bearer not_a_valid_jwt_token"}
    res = await async_client.get("/api/checkout/orders/non_existent_id?session_id=sess_123", headers=bogus_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Expired token supplied on optional route
    expired_token, _, _ = create_access_token(
        user_id="user_123",
        email="test@example.com",
        name="Tester",
        expires_delta=timedelta(seconds=-10),  # expired 10 seconds ago
    )
    expired_headers = {"Authorization": f"Bearer {expired_token}"}
    res_exp = await async_client.get("/api/checkout/orders/non_existent_id?session_id=sess_123", headers=expired_headers)
    assert res_exp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in res_exp.json().get("detail", "").lower()


# ==============================================================================
# INTEGRATION TESTS: Proof-of-Possession Guest Migration
# ==============================================================================

@pytest.mark.asyncio
async def test_guest_session_proof_of_possession_and_migration(async_client: httpx.AsyncClient):
    """
    Mandatory Correction 2:
    Guest-session migration requires proof of possession.
    Cannot claim another guest's session without matching session_token.
    """
    # 1. Obtain session token for a fresh guest session
    token_res = await async_client.post("/api/auth/session-token", json={})
    assert token_res.status_code == status.HTTP_200_OK
    session_cred = token_res.json()
    sid = session_cred["session_id"]
    valid_session_token = session_cred["session_token"]
    assert valid_session_token is not None and len(valid_session_token) > 10

    # 2. Register User A and claim the guest session with correct proof of possession
    user_a_email = f"usera_{uuid.uuid4().hex[:8]}@example.com"
    reg_a = await async_client.post(
        "/api/auth/register",
        json={
            "name": "User Alpha",
            "email": user_a_email,
            "password": "Password123!",
            "guest_session_id": sid,
            "guest_session_token": valid_session_token,
        },
    )
    assert reg_a.status_code == status.HTTP_201_CREATED
    assert reg_a.json()["linked_session_id"] == sid

    # 3. Register User B and attempt to hijack User A's claimed session -> 403 Forbidden
    user_b_email = f"userb_{uuid.uuid4().hex[:8]}@example.com"
    reg_b = await async_client.post(
        "/api/auth/register",
        json={
            "name": "User Beta",
            "email": user_b_email,
            "password": "Password123!",
            "guest_session_id": sid,
            "guest_session_token": valid_session_token,
        },
    )
    assert reg_b.status_code == status.HTTP_403_FORBIDDEN
    assert "another account" in reg_b.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_claim_session_with_invalid_proof_rejected(async_client: httpx.AsyncClient):
    """Verify attempting to link a guest session with incorrect session_token is rejected with 403."""
    token_res = await async_client.post("/api/auth/session-token", json={})
    assert token_res.status_code == status.HTTP_200_OK
    sid = token_res.json()["session_id"]

    user_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await async_client.post(
        "/api/auth/register",
        json={
            "name": "Test User",
            "email": user_email,
            "password": "Password123!",
            "guest_session_id": sid,
            "guest_session_token": "fraudulent_token_value",
        },
    )
    assert reg.status_code == status.HTTP_403_FORBIDDEN
    assert "possession" in reg.json().get("detail", "").lower() or "credentials" in reg.json().get("detail", "").lower()


# ==============================================================================
# INTEGRATION TESTS: Cross-User Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_cross_user_order_and_mandate_isolation(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Mandatory Correction 4 & 7:
    User A's orders, mandates, and analytics cannot be accessed by User B.
    """
    # 1. Create User A and User B
    u_a = User(
        id=str(uuid.uuid4()),
        email=f"alpha_{uuid.uuid4().hex[:6]}@test.com",
        name="Alpha",
        hashed_password=hash_password("Password123!"),
    )
    u_b = User(
        id=str(uuid.uuid4()),
        email=f"beta_{uuid.uuid4().hex[:6]}@test.com",
        name="Beta",
        hashed_password=hash_password("Password123!"),
    )
    test_session.add_all([u_a, u_b])
    test_session.commit()

    token_a, _, _ = create_access_token(u_a.id, u_a.email, u_a.name)
    token_b, _, _ = create_access_token(u_b.id, u_b.email, u_b.name)
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 2. Create session and order belonging to User A
    sess_a = ShoppingSession(
        id=f"sess_{uuid.uuid4().hex[:12]}",
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        user_id=u_a.id,
    )
    mandate_a = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess_a.id,
        total_budget=500.0,
        maximum_item_price=300.0,
        maximum_quantity=2,
        currency="USD",
    )
    order_a = PaymentOrder(
        id=str(uuid.uuid4()),
        user_id=u_a.id,
        session_id=sess_a.id,
        mandate_id=mandate_a.id,
        total_amount=Decimal("150.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add_all([sess_a, mandate_a, order_a])
    test_session.commit()

    # 3. User A can view their own order
    res_a = await async_client.get(
        f"/api/checkout/orders/{order_a.id}?session_id={sess_a.id}",
        headers=headers_a,
    )
    assert res_a.status_code == status.HTTP_200_OK

    # 4. User B cannot view User A's order -> 403 Forbidden
    res_b = await async_client.get(
        f"/api/checkout/orders/{order_a.id}?session_id={sess_a.id}",
        headers=headers_b,
    )
    assert res_b.status_code == status.HTTP_403_FORBIDDEN
    assert "another account" in res_b.json().get("detail", "").lower()

    # 5. User B cannot access User A's mandate -> 403 Forbidden
    mandate_res_b = await async_client.get(
        f"/api/mandates/{sess_a.id}",
        headers=headers_b,
    )
    assert mandate_res_b.status_code == status.HTTP_403_FORBIDDEN

    # 6. User B cannot access User A's personal analytics -> 403 Forbidden
    analytics_res_b = await async_client.get(
        f"/api/checkout/analytics/{sess_a.id}",
        headers=headers_b,
    )
    assert analytics_res_b.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# INTEGRATION TESTS: Guest Session Proof-of-Possession Remediation
# ==============================================================================

@pytest.mark.asyncio
async def test_cannot_retrieve_session_token_with_only_session_id_or_identifier(async_client: httpx.AsyncClient):
    """
    Remediation Requirement 1 & 2:
    - POST /api/auth/session-token must NOT issue a session token merely because someone supplies
      a session_id or session_identifier.
    - Missing credentials fail with 401; invalid credentials fail with 403.
    - Valid credentials allow retrieval/verification with 200.
    """
    # 1. Bootstrap a fresh session
    bootstrap_res = await async_client.post("/api/auth/session-token", json={})
    assert bootstrap_res.status_code == status.HTTP_200_OK
    data = bootstrap_res.json()
    sid = data["session_id"]
    ident = data["session_identifier"]
    token = data["session_token"]
    assert token and len(token) > 10

    # 2. Attempt to query token with only guest_session_id -> 401 Unauthorized
    res_sid_only = await async_client.post("/api/auth/session-token", json={"guest_session_id": sid})
    assert res_sid_only.status_code == status.HTTP_401_UNAUTHORIZED
    assert "credential required" in res_sid_only.json().get("detail", "").lower()

    # 3. Attempt to query token with only session_identifier -> 401 Unauthorized
    res_ident_only = await async_client.post("/api/auth/session-token", json={"session_identifier": ident})
    assert res_ident_only.status_code == status.HTTP_401_UNAUTHORIZED
    assert "credential required" in res_ident_only.json().get("detail", "").lower()

    # 4. Attempt to query token with invalid session_token -> 403 Forbidden
    res_bad_token = await async_client.post(
        "/api/auth/session-token",
        json={"guest_session_id": sid, "guest_session_token": "fraudulent_token_value"},
    )
    assert res_bad_token.status_code == status.HTTP_403_FORBIDDEN
    assert "possession could not be verified" in res_bad_token.json().get("detail", "").lower()

    # 5. Access with valid proof of possession in body -> 200 OK
    res_valid_body = await async_client.post(
        "/api/auth/session-token",
        json={"guest_session_id": sid, "guest_session_token": token},
    )
    assert res_valid_body.status_code == status.HTTP_200_OK
    assert res_valid_body.json()["session_token"] == token

    # 6. Access with valid proof of possession in X-Guest-Session-Token header -> 200 OK
    res_valid_header = await async_client.post(
        "/api/auth/session-token",
        json={"guest_session_id": sid},
        headers={"X-Guest-Session-Token": token},
    )
    assert res_valid_header.status_code == status.HTTP_200_OK
    assert res_valid_header.json()["session_token"] == token


@pytest.mark.asyncio
async def test_guest_checkout_orders_require_proof_of_possession(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch,
):
    """
    Remediation Requirement 3:
    An unauthenticated caller cannot create an order, capture an order, or retrieve order status
    for a guest session without providing the session's proof of possession.
    """
    # 1. Bootstrap a fresh session
    token_res = await async_client.post("/api/auth/session-token", json={})
    cred = token_res.json()
    sid = cred["session_id"]
    token = cred["session_token"]

    # Mock PayPal Client so order creation doesn't make external network calls
    from backend.app.services.paypal_client import PayPalClient
    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=xyz"}],
        },
    )

    order_payload = {
        "session_id": sid,
        "currency": "USD",
        "items": [
            {
                "product_name": "Demo Test Item",
                "retailer_name": "Demo Store",
                "quantity": 1,
                "unit_price": 50.0,
                "currency": "USD",
            }
        ],
    }

    # 2. Order creation without authentication -> 401 Unauthorized
    res_no_token = await async_client.post("/api/checkout/orders", json=order_payload)
    assert res_no_token.status_code == status.HTTP_401_UNAUTHORIZED

    # 3. Order creation with invalid token in header -> 401 Unauthorized
    res_bad_token = await async_client.post(
        "/api/checkout/orders",
        json=order_payload,
        headers={"X-Guest-Session-Token": "invalid_secret_token"},
    )
    assert res_bad_token.status_code == status.HTTP_401_UNAUTHORIZED

    # 4. Authenticate user and link guest session
    reg_res = await async_client.post(
        "/api/auth/register",
        json={
            "name": "Checkout Auth User",
            "email": f"auth_chk_{uuid.uuid4().hex[:6]}@example.com",
            "password": "ValidPassword123!",
            "guest_session_id": sid,
            "guest_session_token": token,
        },
    )
    assert reg_res.status_code == status.HTTP_201_CREATED
    auth_token = reg_res.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {auth_token}"}

    # 5. Order creation with authenticated user and linked session -> 201 Created
    res_ok = await async_client.post(
        "/api/checkout/orders",
        json=order_payload,
        headers=auth_headers,
    )
    assert res_ok.status_code == status.HTTP_201_CREATED
    order_data = res_ok.json()
    order_id = order_data["id"]
    paypal_id = order_data["paypal_order_id"]
    assert order_id is not None

    # 6. Unauthenticated caller attempts to view authenticated user's order -> 403 Forbidden
    res_st_no = await async_client.get(f"/api/checkout/orders/{order_id}?session_id={sid}")
    assert res_st_no.status_code == status.HTTP_403_FORBIDDEN

    # 7. Order status with invalid token -> 403 Forbidden
    res_st_bad = await async_client.get(
        f"/api/checkout/orders/{order_id}?session_id={sid}",
        headers={"X-Guest-Session-Token": "bad_token"},
    )
    assert res_st_bad.status_code == status.HTTP_403_FORBIDDEN

    # 8. Order status with authenticated user -> 200 OK
    res_st_ok = await async_client.get(
        f"/api/checkout/orders/{order_id}?session_id={sid}",
        headers=auth_headers,
    )
    assert res_st_ok.status_code == status.HTTP_200_OK
    assert res_st_ok.json()["id"] == order_id

    # 9. Lookup by PayPal ID unauthenticated -> 403 Forbidden
    res_pp_no = await async_client.get(f"/api/checkout/orders/by-paypal-id/{paypal_id}?session_id={sid}")
    assert res_pp_no.status_code == status.HTTP_403_FORBIDDEN

    # 10. Lookup by PayPal ID with authenticated user -> 200 OK
    res_pp_ok = await async_client.get(
        f"/api/checkout/orders/by-paypal-id/{paypal_id}?session_id={sid}",
        headers=auth_headers,
    )
    assert res_pp_ok.status_code == status.HTTP_200_OK

    # 11. Capture without authenticated user -> 403 Forbidden
    res_cap_no = await async_client.post(f"/api/checkout/orders/{order_id}/capture?session_id={sid}")
    assert res_cap_no.status_code == status.HTTP_403_FORBIDDEN

    # 12. Capture with invalid token -> 403 Forbidden
    res_cap_bad = await async_client.post(
        f"/api/checkout/orders/{order_id}/capture?session_id={sid}",
        headers={"X-Guest-Session-Token": "bad_token"},
    )
    assert res_cap_bad.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_guest_history_and_analytics_require_proof_of_possession(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Remediation Requirement 3 & 6:
    Historical orders and analytics cannot be queried by unauthorized guests without proof of possession.
    """
    token_res = await async_client.post("/api/auth/session-token", json={})
    cred = token_res.json()
    sid = cred["session_id"]
    token = cred["session_token"]

    # History: missing token -> 401
    hist_no = await async_client.get(f"/api/checkout/history/{sid}")
    assert hist_no.status_code == status.HTTP_401_UNAUTHORIZED

    # History: invalid token -> 403
    hist_bad = await async_client.get(
        f"/api/checkout/history/{sid}",
        headers={"X-Guest-Session-Token": "wrong_token"},
    )
    assert hist_bad.status_code == status.HTTP_403_FORBIDDEN

    # History: valid token -> 200
    hist_ok = await async_client.get(
        f"/api/checkout/history/{sid}",
        headers={"X-Guest-Session-Token": token},
    )
    assert hist_ok.status_code == status.HTTP_200_OK

    # Analytics: missing token -> 401
    ana_no = await async_client.get(f"/api/checkout/analytics/{sid}")
    assert ana_no.status_code == status.HTTP_401_UNAUTHORIZED

    # Analytics: invalid token -> 403
    ana_bad = await async_client.get(
        f"/api/checkout/analytics/{sid}",
        headers={"X-Guest-Session-Token": "wrong_token"},
    )
    assert ana_bad.status_code == status.HTTP_403_FORBIDDEN

    # Analytics: valid token -> 200
    ana_ok = await async_client.get(
        f"/api/checkout/analytics/{sid}",
        headers={"X-Guest-Session-Token": token},
    )
    assert ana_ok.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_guest_mandates_require_proof_of_possession(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Remediation Requirement 3:
    Guest spending mandate creation, retrieval, and updating require proof of possession.
    """
    token_res = await async_client.post("/api/auth/session-token", json={})
    cred = token_res.json()
    sid = cred["session_id"]
    token = cred["session_token"]

    mandate_payload = {
        "session_id": sid,
        "total_budget": 500.0,
        "maximum_item_price": 250.0,
        "maximum_quantity": 2,
        "currency": "USD",
    }

    # Mandate creation: missing token -> 401
    man_no = await async_client.post("/api/mandates", json=mandate_payload)
    assert man_no.status_code == status.HTTP_401_UNAUTHORIZED

    # Mandate creation: invalid token -> 403
    man_bad = await async_client.post(
        "/api/mandates",
        json=mandate_payload,
        headers={"X-Guest-Session-Token": "wrong_token"},
    )
    assert man_bad.status_code == status.HTTP_403_FORBIDDEN

    # Mandate creation: valid token -> 201
    man_ok = await async_client.post(
        "/api/mandates",
        json=mandate_payload,
        headers={"X-Guest-Session-Token": token},
    )
    assert man_ok.status_code == status.HTTP_201_CREATED

    # Mandate retrieval: missing token -> 401
    get_no = await async_client.get(f"/api/mandates/{sid}")
    assert get_no.status_code == status.HTTP_401_UNAUTHORIZED

    # Mandate retrieval: invalid token -> 403
    get_bad = await async_client.get(
        f"/api/mandates/{sid}",
        headers={"X-Guest-Session-Token": "wrong_token"},
    )
    assert get_bad.status_code == status.HTTP_403_FORBIDDEN

    # Mandate retrieval: valid token -> 200
    get_ok = await async_client.get(
        f"/api/mandates/{sid}",
        headers={"X-Guest-Session-Token": token},
    )
    assert get_ok.status_code == status.HTTP_200_OK

    # Mandate update: missing token -> 401
    update_payload = {"total_budget": 600.0}
    put_no = await async_client.put(f"/api/mandates/{sid}", json=update_payload)
    assert put_no.status_code == status.HTTP_401_UNAUTHORIZED

    # Mandate update: valid token -> 200
    put_ok = await async_client.put(
        f"/api/mandates/{sid}",
        json=update_payload,
        headers={"X-Guest-Session-Token": token},
    )
    assert put_ok.status_code == status.HTTP_200_OK
    assert put_ok.json()["total_budget"] == 600.0


@pytest.mark.asyncio
async def test_authenticated_user_records_remain_inaccessible_to_guests(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Remediation Requirement 4 & 6:
    Authenticated user records remain strictly inaccessible to unauthenticated guest callers.
    """
    user = User(
        id=str(uuid.uuid4()),
        email=f"owner_{uuid.uuid4().hex[:6]}@example.com",
        name="Owner User",
        hashed_password=hash_password("Password123!"),
    )
    sess = ShoppingSession(
        id=f"sess_{uuid.uuid4().hex[:12]}",
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
    )
    mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=sess.id,
        total_budget=800.0,
        maximum_item_price=400.0,
        maximum_quantity=3,
        currency="USD",
    )
    order = PaymentOrder(
        id=str(uuid.uuid4()),
        user_id=user.id,
        session_id=sess.id,
        mandate_id=mandate.id,
        total_amount=Decimal("120.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add_all([user, sess, mandate, order])
    test_session.commit()

    # Unauthenticated guest attempts to access user's order -> 403 Forbidden
    res_order = await async_client.get(f"/api/checkout/orders/{order.id}?session_id={sess.id}")
    assert res_order.status_code == status.HTTP_403_FORBIDDEN

    # Unauthenticated guest attempts to access user's mandate -> 403 Forbidden
    res_mandate = await async_client.get(f"/api/mandates/{sess.id}")
    assert res_mandate.status_code == status.HTTP_403_FORBIDDEN

    # Unauthenticated guest attempts to access user's history -> 403 Forbidden
    res_hist = await async_client.get(f"/api/checkout/history/{sess.id}")
    assert res_hist.status_code == status.HTTP_403_FORBIDDEN

    # Unauthenticated guest attempts to access user's analytics -> 403 Forbidden
    res_ana = await async_client.get(f"/api/checkout/analytics/{sess.id}")
    assert res_ana.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_backward_compatibility_legacy_sessions_without_tokens(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Remediation Requirement 5:
    Legacy sessions and orders created prior to Phase 2 (where session_token is None)
    remain accessible without crashing or requiring tokens.
    """
    legacy_sess = ShoppingSession(
        id=f"legacy_sess_{uuid.uuid4().hex[:10]}",
        session_identifier=f"legacy_ident_{uuid.uuid4().hex[:6]}",
        session_token=None,  # Legacy session lacking token
        user_id=None,
    )
    legacy_order = PaymentOrder(
        id=str(uuid.uuid4()),
        session_id=legacy_sess.id,
        user_id=None,
        total_amount=Decimal("45.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    legacy_mandate = SpendingMandate(
        id=str(uuid.uuid4()),
        session_id=legacy_sess.id,
        total_budget=300.0,
        maximum_item_price=100.0,
        maximum_quantity=1,
        currency="USD",
    )
    test_session.add_all([legacy_sess, legacy_order, legacy_mandate])
    test_session.commit()

    # Legacy order status can still be retrieved
    res_ord = await async_client.get(f"/api/checkout/orders/{legacy_order.id}?session_id={legacy_sess.id}")
    assert res_ord.status_code == status.HTTP_200_OK
    assert res_ord.json()["id"] == legacy_order.id

    # Legacy payment history can still be retrieved
    res_hist = await async_client.get(f"/api/checkout/history/{legacy_sess.id}")
    assert res_hist.status_code == status.HTTP_200_OK

    # Legacy analytics can still be retrieved
    res_ana = await async_client.get(f"/api/checkout/analytics/{legacy_sess.id}")
    assert res_ana.status_code == status.HTTP_200_OK

    # Legacy mandate can still be retrieved
    res_man = await async_client.get(f"/api/mandates/{legacy_sess.id}")
    assert res_man.status_code == status.HTTP_200_OK


# ==============================================================================
# INTEGRATION TESTS: Guest Registration with Joined Eager Loading Orders
# ==============================================================================

@pytest.mark.asyncio
async def test_guest_registration_with_existing_orders_and_items(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Verify that registering with a guest session containing historical orders
    and collection items succeeds without SQLAlchemy InvalidRequestError,
    uniquifies eager joined collections correctly, and updates order ownership.
    """
    sid = f"sess_multi_{uuid.uuid4().hex[:10]}"
    token = f"tok_{uuid.uuid4().hex[:16]}"
    guest_session = ShoppingSession(
        id=sid,
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        session_token=token,
        user_id=None,
    )
    test_session.add(guest_session)

    # Add two payment orders, each with multiple order items (joined eager load)
    order1 = PaymentOrder(
        id=f"order_{uuid.uuid4().hex[:10]}",
        session_id=sid,
        user_id=None,
        total_amount=Decimal("199.98"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    item1_a = OrderItem(
        id=f"item_{uuid.uuid4().hex[:10]}",
        payment_order_id=order1.id,
        product_name="Ninja Foodi 8-in-1",
        retailer_name="Best Buy",
        quantity=1,
        unit_price=Decimal("99.99"),
        subtotal=Decimal("99.99"),
        currency="USD",
    )
    item1_b = OrderItem(
        id=f"item_{uuid.uuid4().hex[:10]}",
        payment_order_id=order1.id,
        product_name="Ninja Foodi Accessory Pack",
        retailer_name="Best Buy",
        quantity=1,
        unit_price=Decimal("99.99"),
        subtotal=Decimal("99.99"),
        currency="USD",
    )

    order2 = PaymentOrder(
        id=f"order_{uuid.uuid4().hex[:10]}",
        session_id=sid,
        user_id=None,
        total_amount=Decimal("49.99"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    item2_a = OrderItem(
        id=f"item_{uuid.uuid4().hex[:10]}",
        payment_order_id=order2.id,
        product_name="Wireless Mouse",
        retailer_name="Target",
        quantity=1,
        unit_price=Decimal("49.99"),
        subtotal=Decimal("49.99"),
        currency="USD",
    )

    test_session.add_all([order1, item1_a, item1_b, order2, item2_a])
    test_session.commit()

    # Register user claiming the guest session
    email = f"multi_order_shopper_{uuid.uuid4().hex[:8]}@example.com"
    reg_payload = {
        "name": "Multi Order Shopper",
        "email": email,
        "password": "SecurePassword123!",
        "guest_session_id": sid,
        "guest_session_token": token,
    }
    res = await async_client.post("/api/auth/register", json=reg_payload)

    # Must succeed with 201 Created (no SQLAlchemy unique() or eager loading exception)
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["linked_session_id"] == sid
    assert data["linked_orders_count"] == 2  # Exactly 2 orders, not 3 from joined item rows
    new_user_id = data["user"]["id"]

    # Verify DB state
    test_session.expire_all()
    db_session = test_session.query(ShoppingSession).filter_by(id=sid).first()
    assert db_session.user_id == new_user_id

    db_order1 = test_session.query(PaymentOrder).filter_by(id=order1.id).first()
    assert db_order1.user_id == new_user_id

    db_order2 = test_session.query(PaymentOrder).filter_by(id=order2.id).first()
    assert db_order2.user_id == new_user_id


@pytest.mark.asyncio
async def test_failed_registration_rolls_back_partial_changes(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Verify that if session linking fails during registration (e.g. invalid proof),
    the user creation is rolled back and session/order remain unassigned.
    """
    sid = f"sess_rb_{uuid.uuid4().hex[:10]}"
    real_token = f"tok_{uuid.uuid4().hex[:16]}"
    guest_session = ShoppingSession(
        id=sid,
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        session_token=real_token,
        user_id=None,
    )
    order = PaymentOrder(
        id=f"order_rb_{uuid.uuid4().hex[:10]}",
        session_id=sid,
        user_id=None,
        total_amount=Decimal("120.00"),
        currency="USD",
        status=PaymentOrderStatus.CREATED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add_all([guest_session, order])
    test_session.commit()

    email = f"failing_user_{uuid.uuid4().hex[:8]}@example.com"
    reg_payload = {
        "name": "Failing User",
        "email": email,
        "password": "SecurePassword123!",
        "guest_session_id": sid,
        "guest_session_token": "wrong_invalid_token",
    }
    res = await async_client.post("/api/auth/register", json=reg_payload)
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Verify rollback: user must NOT exist
    test_session.expire_all()
    user_in_db = test_session.query(User).filter_by(email=email).first()
    assert user_in_db is None

    # Verify session and order remain unlinked
    db_session = test_session.query(ShoppingSession).filter_by(id=sid).first()
    assert db_session.user_id is None

    db_order = test_session.query(PaymentOrder).filter_by(id=order.id).first()
    assert db_order.user_id is None


@pytest.mark.asyncio
async def test_registration_and_checkout_resumption_flow(
    async_client: httpx.AsyncClient,
    test_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Verify complete flow:
    1. Guest browses and selects item (e.g. Ninja Foodi)
    2. Guest registers and claims guest session
    3. User uses JWT to place the checkout order successfully
    """
    monkeypatch.setattr(
        PayPalClient,
        "create_order",
        lambda *args, **kwargs: {
            "id": f"PAYPAL-ORDER-{uuid.uuid4().hex[:8]}",
            "status": "CREATED",
            "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/checkoutnow?token=xyz"}],
        },
    )

    sid = f"sess_resume_{uuid.uuid4().hex[:10]}"
    token = f"tok_{uuid.uuid4().hex[:16]}"
    guest_session = ShoppingSession(
        id=sid,
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        session_token=token,
        user_id=None,
    )
    mandate = SpendingMandate(
        id=f"man_{uuid.uuid4().hex[:10]}",
        session_id=sid,
        total_budget=5000.0,
        maximum_item_price=5000.0,
        maximum_quantity=5,
        currency="USD",
    )
    prod = test_session.query(Product).first()
    assert prod is not None
    offer = test_session.query(RetailerOffer).filter_by(product_id=prod.id).first()
    assert offer is not None

    test_session.add_all([guest_session, mandate])
    test_session.commit()

    email = f"resumer_{uuid.uuid4().hex[:8]}@example.com"
    reg_res = await async_client.post(
        "/api/auth/register",
        json={
            "name": "Checkout Resumer",
            "email": email,
            "password": "Password123!",
            "guest_session_id": sid,
            "guest_session_token": token,
        },
    )
    assert reg_res.status_code == status.HTTP_201_CREATED
    auth_data = reg_res.json()
    jwt_token = auth_data["access_token"]
    user_id = auth_data["user"]["id"]

    checkout_payload = {
        "session_id": sid,
        "mandate_id": mandate.id,
        "currency": "USD",
        "items": [
            {
                "product_id": prod.id,
                "retailer_offer_id": offer.id,
                "product_name": prod.name,
                "retailer_name": offer.retailer_name,
                "quantity": 1,
                "unit_price": float(offer.price),
                "currency": "USD",
            }
        ],
    }
    checkout_res = await async_client.post(
        "/api/checkout/orders",
        json=checkout_payload,
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert checkout_res.status_code == status.HTTP_201_CREATED
    order_data = checkout_res.json()
    assert order_data["id"] is not None
    assert order_data["session_id"] == sid

    test_session.expire_all()
    created_db_order = test_session.query(PaymentOrder).filter_by(id=order_data["id"]).first()
    assert created_db_order is not None
    assert created_db_order.user_id == user_id


# ==============================================================================
# INTEGRATION TESTS: Login After Logout & Stale Guest Credentials Non-Blocking
# ==============================================================================

@pytest.mark.asyncio
async def test_login_logout_login_repeated_cycles(async_client: httpx.AsyncClient):
    """
    Verify user can log out and log back in repeatedly using the same credentials.
    Each logout revokes the current token without breaking future logins.
    """
    email = f"cycle_{uuid.uuid4().hex[:8]}@example.com"
    password = "CyclePassword123!"

    reg = await async_client.post(
        "/api/auth/register",
        json={"name": "Cycle User", "email": email, "password": password},
    )
    assert reg.status_code == status.HTTP_201_CREATED

    for cycle in range(3):
        # 1. Login
        login_res = await async_client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
        assert login_res.status_code == status.HTTP_200_OK
        token = login_res.json()["access_token"]
        assert token is not None

        # 2. Access protected endpoint
        me_res = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_res.status_code == status.HTTP_200_OK
        assert me_res.json()["email"] == email

        # 3. Logout
        logout_res = await async_client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout_res.status_code == status.HTTP_200_OK

        # 4. Verify token revoked
        revoked_res = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert revoked_res.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_with_no_guest_credentials(async_client: httpx.AsyncClient):
    """
    Verify normal login without guest fields succeeds cleanly with 200 OK.
    """
    email = f"noguest_{uuid.uuid4().hex[:8]}@example.com"
    password = "NoGuestPassword123!"

    reg = await async_client.post(
        "/api/auth/register",
        json={"name": "No Guest User", "email": email, "password": password},
    )
    assert reg.status_code == status.HTTP_201_CREATED

    login_res = await async_client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == status.HTTP_200_OK
    data = login_res.json()
    assert "access_token" in data
    assert data["linked_session_id"] is None
    assert data["session_link_error"] is None


@pytest.mark.asyncio
async def test_login_with_stale_guest_credentials_does_not_block_authentication(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Verify that if a login request includes stale/invalid guest credentials,
    the account authentication STILL succeeds (200 OK), issues JWT, but skips
    claiming the invalid guest session without throwing a 403 Forbidden error.
    """
    # Create an active guest session in DB with a specific secret token
    sid = f"sess_stale_{uuid.uuid4().hex[:10]}"
    real_token = f"tok_real_{uuid.uuid4().hex[:16]}"
    guest_sess = ShoppingSession(
        id=sid,
        session_identifier=f"ident_{uuid.uuid4().hex[:8]}",
        session_token=real_token,
        user_id=None,
    )
    test_session.add(guest_sess)
    test_session.commit()

    email = f"stale_shopper_{uuid.uuid4().hex[:8]}@example.com"
    password = "StalePassword123!"
    reg = await async_client.post(
        "/api/auth/register",
        json={"name": "Stale Shopper", "email": email, "password": password},
    )
    assert reg.status_code == status.HTTP_201_CREATED

    # Login with wrong/stale guest session token
    login_res = await async_client.post(
        "/api/auth/login",
        json={
            "email": email,
            "password": password,
            "guest_session_id": sid,
            "guest_session_token": "stale_outdated_or_wrong_token",
        },
    )
    # Must NOT fail with 403 Forbidden!
    assert login_res.status_code == status.HTTP_200_OK
    data = login_res.json()
    assert "access_token" in data
    assert data["user"]["email"] == email
    # Session must NOT be linked
    assert data["linked_session_id"] is None
    assert data["session_link_error"] is not None
    assert "credentials" in data["session_link_error"].lower() or "possession" in data["session_link_error"].lower()

    # Session in DB remains unassigned
    test_session.expire_all()
    db_sess = test_session.query(ShoppingSession).filter_by(id=sid).first()
    assert db_sess.user_id is None


@pytest.mark.asyncio
async def test_login_cannot_hijack_another_users_session(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Verify that if User B supplies User A's session ID on login,
    User B still authenticates, but User A's session is NOT hijacked or transferred.
    """
    # User A with owned session
    user_a = User(
        id=str(uuid.uuid4()),
        email=f"victim_{uuid.uuid4().hex[:6]}@example.com",
        name="Victim User",
        hashed_password=hash_password("Pass12345!"),
    )
    sess_a = ShoppingSession(
        id=f"sess_victim_{uuid.uuid4().hex[:8]}",
        session_identifier=f"ident_victim_{uuid.uuid4().hex[:6]}",
        session_token="secret_token_a",
        user_id=user_a.id,
    )
    test_session.add_all([user_a, sess_a])
    test_session.commit()

    # User B registers
    email_b = f"user_b_{uuid.uuid4().hex[:6]}@example.com"
    pw_b = "PasswordB123!"
    reg_b = await async_client.post(
        "/api/auth/register",
        json={"name": "User B", "email": email_b, "password": pw_b},
    )
    assert reg_b.status_code == status.HTTP_201_CREATED

    # User B logs in trying to claim User A's session
    login_b = await async_client.post(
        "/api/auth/login",
        json={
            "email": email_b,
            "password": pw_b,
            "guest_session_id": sess_a.id,
            "guest_session_token": "secret_token_a",
        },
    )
    # User B logs in successfully
    assert login_b.status_code == status.HTTP_200_OK
    assert login_b.json()["linked_session_id"] is None
    assert "another account" in login_b.json()["session_link_error"].lower()

    # User A's session remains owned by User A
    test_session.expire_all()
    re_sess = test_session.query(ShoppingSession).filter_by(id=sess_a.id).first()
    assert re_sess.user_id == user_a.id


# ==============================================================================
# INTEGRATION TESTS: Guest Analytics Session Ownership & Post-Logout Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_post_logout_guest_analytics_isolation_and_relogin_restoration(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Regression Test for Guest Analytics Session Ownership Conflict:
    1. Account registers and claims a guest session (sess_a).
    2. Account has a verified completed order in sess_a.
    3. User 1 accesses analytics -> sees 1 order.
    4. User 1 logs out.
    5. Unauthenticated guest attempting to access sess_a is rejected with 403.
    6. Guest browses as new guest -> fresh independent session sess_b is created.
    7. Guest analytics for sess_b loads cleanly with 0 completed orders (empty state).
    8. User 1 logs back in -> sess_a is restored as linked_session_id.
    9. User 1 analytics for sess_a shows full historical data.
    """
    # 1. Bootstrap guest session A
    boot_res = await async_client.post("/api/auth/session-token", json={})
    assert boot_res.status_code == status.HTTP_200_OK
    data_a = boot_res.json()
    sid_a = data_a["session_id"]
    token_a = data_a["session_token"]

    # 2. Register User 1 claiming sess_a
    email_1 = f"analytics_owner_{uuid.uuid4().hex[:8]}@example.com"
    pw_1 = "SecurePassword123!"
    reg_res = await async_client.post(
        "/api/auth/register",
        json={
            "name": "Analytics Owner",
            "email": email_1,
            "password": pw_1,
            "guest_session_id": sid_a,
            "guest_session_token": token_a,
        },
    )
    assert reg_res.status_code == status.HTTP_201_CREATED
    user_1_id = reg_res.json()["user"]["id"]
    jwt_1 = reg_res.json()["access_token"]
    assert reg_res.json()["linked_session_id"] == sid_a

    # Add a completed order with verified capture in DB for sess_a
    pp_order_id = f"PP-ORD-{uuid.uuid4().hex[:10].upper()}"
    order_a = PaymentOrder(
        id=str(uuid.uuid4()),
        user_id=user_1_id,
        session_id=sid_a,
        paypal_order_id=pp_order_id,
        total_amount=Decimal("199.99"),
        currency="USD",
        status=PaymentOrderStatus.COMPLETED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    from backend.app.models.payment import PaymentCapture
    cap_a = PaymentCapture(
        id=str(uuid.uuid4()),
        payment_order_id=order_a.id,
        paypal_order_id=pp_order_id,
        paypal_capture_id=f"CAP-{uuid.uuid4().hex[:12].upper()}",
        captured_amount=Decimal("199.99"),
        currency="USD",
        capture_status="COMPLETED",
        is_verified=True,
        captured_at=datetime.now(timezone.utc),
    )
    test_session.add_all([order_a, cap_a])
    test_session.commit()

    # 3. User 1 accesses analytics for sess_a -> sees their completed order
    ana_1 = await async_client.get(
        f"/api/checkout/analytics/{sid_a}",
        headers={"Authorization": f"Bearer {jwt_1}"},
    )
    assert ana_1.status_code == status.HTTP_200_OK
    assert ana_1.json()["completed_order_count"] == 1

    # 4. User 1 logs out
    logout_res = await async_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {jwt_1}"},
    )
    assert logout_res.status_code == status.HTTP_200_OK

    # 5. Unauthenticated guest attempts to access sess_a -> 403 Forbidden (belongs to account)
    guest_leak_res = await async_client.get(f"/api/checkout/analytics/{sid_a}")
    assert guest_leak_res.status_code == status.HTTP_403_FORBIDDEN
    assert "another account" in guest_leak_res.json().get("detail", "").lower()

    # 6. Guest browses as guest again: bootstrap a fresh independent session B
    fresh_boot = await async_client.post("/api/auth/session-token", json={})
    assert fresh_boot.status_code == status.HTTP_200_OK
    data_b = fresh_boot.json()
    sid_b = data_b["session_id"]
    token_b = data_b["session_token"]
    assert sid_b != sid_a

    # 7. Guest analytics for sess_b loads cleanly with empty state (0 orders)
    ana_guest = await async_client.get(
        f"/api/checkout/analytics/{sid_b}",
        headers={"X-Guest-Session-Token": token_b},
    )
    assert ana_guest.status_code == status.HTTP_200_OK
    assert ana_guest.json()["completed_order_count"] == 0
    assert len(ana_guest.json()["total_spending_by_currency"]) == 0

    # 8. User 1 logs back in without guest credentials
    login_res = await async_client.post(
        "/api/auth/login",
        json={"email": email_1, "password": pw_1},
    )
    assert login_res.status_code == status.HTTP_200_OK
    new_jwt = login_res.json()["access_token"]
    # User 1's primary linked session is restored
    assert login_res.json()["linked_session_id"] == sid_a

    # 9. User 1 can view their analytics again with restored orders
    ana_restored = await async_client.get(
        f"/api/checkout/analytics/{sid_a}",
        headers={"Authorization": f"Bearer {new_jwt}"},
    )
    assert ana_restored.status_code == status.HTTP_200_OK
    assert ana_restored.json()["completed_order_count"] == 1


@pytest.mark.asyncio
async def test_refreshing_guest_browsing_preserves_session_without_duplicates(
    async_client: httpx.AsyncClient,
    test_session: Session,
):
    """
    Verify that existing guest sessions verified with valid tokens
    do not spawn duplicate database records on subsequent requests.
    """
    # 1. Bootstrap
    res1 = await async_client.post("/api/auth/session-token", json={})
    assert res1.status_code == status.HTTP_200_OK
    sid = res1.json()["session_id"]
    token = res1.json()["session_token"]

    # 2. Re-verify with credentials (simulating page reload)
    res2 = await async_client.post(
        "/api/auth/session-token",
        json={"guest_session_id": sid, "guest_session_token": token},
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["session_id"] == sid
    assert res2.json()["session_token"] == token

    # 3. Check DB: exactly 1 session exists
    count = test_session.query(ShoppingSession).filter_by(id=sid).count()
    assert count == 1

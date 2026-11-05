"""
BUYWISE AI — Frontend Static Integration & Asset Serving Tests.
Verifies FastAPI serving of index.html, static assets (CSS, JS), route preservation, and error handling.
"""

import httpx
import pytest
from fastapi import status


@pytest.mark.asyncio
async def test_frontend_index_served_for_browser(async_client: httpx.AsyncClient):
    """Verify GET / with browser Accept header returns HTML application shell."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }
    response = await async_client.get("/", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in response.text
    assert "BUYWISE AI" in response.text
    assert "Discover smarter." in response.text
    assert "Compare deeper." in response.text
    assert "Spend responsibly." in response.text


@pytest.mark.asyncio
async def test_frontend_direct_index_html(async_client: httpx.AsyncClient):
    """Verify GET /index.html returns HTML application shell."""
    response = await async_client.get("/index.html")
    assert response.status_code == status.HTTP_200_OK
    assert "text/html" in response.headers.get("content-type", "")
    assert "BUYWISE AI" in response.text


@pytest.mark.asyncio
async def test_frontend_css_asset_served(async_client: httpx.AsyncClient):
    """Verify /assets/css/styles.css is served with correct mime type."""
    response = await async_client.get("/assets/css/styles.css")
    assert response.status_code == status.HTTP_200_OK
    assert "text/css" in response.headers.get("content-type", "")
    assert "--color-burnt-orange" in response.text


@pytest.mark.asyncio
async def test_frontend_js_modules_served(async_client: httpx.AsyncClient):
    """Verify all JavaScript ES modules are properly served."""
    modules = ["app.js", "api.js", "components.js", "config.js"]
    for mod in modules:
        response = await async_client.get(f"/assets/js/{mod}")
        assert response.status_code == status.HTTP_200_OK
        ctype = response.headers.get("content-type", "")
        assert "javascript" in ctype or "text/plain" in ctype
        assert len(response.text) > 50


@pytest.mark.asyncio
async def test_missing_static_asset_returns_404(async_client: httpx.AsyncClient):
    """Verify missing static file under /assets returns 404 Not Found."""
    response = await async_client.get("/assets/js/non_existent_file.js")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_docs_and_openapi_preserved(async_client: httpx.AsyncClient):
    """Verify OpenAPI documentation routes are not shadowed by frontend mounts."""
    docs_res = await async_client.get("/docs")
    assert docs_res.status_code == status.HTTP_200_OK
    assert "text/html" in docs_res.headers.get("content-type", "")

    openapi_res = await async_client.get("/openapi.json")
    assert openapi_res.status_code == status.HTTP_200_OK
    assert "application/json" in openapi_res.headers.get("content-type", "")
    data = openapi_res.json()
    assert "openapi" in data
    assert "/api/products" in data.get("paths", {})
    assert "/api/mandates" in data.get("paths", {})
    assert "/api/checkout/orders" in data.get("paths", {})


@pytest.mark.asyncio
async def test_favicon_endpoint(async_client: httpx.AsyncClient):
    """Verify GET /favicon.ico returns 204 No Content."""
    response = await async_client.get("/favicon.ico")
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
async def test_frontend_js_exports_and_imports(async_client: httpx.AsyncClient):
    """
    Verify components.js exports safe image helpers and app.js imports them.
    Ensures PLACEHOLDER_IMAGE is never undefined and uses base64 data URI to avoid attribute escapes.
    """
    components_res = await async_client.get("/assets/js/components.js")
    assert components_res.status_code == status.HTTP_200_OK
    c_text = components_res.text

    assert "export const PLACEHOLDER_IMAGE" in c_text
    assert "data:image/svg+xml;base64," in c_text
    assert "export function getSafeImageUrl" in c_text
    assert "export const IMG_FALLBACK_ATTR" in c_text
    assert "export function escapeHtml" in c_text

    app_res = await async_client.get("/assets/js/app.js")
    assert app_res.status_code == status.HTTP_200_OK
    a_text = app_res.text

    assert "PLACEHOLDER_IMAGE" in a_text
    assert "getSafeImageUrl" in a_text
    assert "IMG_FALLBACK_ATTR" in a_text
    assert "escapeHtml" in a_text
    # Ensure no naked unescaped inline SVGs inside onerror
    assert 'onerror="this.onerror=null; this.src=\'data:image/svg+xml' not in a_text


@pytest.mark.asyncio
async def test_seed_products_image_urls_valid(async_client: httpx.AsyncClient):
    """
    Verify all seeded product items returned by the API have valid, non-broken image URLs.
    Specifically checks Apple Watch does not use coffee cup image and Anker Power Bank does not use 404 URL.
    """
    response = await async_client.get("/api/products?page=1&page_size=50")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    items = data.get("items", [])
    assert len(items) > 0

    for item in items:
        img = item.get("image_url")
        assert img is not None and isinstance(img, str) and len(img) > 10
        assert img.startswith("http://") or img.startswith("https://") or img.startswith("data:image/")

        # Verify Apple Watch Series 9 uses watch image (1546868871-7041f2a55e12), not coffee cup (1509042239860)
        if "Apple Watch Series 9" in item.get("name", ""):
            assert "1509042239860" not in img
            assert "1546868871-7041f2a55e12" in img

        # Verify Anker 737 uses working powerbank image (1583863788434-e58a36330cf0), not deleted 404 (1609592426508)
        if "Anker 737" in item.get("name", ""):
            assert "1609592426508" not in img
            assert "1583863788434-e58a36330cf0" in img


@pytest.mark.asyncio
async def test_compare_products_api(async_client: httpx.AsyncClient):
    """
    Verify product comparison API succeeds for 2, 3, and 4 items,
    and returns proper comparison matrix and specs.
    """
    # Fetch first 4 product IDs
    p_res = await async_client.get("/api/products?page=1&page_size=4")
    assert p_res.status_code == status.HTTP_200_OK
    products = p_res.json().get("items", [])
    assert len(products) >= 4
    p_ids = [p["id"] for p in products]

    # Test 2 items
    compare2 = await async_client.get(f"/api/products/compare?ids={p_ids[0]}&ids={p_ids[1]}")
    assert compare2.status_code == status.HTTP_200_OK
    c2_data = compare2.json()
    assert len(c2_data["products"]) == 2
    assert "comparison_matrix" in c2_data
    assert "price_summary" in c2_data

    # Test 3 items
    compare3 = await async_client.get(f"/api/products/compare?ids={p_ids[0]}&ids={p_ids[1]}&ids={p_ids[2]}")
    assert compare3.status_code == status.HTTP_200_OK
    c3_data = compare3.json()
    assert len(c3_data["products"]) == 3

    # Test 4 items
    compare4 = await async_client.get(f"/api/products/compare?ids={p_ids[0]}&ids={p_ids[1]}&ids={p_ids[2]}&ids={p_ids[3]}")
    assert compare4.status_code == status.HTTP_200_OK
    c4_data = compare4.json()
    assert len(c4_data["products"]) == 4

    # Test empty IDs parameter
    compare_empty = await async_client.get("/api/products/compare?ids=")
    assert compare_empty.status_code == status.HTTP_400_BAD_REQUEST

    # Test non-existent ID
    compare_404 = await async_client.get("/api/products/compare?ids=non_existent_product_id")
    assert compare_404.status_code == status.HTTP_404_NOT_FOUND

@pytest.mark.asyncio
async def test_frontend_analytics_dashboard_elements(async_client: httpx.AsyncClient):
    """Verify Personal Shopping Analytics elements and script bindings are served in index.html and JS modules."""
    res = await async_client.get("/index.html")
    assert res.status_code == status.HTTP_200_OK
    assert 'id="home-analytics-section"' in res.text
    assert 'id="home-analytics-container"' in res.text
    assert 'id="btn-refresh-analytics"' in res.text

    # Verify components exports
    c_res = await async_client.get("/assets/js/components.js")
    assert c_res.status_code == status.HTTP_200_OK
    assert "renderAnalyticsDashboard" in c_res.text
    assert "renderAnalyticsSkeleton" in c_res.text
    assert "renderAnalyticsError" in c_res.text
    assert "renderAnalyticsEmptyState" in c_res.text

    # Verify api client method
    api_res = await async_client.get("/assets/js/api.js")
    assert api_res.status_code == status.HTTP_200_OK
    assert "getAnalytics" in api_res.text
    assert "/api/checkout/analytics/" in api_res.text

    # Verify app imports and invocations
    app_res = await async_client.get("/assets/js/app.js")
    assert app_res.status_code == status.HTTP_200_OK
    assert "renderAnalyticsDashboard" in app_res.text
    assert "loadHomeAnalytics" in app_res.text



@pytest.mark.asyncio
async def test_frontend_product_intelligence_elements(async_client: httpx.AsyncClient):
    """
    Phase 3C: Verify Individual Product Intelligence UI elements, components, and async loaders.
    Ensures modal template, SVG price chart renderer, and api.getProductIntelligence are properly served.
    """
    # Verify components.js has product intelligence renderers and modal container
    c_res = await async_client.get("/assets/js/components.js")
    assert c_res.status_code == status.HTTP_200_OK
    assert "renderProductIntelligenceContent" in c_res.text
    assert "renderPriceHistoryChartSVG" in c_res.text
    assert "renderProductIntelligenceSkeleton" in c_res.text
    assert "renderProductIntelligenceError" in c_res.text
    assert 'id="product-intelligence-section"' in c_res.text
    # Retailer cross-comparison 4-way branching assertions
    assert "Identical Pricing Across Stores" in c_res.text
    assert "($0.00 Spread)" in c_res.text
    assert "in-stock merchants currently offer this product at the identical price" in c_res.text
    assert "Single in-stock merchant offer — no alternative retailer spread currently." in c_res.text
    assert "No active in-stock merchant offers for cross-comparison." in c_res.text
    assert 'id="product-intelligence-container"' in c_res.text
    assert "Product Intelligence &amp; Price History" in c_res.text

    # Verify api.js has getProductIntelligence method
    api_res = await async_client.get("/assets/js/api.js")
    assert api_res.status_code == status.HTTP_200_OK
    assert "getProductIntelligence" in api_res.text
    assert "/intelligence" in api_res.text

    # Verify app.js imports and invokes product intelligence
    app_res = await async_client.get("/assets/js/app.js")
    assert app_res.status_code == status.HTTP_200_OK
    assert "renderProductIntelligenceContent" in app_res.text
    assert "loadProductIntelligence" in app_res.text
    assert "bindIntelligenceEvents" in app_res.text


@pytest.mark.asyncio
async def test_frontend_auth_elements_and_logo(async_client: httpx.AsyncClient):
    """
    Phase 3: Verify Frontend Authentication HTML structure and vector logo emblem.
    - index.html serves section-login and section-register containers.
    - Header navigation has auth-nav-slot and mobile-auth-nav-slot.
    - Header uses official stylized vector SVG emblem and NOT a generic letter or shopping bag.
    """
    res = await async_client.get("/index.html")
    assert res.status_code == status.HTTP_200_OK
    html = res.text

    # Verify dedicated authentication page sections exist
    assert 'id="section-login"' in html
    assert 'id="section-register"' in html

    # Verify header navigation dynamic auth slots
    assert 'id="auth-nav-slot"' in html
    assert 'id="mobile-auth-nav-slot"' in html

    # Verify official BUYWISE AI stylized orange vector logo SVG is used in the header brand link
    assert "<svg" in html
    assert 'viewBox="0 0 100 100"' in html
    assert "bw-header-grad" in html or "bw-grad" in html
    # Ensure generic letter placeholder is NOT present in brand header
    assert 'class="text-xl font-black text-amber-500">B</div>' not in html


@pytest.mark.asyncio
async def test_frontend_config_auth_token_helpers(async_client: httpx.AsyncClient):
    """
    Phase 3: Verify config.js exports authentication and session token helpers.
    - Storage keys: AUTH_TOKEN, AUTH_USER, SESSION_TOKEN.
    - Token getters/setters: getAuthToken, setAuthToken, getGuestSessionToken, setGuestSessionToken.
    - Auth state helpers: getAuthUser, setAuthUser, clearAuth, isAuthenticated.
    """
    res = await async_client.get("/assets/js/config.js")
    assert res.status_code == status.HTTP_200_OK
    text = res.text

    assert "AUTH_TOKEN:" in text
    assert "AUTH_USER:" in text
    assert "SESSION_TOKEN:" in text
    assert "export function getAuthToken" in text
    assert "export function setAuthToken" in text
    assert "export function getGuestSessionToken" in text
    assert "export function setGuestSessionToken" in text
    assert "export function getAuthUser" in text
    assert "export function setAuthUser" in text
    assert "export function clearAuth" in text
    assert "export function isAuthenticated" in text


@pytest.mark.asyncio
async def test_frontend_api_auth_methods_and_headers(async_client: httpx.AsyncClient):
    """
    Phase 3: Verify api.js attaches auth/session tokens, handles 401 expiration, and defines auth methods.
    - Request method sends Authorization: Bearer <authToken> and X-Guest-Session-Token: <guestToken>.
    - Automatic 401 interception triggers clearAuth and dispatches buywise:auth-expired.
    - Auth API methods: register, login, getMe, logout, bootstrapGuestSession.
    """
    res = await async_client.get("/assets/js/api.js")
    assert res.status_code == status.HTTP_200_OK
    text = res.text

    # Header attachment
    assert "Authorization" in text
    assert "Bearer" in text
    assert "X-Guest-Session-Token" in text

    # 401 expiration dispatch
    assert "401" in text
    assert "buywise:auth-expired" in text
    assert "clearAuth" in text

    # Auth methods
    assert "async register(" in text
    assert "async login(" in text
    assert "async getMe(" in text
    assert "async logout(" in text
    assert "async bootstrapGuestSession(" in text
    assert "/api/auth/register" in text
    assert "/api/auth/login" in text
    assert "/api/auth/logout" in text
    assert "/api/auth/me" in text

    # Session linking parameters forwarded
    assert "guest_session_id" in text
    assert "guest_session_token" in text


@pytest.mark.asyncio
async def test_frontend_components_auth_renderers(async_client: httpx.AsyncClient):
    """
    Phase 3: Verify components.js provides split-screen Login, Register, vector logo, and user nav widget.
    - renderBuywiseLogo with scalable vector SVG.
    - renderLoginPage split-screen layout with email, password toggle, and guest bypass.
    - renderRegisterPage split-screen layout with name, email, password confirmation, safe limits, and session linking badge.
    - renderUserNavWidget dynamically handling authenticated user vs guest state.
    """
    res = await async_client.get("/assets/js/components.js")
    assert res.status_code == status.HTTP_200_OK
    text = res.text

    # Vector logo renderer
    assert "export function renderBuywiseLogo" in text
    assert 'viewBox="0 0 100 100"' in text

    # Split-screen Login Page
    assert "export function renderLoginPage" in text
    assert 'id="login-form"' in text
    assert 'id="login-email"' in text
    assert 'id="login-password"' in text
    assert "js-toggle-password" in text
    assert "Continue browsing as Guest" in text

    # Split-screen Registration Page
    assert "export function renderRegisterPage" in text
    assert 'id="register-form"' in text
    assert 'id="register-name"' in text
    assert 'id="register-email"' in text
    assert 'id="register-password"' in text
    assert 'id="register-confirm-password"' in text
    assert 'maxlength="72"' in text
    assert "Your active guest shopping session will be safely claimed upon sign up" in text

    # Dynamic Navigation Widget
    assert "export function renderUserNavWidget" in text
    assert "btn-header-logout" in text
    assert "nav-auth-login" in text
    assert "nav-auth-register" in text


@pytest.mark.asyncio
async def test_frontend_app_auth_routing_and_session_linking(async_client: httpx.AsyncClient):
    """
    Phase 3: Verify app.js handles auth routes, session claiming, and navigation lifecycle.
    - Hash router supports 'login' and 'register'.
    - Automatically bootstraps guest session token on initial load.
    - Passes guest_session_id and guest_session_token on login/register.
    - Listens for auth state changes and expired sessions.
    """
    res = await async_client.get("/assets/js/app.js")
    assert res.status_code == status.HTTP_200_OK
    text = res.text

    # Route handling
    assert '"login"' in text
    assert '"register"' in text
    assert "loadLoginSection" in text
    assert "loadRegisterSection" in text

    # Guest session bootstrap
    assert "api.bootstrapGuestSession()" in text

    # Auth API integration
    assert "api.login" in text
    assert "api.register" in text

    # Auth lifecycle
    assert "initAuthUI" in text
    assert "updateAuthNav" in text
    assert "handleLogout" in text
    assert "buywise:auth-changed" in text
    assert "buywise:auth-expired" in text

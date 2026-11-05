"""
AI-Powered Multilingual Shopping Intelligence Platform
PayPal Sandbox REST API Client.
Implements OAuth 2.0 client-credentials authentication, token caching with auto-refresh,
order creation, order capture, and status retrieval via PayPal Orders v2.
Enforces strict Sandbox isolation, sanitized logging, and zero credential leakage.
"""

import base64
import logging
import threading
import time
from decimal import Decimal
from typing import Any
import httpx

from backend.app.core.config import get_settings

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Exception Hierarchy
# -------------------------------------------------------------------------

class PayPalError(Exception):
    """Base exception for all PayPal REST API interactions."""
    pass


class PayPalAuthError(PayPalError):
    """Raised when PayPal client credentials are missing, invalid, or rejected."""
    pass


class PayPalRateLimitError(PayPalError):
    """Raised when PayPal returns HTTP 429 Too Many Requests."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PayPalTimeoutError(PayPalError):
    """Raised when a PayPal HTTP request exceeds the network timeout."""
    pass


class PayPalOrderNotFoundError(PayPalError):
    """Raised when an order is not found on PayPal (HTTP 404)."""
    pass


class PayPalCaptureError(PayPalError):
    """Raised when a PayPal capture is declined or permanently rejected."""

    def __init__(self, message: str, issue: str | None = None, details: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.issue = issue
        self.details = details or []


class PayPalAPIError(PayPalError):
    """Raised when PayPal returns an unexpected 4xx or 5xx response."""

    def __init__(self, message: str, status_code: int | None = None, response_body: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body or {}


# -------------------------------------------------------------------------
# PayPal REST Client
# -------------------------------------------------------------------------

class PayPalClient:
    """
    Official PayPal Orders v2 REST client for PayPal Sandbox.
    Handles OAuth token lifecycle, idempotent order creation, and capture operations.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._client_id: str = client_id or settings.PAYPAL_CLIENT_ID
        self._client_secret: str | None = (
            client_secret
            if client_secret is not None
            else (settings.PAYPAL_CLIENT_SECRET.get_secret_value() if settings.PAYPAL_CLIENT_SECRET else None)
        )
        self.base_url: str = (base_url or settings.PAYPAL_BASE_URL).rstrip("/")
        self.timeout: float = timeout or settings.PAYPAL_TIMEOUT_SECONDS

        # Security invariant: Enforce Sandbox endpoint in sandbox mode
        if (settings.PAYPAL_MODE == "sandbox" or settings.PAYPAL_ENV == "sandbox") and "sandbox" not in self.base_url:
            raise PayPalAuthError(
                f"Security violation: configured PayPal base URL '{self.base_url}' is not a Sandbox endpoint."
            )

        # Thread-safe in-memory token cache
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

        # Injected or internal HTTPX client
        self._injected_client: httpx.Client | None = http_client

    @property
    def is_configured(self) -> bool:
        """Return True if both client_id and client_secret are provided."""
        return bool(self._client_id and self._client_secret and self._client_secret.strip())

    def _get_http_client(self) -> httpx.Client:
        """Return injected client or a fresh client with configured timeout."""
        if self._injected_client is not None:
            return self._injected_client
        return httpx.Client(timeout=self.timeout)

    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Obtain a valid OAuth 2.0 Bearer access token using client-credentials grant.
        Uses cached token if available and not expiring within 300 seconds.
        """
        if not self.is_configured:
            raise PayPalAuthError(
                "PayPal Sandbox credentials are not configured. Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET."
            )

        with self._token_lock:
            now = time.time()
            if not force_refresh and self._token and now < self._token_expires_at:
                return self._token

            token_url = f"{self.base_url}/v1/oauth2/token"
            auth_header = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()

            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Accept-Language": "en_US",
            }
            data = {"grant_type": "client_credentials"}

            logger.info("Requesting fresh PayPal OAuth token from %s", token_url)
            try:
                client = self._get_http_client()
                should_close = self._injected_client is None
                try:
                    resp = client.post(token_url, headers=headers, data=data)
                finally:
                    if should_close:
                        client.close()

                if resp.status_code in (401, 403):
                    logger.error("PayPal OAuth rejected: HTTP %s", resp.status_code)
                    raise PayPalAuthError("PayPal client credentials were rejected by PayPal Sandbox.")

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    raise PayPalRateLimitError("PayPal rate limit exceeded during authentication.", retry_after=retry_after)

                if resp.is_error:
                    raise PayPalAPIError(
                        f"PayPal OAuth failed with status {resp.status_code}",
                        status_code=resp.status_code,
                    )

                body = resp.json()
                access_token = body.get("access_token")
                expires_in = int(body.get("expires_in", 32400))

                if not access_token:
                    raise PayPalAPIError("PayPal OAuth response missing 'access_token'.")

                self._token = access_token
                # Buffer expiration by 300 seconds (5 minutes)
                self._token_expires_at = now + max(60, expires_in - 300)
                logger.info("PayPal OAuth token acquired successfully (expires in %ds)", expires_in)
                return self._token

            except httpx.TimeoutException as exc:
                logger.warning("PayPal OAuth timed out after %ss", self.timeout)
                raise PayPalTimeoutError(f"PayPal OAuth request timed out: {exc}") from exc
            except (httpx.ConnectError, httpx.RequestError) as exc:
                logger.error("PayPal OAuth connection failure: %s", exc)
                raise PayPalAPIError(f"PayPal OAuth connection failed: {exc}") from exc

    def _auth_headers(self, request_id: str | None = None) -> dict[str, str]:
        """Construct standard authenticated headers with optional PayPal-Request-Id idempotency key."""
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return headers

    def create_order(
        self,
        total_amount: Decimal,
        currency: str,
        internal_order_number: str,
        paypal_request_id: str,
        return_url: str = "http://localhost:5500/?paypal_return=true",
        cancel_url: str = "http://localhost:5500/?paypal_cancel=true",
        description: str = "AI Shopping Intelligence Demo Checkout",
    ) -> dict[str, Any]:
        """
        Create a PayPal order via POST /v2/checkout/orders.
        Uses exact Decimal amount formatting and stable PayPal-Request-Id idempotency.
        """
        url = f"{self.base_url}/v2/checkout/orders"
        amount_str = f"{total_amount:.2f}"
        norm_currency = currency.strip().upper()

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": internal_order_number,
                    "description": description[:127],
                    "amount": {
                        "currency_code": norm_currency,
                        "value": amount_str,
                    },
                }
            ],
            "application_context": {
                "brand_name": "AI Shopping Intelligence",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        }

        headers = self._auth_headers(request_id=paypal_request_id)
        logger.info("Creating PayPal order for %s %s [Ref: %s]", norm_currency, amount_str, internal_order_number)

        try:
            client = self._get_http_client()
            should_close = self._injected_client is None
            try:
                resp = client.post(url, headers=headers, json=payload)
            finally:
                if should_close:
                    client.close()

            if resp.status_code in (401, 403):
                # Token may have expired prematurely; force refresh once
                headers = self._auth_headers(request_id=paypal_request_id)
                client = self._get_http_client()
                should_close = self._injected_client is None
                try:
                    resp = client.post(url, headers=headers, json=payload)
                finally:
                    if should_close:
                        client.close()

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                raise PayPalRateLimitError("PayPal rate limit exceeded during order creation.", retry_after=retry_after)

            if resp.is_error:
                error_body = resp.json() if resp.content else {}
                err_msg = error_body.get("message", f"HTTP {resp.status_code}")
                logger.error("PayPal order creation error: %s (Status: %s)", err_msg, resp.status_code)
                raise PayPalAPIError(
                    f"PayPal order creation failed: {err_msg}",
                    status_code=resp.status_code,
                    response_body=error_body,
                )

            data = resp.json()
            order_id = data.get("id")
            if not order_id:
                raise PayPalAPIError("PayPal response missing order 'id'.")

            # Extract approval link
            approval_url = None
            for link in data.get("links", []):
                if link.get("rel") == "approve":
                    approval_url = link.get("href")
                    break

            if not approval_url:
                raise PayPalAPIError("PayPal order created but missing buyer 'approve' link.")

            data["_extracted_approval_url"] = approval_url
            logger.info("PayPal order created successfully: %s (Status: %s)", order_id, data.get("status"))
            return data

        except httpx.TimeoutException as exc:
            logger.warning("PayPal create_order timed out: %s", exc)
            raise PayPalTimeoutError(f"PayPal create_order request timed out: {exc}") from exc
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("PayPal create_order connection failure: %s", exc)
            raise PayPalAPIError(f"PayPal connection error: {exc}") from exc

    def capture_order(self, paypal_order_id: str, paypal_capture_request_id: str) -> dict[str, Any]:
        """
        Capture an approved PayPal order via POST /v2/checkout/orders/{id}/capture.
        Requires a distinct stable PayPal-Request-Id separate from the order creation ID.
        """
        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/capture"
        headers = self._auth_headers(request_id=paypal_capture_request_id)
        logger.info("Executing PayPal capture for order %s [ReqId: %s]", paypal_order_id, paypal_capture_request_id)

        try:
            client = self._get_http_client()
            should_close = self._injected_client is None
            try:
                resp = client.post(url, headers=headers, json={})
            finally:
                if should_close:
                    client.close()

            if resp.status_code == 404:
                raise PayPalOrderNotFoundError(f"PayPal order '{paypal_order_id}' was not found.")

            if resp.status_code in (422, 400):
                body = resp.json() if resp.content else {}
                details = body.get("details", [])
                issue = details[0].get("issue") if details else body.get("name")
                description = details[0].get("description") if details else body.get("message")
                logger.error("PayPal capture rejected: issue=%s description=%s", issue, description)
                raise PayPalCaptureError(
                    f"PayPal capture rejected: {issue} - {description}",
                    issue=issue,
                    details=details,
                )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                raise PayPalRateLimitError("PayPal rate limit exceeded during order capture.", retry_after=retry_after)

            if resp.is_error:
                body = resp.json() if resp.content else {}
                raise PayPalAPIError(
                    f"PayPal capture failed with status {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=body,
                )

            data = resp.json()
            status = data.get("status")
            logger.info("PayPal capture completed for order %s with status: %s", paypal_order_id, status)
            return data

        except httpx.TimeoutException as exc:
            logger.warning("PayPal capture_order timed out for order %s: %s", paypal_order_id, exc)
            raise PayPalTimeoutError(f"PayPal capture_order request timed out: {exc}") from exc
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("PayPal capture_order connection failure: %s", exc)
            raise PayPalAPIError(f"PayPal connection error: {exc}") from exc

    def get_order_details(self, paypal_order_id: str) -> dict[str, Any]:
        """
        Retrieve authoritative order details via GET /v2/checkout/orders/{id}.
        Used for reconciliation after network timeouts or verification.
        """
        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}"
        headers = self._auth_headers()
        logger.info("Fetching PayPal order details for %s", paypal_order_id)

        try:
            client = self._get_http_client()
            should_close = self._injected_client is None
            try:
                resp = client.get(url, headers=headers)
            finally:
                if should_close:
                    client.close()

            if resp.status_code == 404:
                raise PayPalOrderNotFoundError(f"PayPal order '{paypal_order_id}' was not found.")

            if resp.is_error:
                body = resp.json() if resp.content else {}
                raise PayPalAPIError(
                    f"PayPal get_order_details failed with status {resp.status_code}",
                    status_code=resp.status_code,
                    response_body=body,
                )

            return resp.json()

        except httpx.TimeoutException as exc:
            logger.warning("PayPal get_order_details timed out for order %s: %s", paypal_order_id, exc)
            raise PayPalTimeoutError(f"PayPal get_order_details request timed out: {exc}") from exc
        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error("PayPal get_order_details connection failure: %s", exc)
            raise PayPalAPIError(f"PayPal connection error: {exc}") from exc

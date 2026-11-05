"""
AI-Powered Multilingual Shopping Intelligence Platform
Checkout & PayPal Payment API Endpoints.
Routes:
  POST /api/checkout/orders               - Initiate checkout, validate policy, create PayPal order
  POST /api/checkout/orders/{id}/capture  - Capture approved PayPal payment and verify settlement
  GET  /api/checkout/orders/{id}          - Retrieve order status and item snapshots
  GET  /api/checkout/history/{session_id} - Retrieve paginated payment history for session
"""

import logging
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.auth import get_current_user, get_current_user_optional
from backend.app.models.user import User
from backend.app.schemas.checkout import (
    CheckoutOrderCreate,
    PaymentOrderResponse,
    PaymentOrderListResponse,
    RetailerRedirectResponse,
    PersonalAnalyticsResponse,
)
from backend.app.services.checkout_service import (
    CheckoutService,
    CheckoutError,
    InvalidSessionError,
    ProductNotFoundError,
    RetailerOfferNotFoundError,
    OfferOutOfStockError,
    ExternalRetailerCheckoutError,
    MandatePolicyViolationError,
    BudgetOverrideRequiredError,
    UnsupportedCurrencyError,
    OrderExpiredError,
    OrderOwnershipError,
    MissingGuestProofError,
    InvalidGuestProofError,
    InvalidOrderStateError,
    DuplicateCaptureError,
    CaptureReconciliationRequiredError,
    OrderNotApprovedError,
)
from backend.app.services.paypal_client import (
    PayPalError,
    PayPalAuthError,
    PayPalRateLimitError,
    PayPalTimeoutError,
    PayPalOrderNotFoundError,
    PayPalCaptureError,
    PayPalAPIError,
)
from backend.app.models.payment import (
    PaymentVerificationError,
    CaptureAmountMismatchError,
    CaptureCurrencyMismatchError,
    UnverifiedCaptureError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkout", tags=["Checkout & Payments"])


@router.post(
    "/orders",
    response_model=PaymentOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate Checkout & Create PayPal Sandbox Order",
    description=(
        "Validates product catalog availability, rechecks spending mandate limits deterministically, "
        "creates a local PaymentOrder draft, and generates a PayPal Orders v2 approval URL."
    ),
)
def create_checkout(
    payload: CheckoutOrderCreate,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaymentOrderResponse:
    """Create a new payment order and return PayPal approval link. Requires authenticated user."""
    try:
        effective_token = x_guest_session_token or payload.session_token
        return CheckoutService.create_checkout_order(
            db=db,
            request=payload,
            user_id=current_user.id,
            guest_session_token=effective_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ExternalRetailerCheckoutError as exc:
        logger.info("External retailer checkout rejected from demo gateway: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "external_retailer_checkout",
                "message": str(exc),
                "retailer_name": exc.retailer_name,
                "product_name": exc.product_name,
                "product_url": exc.product_url,
                "price": str(exc.price),
                "currency": exc.currency,
                "requires_external_checkout": True,
            },
        )
    except BudgetOverrideRequiredError as exc:
        logger.warning("Checkout blocked pending user budget override confirmation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "budget_override_required",
                "message": "Purchase exceeds preferred spending mandate budget. Explicit user confirmation is required to proceed.",
                "requires_override_confirmation": True,
                "reasons": exc.reasons,
                "exceeded_amount": exc.exceeded_amount,
                "currency": exc.currency,
                "preferred_budget": exc.preferred_budget,
                "order_total": exc.order_total,
            },
        )
    except MandatePolicyViolationError as exc:
        logger.warning("Checkout blocked by spending policy: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "mandate_policy_violation",
                "message": "Purchase violates active spending mandate limits.",
                "reasons": exc.reasons,
            },
        )
    except (OfferOutOfStockError, UnsupportedCurrencyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except (ProductNotFoundError, RetailerOfferNotFoundError, InvalidSessionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except PayPalAuthError as exc:
        logger.error("PayPal authentication failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal Sandbox authentication failed. Check gateway credentials.",
        )
    except PayPalRateLimitError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="PayPal rate limit exceeded. Please retry shortly.",
            headers=headers,
        )
    except PayPalTimeoutError as exc:
        logger.warning("PayPal request timed out: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="PayPal order creation timed out.",
        )
    except (PayPalAPIError, PayPalError) as exc:
        logger.error("PayPal provider error during checkout: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PayPal provider error: {str(exc)}",
        )


@router.post(
    "/orders/{order_id}/capture",
    response_model=PaymentOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Capture Approved PayPal Order",
    description=(
        "Executes payment capture on PayPal Sandbox after buyer approval, "
        "authoritatively verifies settlement amount, currency, and gateway status, "
        "and updates the local order state to COMPLETED."
    ),
)
def capture_order(
    order_id: str,
    session_id: Annotated[str, Query(description="Active ShoppingSession UUID owning the order")],
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PaymentOrderResponse:
    """Capture an approved payment order."""
    try:
        return CheckoutService.capture_checkout_order(
            db=db,
            order_id=order_id,
            session_id=session_id,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except OrderExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except InvalidOrderStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except DuplicateCaptureError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except CaptureReconciliationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail=str(exc),
        )
    except OrderNotApprovedError as exc:
        logger.warning("Capture attempted before buyer approval for order %s: %s", order_id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "order_not_approved",
                "message": str(exc),
                "order_id": order_id,
            },
        )
    except (CaptureAmountMismatchError, CaptureCurrencyMismatchError, UnverifiedCaptureError, PaymentVerificationError) as exc:
        logger.error("Capture verification failed for order %s: %s", order_id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Payment capture verification rejected: {str(exc)}",
        )
    except PayPalOrderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except PayPalCaptureError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PayPal capture rejected: {str(exc)}",
        )
    except PayPalTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="PayPal capture request timed out.",
        )
    except (PayPalAPIError, PayPalError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PayPal provider error during capture: {str(exc)}",
        )


@router.post(
    "/orders/{order_id}/cancel",
    response_model=PaymentOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Pending Checkout Order",
    description="Explicitly marks an uncaptured payment order as CANCELLED upon buyer cancellation.",
)
def cancel_order(
    order_id: str,
    session_id: Annotated[str, Query(description="Active ShoppingSession UUID owning the order")],
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PaymentOrderResponse:
    """Cancel a pending payment order."""
    try:
        return CheckoutService.cancel_checkout_order(
            db=db,
            order_id=order_id,
            session_id=session_id,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidOrderStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get(
    "/orders/{order_id}",
    response_model=PaymentOrderResponse,
    summary="Retrieve Payment Order Status",
    description="Retrieve authoritative order details, current status, item snapshots, and verified settlement records.",
)
def get_order_status(
    order_id: str,
    session_id: Annotated[str, Query(description="Active ShoppingSession UUID owning the order")],
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PaymentOrderResponse:
    """Fetch order status by order ID."""
    try:
        return CheckoutService.get_order_status(
            db=db,
            order_id=order_id,
            session_id=session_id,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidOrderStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/orders/by-paypal-id/{paypal_order_id}",
    response_model=PaymentOrderResponse,
    summary="Lookup Payment Order by PayPal Order ID Token",
    description="Retrieve local payment order matching the PayPal order ID / approval token.",
)
def get_order_by_paypal_id(
    paypal_order_id: str,
    session_id: Annotated[str, Query(description="Active ShoppingSession UUID owning the order")],
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PaymentOrderResponse:
    """Fetch order status by PayPal order ID token."""
    try:
        return CheckoutService.get_order_by_paypal_id(
            db=db,
            paypal_order_id=paypal_order_id,
            session_id=session_id,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidOrderStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/history/{session_id}",
    response_model=PaymentOrderListResponse,
    summary="Retrieve Session Payment History",
    description="Retrieve a paginated chronological history of completed and pending payment orders for a guest session.",
)
def get_payment_history(
    session_id: str,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page (max 100)")] = 20,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PaymentOrderListResponse:
    """Retrieve session payment order history."""
    try:
        return CheckoutService.get_session_payment_history(
            db=db,
            session_id=session_id,
            page=page,
            page_size=page_size,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


@router.get(
    "/analytics/{session_id}",
    response_model=PersonalAnalyticsResponse,
    summary="Retrieve Personal Shopping Analytics",
    description="Retrieve personal shopping analytics, spending totals by currency, category/retailer breakdowns, and mandate utilization for a guest session.",
)
def get_personal_analytics(
    session_id: str,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PersonalAnalyticsResponse:
    """Fetch authoritative personal shopping analytics for a guest shopping session."""
    try:
        return CheckoutService.get_session_analytics(
            db=db,
            session_id=session_id,
            user_id=current_user.id if current_user else None,
            guest_session_token=x_guest_session_token,
        )
    except MissingGuestProofError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        )
    except (InvalidGuestProofError, OrderOwnershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


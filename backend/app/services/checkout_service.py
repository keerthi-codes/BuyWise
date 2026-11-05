"""
AI-Powered Multilingual Shopping Intelligence Platform
Checkout Service & Business Logic Orchestrator.
Coordinates product validation, deterministic spending policy gates,
idempotent PayPal Sandbox order creation, buyer approval, and authoritative capture verification.
Enforces strict boundaries: AI suggests, Backend validates, User approves, PayPal processes.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import logging
from typing import Any
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.models.shopping import Product, RetailerOffer, ShoppingSession, SpendingMandate
from backend.app.models.payment import (
    PaymentOrder,
    OrderItem,
    PaymentCapture,
    PaymentOrderStatus,
    InvalidStateTransitionError,
    PaymentVerificationError,
    CaptureAmountMismatchError,
    CaptureCurrencyMismatchError,
    UnverifiedCaptureError,
    generate_uuid,
)
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.schemas.checkout import (
    CheckoutOrderCreate,
    PaymentOrderResponse,
    OrderItemResponse,
    PaymentCaptureResponse,
    PaymentHistoryItemResponse,
    PaymentOrderListResponse,
    RetailerRedirectResponse,
    PersonalAnalyticsResponse,
    CurrencySpendingSummary,
    MonthlySpendingTrend,
    CategorySpending,
    RetailerSpending,
    MandateBudgetUtilization,
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
from backend.app.services.policy_engine import SpendingPolicyEngine

logger = logging.getLogger(__name__)

# Authoritative list of standard currencies supported in PayPal Sandbox
SUPPORTED_PAYPAL_CURRENCIES = {
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "INR", "CHF", "HKD", "SGD", "SEK", "NZD", "MXN"
}


# -------------------------------------------------------------------------
# Checkout Exceptions Hierarchy
# -------------------------------------------------------------------------

class CheckoutError(Exception):
    """Base exception for checkout service errors."""
    pass


class InvalidSessionError(CheckoutError):
    """Raised when an active session cannot be resolved."""
    pass


class ProductNotFoundError(CheckoutError):
    """Raised when a requested product ID is not found."""
    pass


class RetailerOfferNotFoundError(CheckoutError):
    """Raised when a requested retailer offer ID is not found."""
    pass


class OfferOutOfStockError(CheckoutError):
    """Raised when a selected offer is out of stock."""
    pass


class ExternalRetailerCheckoutError(CheckoutError):
    """
    Raised when a user attempts internal demo checkout for a live Channel3 marketplace offer.
    Directs the user to complete purchase on the external merchant's website.
    """

    def __init__(
        self,
        retailer_name: str,
        product_url: str,
        product_name: str,
        price: Decimal,
        currency: str,
    ):
        super().__init__(
            f"Item '{product_name}' is sold by external merchant '{retailer_name}'. "
            f"You will complete your purchase directly on their website."
        )
        self.retailer_name = retailer_name
        self.product_url = product_url
        self.product_name = product_name
        self.price = price
        self.currency = currency


class MandatePolicyViolationError(CheckoutError):
    """Raised when a purchase violates deterministic spending limits."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class BudgetOverrideRequiredError(CheckoutError):
    """Raised when a candidate checkout exceeds the spending mandate budget/item ceiling and requires user override confirmation."""

    def __init__(
        self,
        reasons: list[str],
        exceeded_amount: float,
        currency: str,
        preferred_budget: float | None = None,
        order_total: float | None = None,
    ):
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.exceeded_amount = exceeded_amount
        self.currency = currency
        self.preferred_budget = preferred_budget
        self.order_total = order_total


class UnsupportedCurrencyError(CheckoutError):
    """Raised when an order currency is not supported by PayPal Sandbox."""
    pass


class OrderExpiredError(CheckoutError):
    """Raised when attempting capture on an order whose 15-minute TTL elapsed."""
    pass


class OrderOwnershipError(CheckoutError):
    """Raised when attempting to access or capture an order belonging to another session."""
    pass


class MissingGuestProofError(OrderOwnershipError):
    """Raised when guest session proof of possession is missing."""
    pass


class InvalidGuestProofError(OrderOwnershipError):
    """Raised when guest session proof of possession is invalid."""
    pass


class InvalidOrderStateError(CheckoutError):
    """Raised when an order is not in a valid state for the requested operation."""
    pass


class DuplicateCaptureError(CheckoutError):
    """Raised when a capture settlement has already been recorded."""
    pass


class CaptureReconciliationRequiredError(CheckoutError):
    """Raised when a capture request outcome is uncertain and requires reconciliation."""
    pass


class OrderNotApprovedError(CheckoutError):
    """Raised when payment capture is attempted before the buyer approves the order on PayPal."""
    pass


# -------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------

def mask_email(email: str | None) -> str | None:
    """Mask email address for privacy (e.g. buyer@example.com -> b***r@example.com)."""
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"


# -------------------------------------------------------------------------
# Checkout Service
# -------------------------------------------------------------------------

class CheckoutService:
    """
    Coordinates checkout initialization, policy validation, order creation,
    and authoritative payment capture verification.
    """

    @staticmethod
    def create_checkout_order(
        db: Session,
        request: CheckoutOrderCreate,
        paypal_client: PayPalClient | None = None,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderResponse:
        """
        Create a new payment order and generate a PayPal Sandbox approval link.
        Validates product eligibility, offer freshness, and spending mandate limits.
        """
        client = paypal_client or PayPalClient()

        # 1. Resolve or verify active guest session
        session = None
        if request.session_id:
            session = db.get(ShoppingSession, request.session_id)
            if not session and not request.session_identifier:
                raise InvalidSessionError(f"Session '{request.session_id}' not found.")

        if not session and request.session_identifier:
            session = MandateRepository.get_or_create_session(
                db=db,
                session_identifier=request.session_identifier,
            )

        if not session:
            raise InvalidSessionError("Could not resolve an active guest shopping session.")

        if not user_id:
            raise OrderOwnershipError("Authentication required to place an order.")

        if session.user_id:
            if session.user_id != user_id:
                raise OrderOwnershipError("Shopping session belongs to another account.")
        else:
            if session.session_token:
                effective_token = guest_session_token or request.session_token
                if not effective_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if effective_token != session.session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")
                session.user_id = user_id
                db.flush()
            else:
                session.user_id = user_id
                db.flush()

        # 2. Local session-scoped idempotency check
        if request.idempotency_key:
            existing_order = (
                db.query(PaymentOrder)
                .filter_by(session_id=session.id, idempotency_key=request.idempotency_key)
                .first()
            )
            if existing_order:
                # If already completed or still valid and active, return existing order
                if existing_order.status == PaymentOrderStatus.COMPLETED.value:
                    logger.info("Idempotent hit: returning existing COMPLETED order %s", existing_order.id)
                    return PaymentOrderResponse.model_validate(existing_order)
                if not existing_order.is_expired and existing_order.status in (
                    PaymentOrderStatus.CREATED.value,
                    PaymentOrderStatus.APPROVED.value,
                ):
                    logger.info("Idempotent hit: returning existing active order %s", existing_order.id)
                    return PaymentOrderResponse.model_validate(existing_order)

        # 3. Validate items, pricing, and purchase track
        order_currency = request.currency.strip().upper()
        if order_currency not in SUPPORTED_PAYPAL_CURRENCIES:
            raise UnsupportedCurrencyError(
                f"Currency '{order_currency}' is not supported for PayPal Sandbox checkout."
            )

        validated_items: list[dict[str, Any]] = []
        max_item_unit_price = Decimal("0.00")
        total_quantity = 0

        for item_in in request.items:
            prod_id = item_in.product_id
            offer_id = item_in.retailer_offer_id

            if offer_id:
                offer = db.query(RetailerOffer).filter_by(id=offer_id).first()
                if not offer:
                    raise RetailerOfferNotFoundError(f"Retailer offer '{offer_id}' was not found.")

                product = offer.product
                if not product:
                    raise ProductNotFoundError(f"Product for offer '{offer_id}' was not found.")

                # PURCHASE TRACK ENFORCEMENT:
                # Reject external Channel3 marketplace items from internal demo checkout!
                if not product.is_synthetic or product.provider != "synthetic":
                    logger.info("External marketplace item requested: redirecting to %s", offer.product_url)
                    raise ExternalRetailerCheckoutError(
                        retailer_name=offer.retailer_name,
                        product_url=offer.product_url,
                        product_name=product.name,
                        price=Decimal(str(offer.price)),
                        currency=offer.currency,
                    )

                # Revalidate stock availability
                if offer.availability.upper() not in ("IN_STOCK", "LIMITED_STOCK", "LOW_STOCK"):
                    raise OfferOutOfStockError(
                        f"Offer from '{offer.retailer_name}' is currently {offer.availability}."
                    )

                # Server-calculated authoritative price
                server_unit_price = Decimal(str(offer.price))
                item_currency = offer.currency.strip().upper()
                product_name = product.name
                retailer_name = offer.retailer_name
            elif prod_id:
                product = db.query(Product).filter_by(id=prod_id).first()
                if not product:
                    raise ProductNotFoundError(f"Product '{prod_id}' was not found.")

                if not product.is_synthetic or product.provider != "synthetic":
                    raise ExternalRetailerCheckoutError(
                        retailer_name=product.brand,
                        product_url=product.image_url or "https://example.com",
                        product_name=product.name,
                        price=Decimal(str(item_in.unit_price)),
                        currency=order_currency,
                    )

                server_unit_price = Decimal(str(item_in.unit_price))
                item_currency = item_in.currency.strip().upper()
                product_name = product.name
                retailer_name = item_in.retailer_name or "Synthetic Demo Store"
            else:
                # Custom synthetic item snapshot
                server_unit_price = Decimal(str(item_in.unit_price))
                item_currency = item_in.currency.strip().upper()
                product_name = item_in.product_name
                retailer_name = item_in.retailer_name or "Demo Store"

            # Enforce currency consistency across items
            if item_currency != order_currency:
                raise UnsupportedCurrencyError(
                    f"Item currency '{item_currency}' differs from order currency '{order_currency}'."
                )

            quantity = max(1, item_in.quantity)
            subtotal = server_unit_price * quantity
            total_quantity += quantity
            if server_unit_price > max_item_unit_price:
                max_item_unit_price = server_unit_price

            validated_items.append({
                "product_id": prod_id,
                "retailer_offer_id": offer_id,
                "product_name": product_name,
                "retailer_name": retailer_name,
                "quantity": quantity,
                "unit_price": server_unit_price,
                "subtotal": subtotal,
                "currency": order_currency,
            })

        total_amount = sum((it["subtotal"] for it in validated_items), Decimal("0.00"))

        # 4. Authoritative deterministic spending mandate revalidation
        mandate = None
        if request.mandate_id:
            mandate = db.query(SpendingMandate).filter_by(id=request.mandate_id).first()
        if not mandate:
            mandate = MandateRepository.get_mandate_by_session_id(db, session.id)

        budget_override_applied = False
        original_budget_amount = None
        budget_exceeded_amount = None

        if mandate:
            evaluation = SpendingPolicyEngine.evaluate(
                mandate=mandate,
                unit_price=float(max_item_unit_price),
                quantity=total_quantity,
                currency=order_currency,
            )
            if evaluation.decision == "BLOCKED":
                logger.warning("Checkout blocked by spending policy: %s", evaluation.reasons)
                raise MandatePolicyViolationError(reasons=evaluation.reasons)
            elif evaluation.decision == "WARNING_OVERRIDE_REQUIRED":
                if not request.budget_override_confirmed:
                    logger.warning("Checkout requires explicit budget override: %s", evaluation.reasons)
                    raise BudgetOverrideRequiredError(
                        reasons=evaluation.reasons,
                        exceeded_amount=evaluation.budget_exceeded_amount or 0.0,
                        currency=order_currency,
                        preferred_budget=mandate.total_budget,
                        order_total=float(total_amount),
                    )
                # Override is explicitly confirmed by user
                budget_override_applied = True
                original_budget_amount = Decimal(str(mandate.total_budget))
                budget_exceeded_amount = Decimal(str(evaluation.budget_exceeded_amount or 0.0))
                logger.info(
                    "User confirmed budget override: excess=%s %s on mandate budget=%s",
                    budget_exceeded_amount,
                    order_currency,
                    original_budget_amount,
                )

        # 5. Database Transaction 1: Persist Local PaymentOrder Draft
        paypal_req_id = generate_uuid()
        client_idemp_key = request.idempotency_key or generate_uuid()
        order_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        effective_user_id = user_id or session.user_id
        order = PaymentOrder(
            session_id=session.id,
            user_id=effective_user_id,
            mandate_id=mandate.id if mandate else None,
            paypal_request_id=paypal_req_id,
            idempotency_key=client_idemp_key,
            status=PaymentOrderStatus.CREATED.value,
            total_amount=total_amount,
            currency=order_currency,
            payment_provider="paypal_sandbox",
            order_type="SANDBOX_DEMO",
            budget_override_applied=budget_override_applied,
            original_budget_amount=original_budget_amount,
            budget_exceeded_amount=budget_exceeded_amount,
            expires_at=order_expires_at,
        )
        db.add(order)
        db.flush()

        for it in validated_items:
            db_item = OrderItem(
                payment_order_id=order.id,
                product_id=it["product_id"],
                retailer_offer_id=it["retailer_offer_id"],
                product_name=it["product_name"],
                retailer_name=it["retailer_name"],
                quantity=it["quantity"],
                unit_price=it["unit_price"],
                subtotal=it["subtotal"],
                currency=it["currency"],
            )
            db.add(db_item)

        db.commit()
        db.refresh(order)
        logger.info("Persisted local PaymentOrder draft: %s [Ref: %s]", order.id, order.internal_order_number)

        # 6. Outside DB Transaction: External PayPal Orders v2 Call
        default_return = "http://localhost:5500/?paypal_return=true"
        default_cancel = "http://localhost:5500/?paypal_cancel=true"
        resolved_return_url = (request.return_url or default_return).strip()
        resolved_cancel_url = (request.cancel_url or default_cancel).strip()

        try:
            pp_order_data = client.create_order(
                total_amount=total_amount,
                currency=order_currency,
                internal_order_number=order.internal_order_number,
                paypal_request_id=paypal_req_id,
                return_url=resolved_return_url,
                cancel_url=resolved_cancel_url,
                description=f"Order {order.internal_order_number} Demo Checkout",
            )
            paypal_order_id = pp_order_data["id"]
            approval_url = pp_order_data.get("_extracted_approval_url")
        except Exception as exc:
            logger.error("PayPal order creation failed for local order %s: %s", order.id, exc)
            order.status = PaymentOrderStatus.FAILED.value
            db.commit()
            raise

        # 7. Database Transaction 2: Update Order with PayPal Order ID
        order.paypal_order_id = paypal_order_id
        db.commit()
        db.refresh(order)

        response = PaymentOrderResponse.model_validate(order)
        response.approval_url = approval_url
        return response

    @staticmethod
    def capture_checkout_order(
        db: Session,
        order_id: str,
        session_id: str,
        paypal_client: PayPalClient | None = None,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderResponse:
        """
        Capture an approved PayPal order and verify settlement proof.
        Atomically establishes CAPTURE_PENDING, executes capture with stable PayPal-Request-Id,
        validates amounts, currencies, and records verified settlement.
        """
        client = paypal_client or PayPalClient()

        # 1. Fetch local order
        order = db.query(PaymentOrder).filter_by(id=order_id).first()
        if not order:
            raise InvalidOrderStateError(f"Payment order '{order_id}' was not found.")

        # 2. Verify user and session ownership
        if order.user_id:
            if not user_id or order.user_id != user_id:
                raise OrderOwnershipError("Order belongs to another account.")
        else:
            if order.session_id and order.session_id != session_id:
                raise OrderOwnershipError("Order does not belong to the active shopping session.")
            session = db.get(ShoppingSession, order.session_id) if order.session_id else None
            if session and session.user_id:
                raise OrderOwnershipError("Order belongs to an authenticated user account.")
            if session and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")

        # 3. Idempotent check: if already completed, return existing verified order
        if order.status == PaymentOrderStatus.COMPLETED.value:
            logger.info("Idempotent capture hit: order %s is already COMPLETED", order_id)
            return PaymentOrderResponse.model_validate(order)

        # 4. Verify order state is capturable
        if order.status in (PaymentOrderStatus.CANCELLED.value, PaymentOrderStatus.EXPIRED.value, PaymentOrderStatus.FAILED.value):
            raise InvalidOrderStateError(f"Cannot capture order in terminal '{order.status}' state.")

        if order.is_expired:
            logger.warning("Order %s expired prior to capture", order_id)
            order.status = PaymentOrderStatus.EXPIRED.value
            db.commit()
            raise OrderExpiredError(f"Order '{order.internal_order_number}' expired (15-minute validity window elapsed).")

        # 5. Verify authoritative PayPal order status if local order is CREATED
        if order.status == PaymentOrderStatus.CREATED.value:
            paypal_order = client.get_order_details(order.paypal_order_id)  # type: ignore[arg-type]
            paypal_status = (paypal_order.get("status") or "").upper()

            if paypal_status == "CREATED":
                logger.warning("Capture rejected: PayPal order %s has not yet been approved by buyer", order.paypal_order_id)
                raise OrderNotApprovedError(
                    f"PayPal order '{order.internal_order_number}' has not yet been approved by the buyer. "
                    "Please approve the order via the PayPal approval window before capturing payment."
                )
            elif paypal_status in ("VOIDED", "CANCELLED"):
                order.transition_to(PaymentOrderStatus.CANCELLED)
                db.commit()
                db.refresh(order)
                raise InvalidOrderStateError(
                    f"PayPal order '{order.internal_order_number}' was cancelled or voided by the buyer."
                )
            elif paypal_status in ("APPROVED", "COMPLETED"):
                order.transition_to(PaymentOrderStatus.APPROVED)
                db.commit()
                db.refresh(order)
            else:
                raise InvalidOrderStateError(
                    f"PayPal order '{order.internal_order_number}' has unexpected gateway status '{paypal_status}'."
                )

        # 6. Database Transaction 1: Atomically lock order in CAPTURE_PENDING
        # Allocate stable capture request ID if not yet assigned
        if not order.paypal_capture_request_id:
            order.paypal_capture_request_id = generate_uuid()

        order.transition_to(PaymentOrderStatus.CAPTURE_PENDING)
        db.commit()
        db.refresh(order)
        logger.info("Order %s locked in CAPTURE_PENDING [CaptureReqId: %s]", order.id, order.paypal_capture_request_id)

        # 7. Outside DB Transaction: Call PayPal Capture Endpoint
        capture_data = None
        try:
            capture_data = client.capture_order(
                paypal_order_id=order.paypal_order_id,  # type: ignore[arg-type]
                paypal_capture_request_id=order.paypal_capture_request_id,
            )
        except PayPalCaptureError as exc:
            if exc.issue == "ORDER_NOT_APPROVED":
                logger.warning("PayPal capture returned ORDER_NOT_APPROVED for order %s", order.id)
                order.status = PaymentOrderStatus.CREATED.value
                db.commit()
                raise OrderNotApprovedError(
                    f"PayPal order '{order.internal_order_number}' has not yet been approved by the buyer."
                ) from exc
            # If declined or failed, transition order to FAILED
            order.transition_to(PaymentOrderStatus.FAILED)
            db.commit()
            raise
        except PayPalTimeoutError as exc:
            logger.warning("Capture call timed out for order %s. Initiating reconciliation check...", order.id)
            # Reconcile status before failing
            try:
                order_details = client.get_order_details(order.paypal_order_id)  # type: ignore[arg-type]
                if order_details.get("status") == "COMPLETED":
                    logger.info("Reconciliation confirmed order %s completed on PayPal", order.id)
                    capture_data = order_details
                else:
                    raise CaptureReconciliationRequiredError(
                        "Capture request timed out. Gateway state is uncertain; manual reconciliation in progress."
                    ) from exc
            except Exception:
                raise CaptureReconciliationRequiredError(
                    "Capture request timed out. Gateway state is uncertain; manual reconciliation in progress."
                ) from exc

        # 7. Extract authoritative capture evidence
        captures_list = []
        purchase_units = capture_data.get("purchase_units", [])
        for pu in purchase_units:
            payments = pu.get("payments", {})
            captures_list.extend(payments.get("captures", []))

        if not captures_list:
            logger.error("PayPal capture response missing payments.captures array: %s", capture_data)
            raise PayPalCaptureError("PayPal capture response did not contain capture settlement details.")

        primary_capture = captures_list[0]
        paypal_capture_id = primary_capture.get("id")
        capture_status = primary_capture.get("status", "COMPLETED")
        captured_amount_str = primary_capture.get("amount", {}).get("value", "0.00")
        captured_currency = primary_capture.get("amount", {}).get("currency_code", "USD")
        captured_amount = Decimal(captured_amount_str)

        if not paypal_capture_id:
            raise PayPalCaptureError("PayPal capture record missing capture ID.")

        # Check duplicate capture ID
        existing_cap = db.query(PaymentCapture).filter_by(paypal_capture_id=paypal_capture_id).first()
        if existing_cap:
            raise DuplicateCaptureError(f"PayPal capture ID '{paypal_capture_id}' is already recorded.")

        # Extract buyer info (safely masked)
        payer = capture_data.get("payer", {})
        payer_id = payer.get("payer_id") or capture_data.get("payment_source", {}).get("paypal", {}).get("account_id")
        raw_email = payer.get("email_address") or capture_data.get("payment_source", {}).get("paypal", {}).get("email_address")
        masked_email_val = mask_email(raw_email)

        # 8. Database Transaction 2: Persist Verified Capture and Complete Order
        capture_record = PaymentCapture(
            payment_order_id=order.id,
            paypal_order_id=order.paypal_order_id,
            paypal_capture_id=paypal_capture_id,
            captured_amount=captured_amount,
            currency=captured_currency,
            capture_status=capture_status,
            is_verified=True,  # Established strictly by backend REST response
            paypal_payer_id=payer_id,
            paypal_payer_email_masked=masked_email_val,
            raw_response_summary={
                "id": paypal_capture_id,
                "status": capture_status,
                "create_time": primary_capture.get("create_time"),
            },
        )
        db.add(capture_record)

        # Non-bypassable verification boundary validates amounts, currencies, and status
        order.complete_with_verified_capture(capture_record)
        db.commit()
        db.refresh(order)
        logger.info("Order %s successfully COMPLETED with verified capture %s", order.id, paypal_capture_id)

        return PaymentOrderResponse.model_validate(order)

    @staticmethod
    def cancel_checkout_order(
        db: Session,
        order_id: str,
        session_id: str,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderResponse:
        """
        Cancel an uncaptured payment order explicitly upon buyer request or popup closure.
        Orders in CREATED or APPROVED states transition to CANCELLED.
        """
        order = db.query(PaymentOrder).filter_by(id=order_id).first()
        if not order:
            raise InvalidOrderStateError(f"Payment order '{order_id}' was not found.")

        if order.user_id:
            if not user_id or order.user_id != user_id:
                raise OrderOwnershipError("Order belongs to another account.")
        else:
            if order.session_id and order.session_id != session_id:
                raise OrderOwnershipError("Order does not belong to the active shopping session.")
            session = db.get(ShoppingSession, order.session_id) if order.session_id else None
            if session and session.user_id:
                raise OrderOwnershipError("Order belongs to an authenticated user account.")
            if session and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")

        if order.status == PaymentOrderStatus.COMPLETED.value:
            raise InvalidOrderStateError("Cannot cancel an already completed and settled payment order.")

        if order.status in (PaymentOrderStatus.CANCELLED.value, PaymentOrderStatus.EXPIRED.value):
            return PaymentOrderResponse.model_validate(order)

        if order.status == PaymentOrderStatus.CAPTURE_PENDING.value:
            raise InvalidOrderStateError("Cannot cancel an order while payment capture is actively in flight.")

        order.transition_to(PaymentOrderStatus.CANCELLED)
        db.commit()
        db.refresh(order)
        logger.info("Order %s [%s] successfully CANCELLED", order.id, order.internal_order_number)
        return PaymentOrderResponse.model_validate(order)

    @staticmethod
    def get_order_status(
        db: Session,
        order_id: str,
        session_id: str,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderResponse:
        """Retrieve authoritative status and item snapshots for a payment order."""
        order = db.query(PaymentOrder).filter_by(id=order_id).first()
        if not order:
            raise InvalidOrderStateError(f"Payment order '{order_id}' was not found.")
        if order.session_id and order.session_id != session_id:
            raise OrderOwnershipError("Order does not belong to the active shopping session.")
        if order.user_id:
            if not user_id or order.user_id != user_id:
                raise OrderOwnershipError("Order belongs to another account.")
        else:
            session = db.get(ShoppingSession, order.session_id) if order.session_id else None
            if session and session.user_id:
                raise OrderOwnershipError("Order belongs to an authenticated user account.")
            if session and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")
        return PaymentOrderResponse.model_validate(order)

    @staticmethod
    def get_order_by_paypal_id(
        db: Session,
        paypal_order_id: str,
        session_id: str,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderResponse:
        """Retrieve authoritative status and item snapshots for a payment order by PayPal order ID token."""
        order = db.query(PaymentOrder).filter_by(paypal_order_id=paypal_order_id).first()
        if not order:
            raise InvalidOrderStateError(f"Payment order for PayPal token '{paypal_order_id}' was not found.")
        if order.session_id and order.session_id != session_id:
            raise OrderOwnershipError("Order does not belong to the active shopping session.")
        if order.user_id:
            if not user_id or order.user_id != user_id:
                raise OrderOwnershipError("Order belongs to another account.")
        else:
            session = db.get(ShoppingSession, order.session_id) if order.session_id else None
            if session and session.user_id:
                raise OrderOwnershipError("Order belongs to an authenticated user account.")
            if session and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")
        return PaymentOrderResponse.model_validate(order)

    @staticmethod
    def get_session_payment_history(
        db: Session,
        session_id: str,
        page: int = 1,
        page_size: int = 20,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PaymentOrderListResponse:
        """Retrieve paginated payment history for a guest or authenticated shopping session."""
        clean_sid = session_id.strip() if session_id else ""
        session = None
        if clean_sid and clean_sid != "me":
            session = (
                db.query(ShoppingSession)
                .filter(
                    (ShoppingSession.id == clean_sid) | (ShoppingSession.session_identifier == clean_sid)
                )
                .first()
            )
        if not session and user_id:
            session = (
                db.query(ShoppingSession)
                .filter(ShoppingSession.user_id == user_id)
                .order_by(ShoppingSession.updated_at.desc())
                .first()
            )

        if session:
            if session.user_id:
                if not user_id or session.user_id != user_id:
                    raise OrderOwnershipError("Shopping session belongs to another account.")
            elif not user_id and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")

        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        target_sid = session.id if session else clean_sid
        query = db.query(PaymentOrder)
        if user_id:
            query = query.filter(
                (PaymentOrder.user_id == user_id) | (PaymentOrder.session_id == target_sid)
            )
        else:
            query = query.filter(
                PaymentOrder.session_id == target_sid,
                PaymentOrder.user_id.is_(None),
            )
        total = query.count()
        orders = (
            query.order_by(PaymentOrder.created_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

        history_items = []
        for ord_item in orders:
            primary_cap_id = ord_item.captures[0].paypal_capture_id if ord_item.captures else None
            captured_at = ord_item.captures[0].captured_at if ord_item.captures else None
            history_items.append(
                PaymentHistoryItemResponse(
                    id=ord_item.id,
                    internal_order_number=ord_item.internal_order_number,
                    status=ord_item.status,
                    total_amount=ord_item.total_amount,
                    currency=ord_item.currency,
                    item_count=len(ord_item.items),
                    paypal_order_id=ord_item.paypal_order_id,
                    paypal_capture_id=primary_cap_id,
                    created_at=ord_item.created_at,
                    captured_at=captured_at,
                )
            )

        return PaymentOrderListResponse(
            orders=history_items,
            total=total,
            session_id=session_id,
        )

    @staticmethod
    def get_session_analytics(
        db: Session,
        session_id: str,
        user_id: str | None = None,
        guest_session_token: str | None = None,
    ) -> PersonalAnalyticsResponse:
        """
        Calculate authoritative personal shopping analytics for a guest or authenticated shopping session.
        Enforces strict constraints:
          - Only COMPLETED orders with verified COMPLETED PayPal captures are included.
          - Multi-currency segregation: totals are NEVER combined across currencies.
          - Avoids Cartesian join inflation by separating order/capture financial totals from item joins.
          - Explains differences between captured amounts and order totals if any exist.
          - Evaluates active mandate budget utilization if configured.
          - Scoped strictly to the requested session.
        """
        if not session_id or not session_id.strip():
            return PersonalAnalyticsResponse(
                session_id="",
                session_identifier=None,
                completed_order_count=0,
                total_spending_by_currency=[],
                average_order_value_by_currency={},
                monthly_spending_trends=[],
                category_spending=[],
                retailer_spending=[],
                active_mandate_utilization=None,
            )

        clean_sid = session_id.strip() if session_id else ""
        # 1. Resolve session by ID or identifier
        session = None
        if clean_sid and clean_sid != "me":
            session = (
                db.query(ShoppingSession)
                .filter(
                    (ShoppingSession.id == clean_sid) | (ShoppingSession.session_identifier == clean_sid)
                )
                .first()
            )
        if not session and user_id:
            # Fall back to authenticated user's primary linked session
            session = (
                db.query(ShoppingSession)
                .filter(ShoppingSession.user_id == user_id)
                .order_by(ShoppingSession.updated_at.desc())
                .first()
            )

        if session:
            if session.user_id:
                if not user_id or session.user_id != user_id:
                    raise OrderOwnershipError("Shopping session belongs to another account.")
            elif not user_id and session.session_token:
                if not guest_session_token:
                    raise MissingGuestProofError("Missing guest session proof of possession.")
                if session.session_token != guest_session_token:
                    raise InvalidGuestProofError("Invalid guest session proof of possession.")

        canonical_session_id = session.id if session else clean_sid
        session_identifier = session.session_identifier if session else None

        if not session:
            return PersonalAnalyticsResponse(
                session_id=canonical_session_id,
                session_identifier=session_identifier,
                completed_order_count=0,
                total_spending_by_currency=[],
                average_order_value_by_currency={},
                monthly_spending_trends=[],
                category_spending=[],
                retailer_spending=[],
                active_mandate_utilization=None,
            )

        # 2. Query completed orders
        orders_query = db.query(PaymentOrder).filter(
            PaymentOrder.status == PaymentOrderStatus.COMPLETED.value
        )
        if user_id:
            orders_query = orders_query.filter(
                (PaymentOrder.user_id == user_id) | (PaymentOrder.session_id == session.id)
            )
        else:
            orders_query = orders_query.filter(
                PaymentOrder.session_id == session.id,
                PaymentOrder.user_id.is_(None),
            )
        completed_orders = orders_query.order_by(PaymentOrder.created_at.asc()).all()

        # 3. Filter for orders that have verified completed captures
        # Also map order_id -> primary verified capture to prevent any duplicate-capture inflation
        valid_completed_orders: list[PaymentOrder] = []
        order_verified_captures: dict[str, PaymentCapture] = {}

        for order in completed_orders:
            verified_cap = next(
                (c for c in order.captures if c.is_verified and c.capture_status == "COMPLETED"),
                None,
            )
            if verified_cap is not None:
                valid_completed_orders.append(order)
                order_verified_captures[order.id] = verified_cap
            else:
                logger.warning(
                    "Order %s marked COMPLETED lacks verified completed PayPal capture; excluded from analytics",
                    order.id,
                )

        completed_order_count = len(valid_completed_orders)
        active_mandate = db.query(SpendingMandate).filter_by(session_id=session.id).first()

        if completed_order_count == 0:
            mandate_utilization = None
            if active_mandate:
                mandate_budget = Decimal(str(active_mandate.total_budget))
                mandate_utilization = MandateBudgetUtilization(
                    mandate_id=active_mandate.id,
                    currency=(active_mandate.currency or "USD").upper(),
                    total_budget=mandate_budget,
                    spent_amount=Decimal("0.00"),
                    remaining_budget=mandate_budget,
                    utilization_percentage=0.0,
                    maximum_item_price=Decimal(str(active_mandate.maximum_item_price)),
                    maximum_quantity=active_mandate.maximum_quantity,
                )

            return PersonalAnalyticsResponse(
                session_id=canonical_session_id,
                session_identifier=session_identifier,
                completed_order_count=0,
                total_spending_by_currency=[],
                average_order_value_by_currency={},
                monthly_spending_trends=[],
                category_spending=[],
                retailer_spending=[],
                active_mandate_utilization=mandate_utilization,
            )

        # 4. Group valid orders by currency
        orders_by_currency: dict[str, list[PaymentOrder]] = {}
        for o in valid_completed_orders:
            curr = (o.currency or "USD").strip().upper()
            orders_by_currency.setdefault(curr, []).append(o)

        currency_summaries: list[CurrencySpendingSummary] = []
        aov_by_currency: dict[str, Decimal] = {}
        currency_settled_totals: dict[str, Decimal] = {}

        for curr, curr_orders in sorted(orders_by_currency.items()):
            order_count = len(curr_orders)
            total_spent = sum(
                (order_verified_captures[o.id].captured_amount for o in curr_orders),
                Decimal("0.00"),
            )
            total_order_amount = sum(
                (o.total_amount for o in curr_orders),
                Decimal("0.00"),
            )
            aov = (total_spent / order_count).quantize(Decimal("0.01")) if order_count > 0 else Decimal("0.00")

            discrepancy = None
            if total_spent != total_order_amount:
                discrepancy = (
                    f"Settled capture total ({curr} {total_spent}) differs from "
                    f"recorded order total ({curr} {total_order_amount})."
                )
            else:
                discrepancy = "Settled capture amounts match order totals exactly."

            currency_summaries.append(
                CurrencySpendingSummary(
                    currency=curr,
                    total_spent=total_spent,
                    total_order_amount=total_order_amount,
                    order_count=order_count,
                    average_order_value=aov,
                    discrepancy_explanation=discrepancy,
                )
            )
            aov_by_currency[curr] = aov
            currency_settled_totals[curr] = total_spent

        # 5. Monthly spending trends (grouped by month and currency)
        monthly_map: dict[tuple[str, str], list[Decimal]] = {}
        for o in valid_completed_orders:
            cap = order_verified_captures[o.id]
            curr = (o.currency or "USD").strip().upper()
            ts = cap.captured_at or o.created_at
            month_str = ts.strftime("%Y-%m")
            monthly_map.setdefault((month_str, curr), []).append(cap.captured_amount)

        monthly_trends: list[MonthlySpendingTrend] = []
        for (m_str, curr), amounts in sorted(monthly_map.items()):
            month_total = sum(amounts, Decimal("0.00"))
            monthly_trends.append(
                MonthlySpendingTrend(
                    month=m_str,
                    currency=curr,
                    total_amount=month_total,
                    order_count=len(amounts),
                )
            )

        # 6. Category-wise and Retailer-wise spending (queried from order items for valid completed orders)
        valid_order_ids = [o.id for o in valid_completed_orders]
        items_with_category = (
            db.query(OrderItem, Product.category)
            .outerjoin(Product, OrderItem.product_id == Product.id)
            .filter(OrderItem.payment_order_id.in_(valid_order_ids))
            .all()
        )

        category_map: dict[tuple[str, str], dict[str, Any]] = {}
        retailer_map: dict[tuple[str, str], dict[str, Any]] = {}

        for item, category_name in items_with_category:
            curr = (item.currency or "USD").strip().upper()
            cat = category_name or "Uncategorized"
            ret = item.retailer_name or "Direct"
            subtotal = Decimal(str(item.subtotal))
            qty = item.quantity or 1

            # Category aggregation
            cat_key = (cat, curr)
            if cat_key not in category_map:
                category_map[cat_key] = {"total": Decimal("0.00"), "count": 0}
            category_map[cat_key]["total"] += subtotal
            category_map[cat_key]["count"] += qty

            # Retailer aggregation
            ret_key = (ret, curr)
            if ret_key not in retailer_map:
                retailer_map[ret_key] = {"total": Decimal("0.00"), "count": 0}
            retailer_map[ret_key]["total"] += subtotal
            retailer_map[ret_key]["count"] += qty

        category_spending: list[CategorySpending] = []
        for (cat, curr), data in sorted(category_map.items(), key=lambda x: x[1]["total"], reverse=True):
            curr_total = currency_settled_totals.get(curr, Decimal("0.00"))
            pct = float((data["total"] / curr_total) * 100) if curr_total > 0 else 0.0
            category_spending.append(
                CategorySpending(
                    category=cat,
                    currency=curr,
                    total_amount=data["total"],
                    item_count=data["count"],
                    percentage_of_currency_total=round(min(100.0, pct), 1),
                )
            )

        retailer_spending: list[RetailerSpending] = []
        for (ret, curr), data in sorted(retailer_map.items(), key=lambda x: x[1]["total"], reverse=True):
            curr_total = currency_settled_totals.get(curr, Decimal("0.00"))
            pct = float((data["total"] / curr_total) * 100) if curr_total > 0 else 0.0
            retailer_spending.append(
                RetailerSpending(
                    retailer_name=ret,
                    currency=curr,
                    total_amount=data["total"],
                    item_count=data["count"],
                    percentage_of_currency_total=round(min(100.0, pct), 1),
                )
            )

        # 7. Active Mandate Budget Utilization
        mandate_utilization = None
        if active_mandate:
            mandate_curr = (active_mandate.currency or "USD").strip().upper()
            mandate_budget = Decimal(str(active_mandate.total_budget))
            mandate_spent = currency_settled_totals.get(mandate_curr, Decimal("0.00"))
            remaining = max(Decimal("0.00"), mandate_budget - mandate_spent)
            pct = float((mandate_spent / mandate_budget) * 100) if mandate_budget > 0 else 0.0
            mandate_utilization = MandateBudgetUtilization(
                mandate_id=active_mandate.id,
                currency=mandate_curr,
                total_budget=mandate_budget,
                spent_amount=mandate_spent,
                remaining_budget=remaining,
                utilization_percentage=round(min(100.0, pct), 1),
                maximum_item_price=Decimal(str(active_mandate.maximum_item_price)),
                maximum_quantity=active_mandate.maximum_quantity,
            )

        return PersonalAnalyticsResponse(
            session_id=canonical_session_id,
            session_identifier=session_identifier,
            completed_order_count=completed_order_count,
            total_spending_by_currency=currency_summaries,
            average_order_value_by_currency=aov_by_currency,
            monthly_spending_trends=monthly_trends,
            category_spending=category_spending,
            retailer_spending=retailer_spending,
            active_mandate_utilization=mandate_utilization,
        )


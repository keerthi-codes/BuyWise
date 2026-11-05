"""
AI-Powered Multilingual Shopping Intelligence Platform
SQLAlchemy 2.0 Payment & Checkout Domain Models.
Supports Payment Orders, Order Item Snapshots, and Verified PayPal Captures.
Enforces strict decimal precision, non-bypassable status transitions, and idempotency constraints.
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import uuid
from typing import Any
from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


def generate_uuid() -> str:
    """Generate a UUID v4 string identifier."""
    return str(uuid.uuid4())


def generate_order_number() -> str:
    """
    Generate an authoritative, human-friendly order identifier.
    Format: ORD-YYYYMMDD-<8-char-hex>
    Example: ORD-20261102-8F3A29B1
    """
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:8].upper()
    return f"ORD-{today_str}-{short_uuid}"


class PaymentOrderStatus(str, Enum):
    """
    Authoritative lifecycle states for payment orders.
    Transitions are strictly enforced via can_transition_to and transition_to.
    """
    CREATED = "CREATED"                    # Order created in backend / PayPal order pending
    APPROVED = "APPROVED"                  # Buyer authorized payment via PayPal
    CAPTURE_PENDING = "CAPTURE_PENDING"    # Capture initiated; locked against duplicate attempts
    COMPLETED = "COMPLETED"                # Payment capture verified and settled
    FAILED = "FAILED"                      # Authorization or capture failed
    CANCELLED = "CANCELLED"                # Buyer or operator cancelled before capture
    EXPIRED = "EXPIRED"                    # Order 15-minute validity window expired


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal order status transition is attempted."""
    pass


class PaymentVerificationError(ValueError):
    """Raised when payment completion verification fails."""
    pass


class CaptureAmountMismatchError(PaymentVerificationError):
    """Raised when captured amount does not match the payment order total."""
    pass


class CaptureCurrencyMismatchError(PaymentVerificationError):
    """Raised when captured currency does not match the payment order currency."""
    pass


class UnverifiedCaptureError(PaymentVerificationError):
    """Raised when attempting completion with an unverified capture."""
    pass


# Authoritative state transition graph
VALID_ORDER_TRANSITIONS: dict[PaymentOrderStatus, set[PaymentOrderStatus]] = {
    PaymentOrderStatus.CREATED: {
        PaymentOrderStatus.APPROVED,
        PaymentOrderStatus.FAILED,
        PaymentOrderStatus.CANCELLED,
        PaymentOrderStatus.EXPIRED,
    },
    PaymentOrderStatus.APPROVED: {
        PaymentOrderStatus.CAPTURE_PENDING,
        PaymentOrderStatus.CANCELLED,
        PaymentOrderStatus.EXPIRED,
    },
    PaymentOrderStatus.CAPTURE_PENDING: {
        PaymentOrderStatus.COMPLETED,
        PaymentOrderStatus.FAILED,
    },
    PaymentOrderStatus.COMPLETED: set(),  # Terminal state
    PaymentOrderStatus.FAILED: set(),     # Terminal state
    PaymentOrderStatus.CANCELLED: set(),  # Terminal state
    PaymentOrderStatus.EXPIRED: set(),    # Terminal state
}


class PaymentOrder(Base):
    """
    Authoritative internal order ledger governing a checkout attempt.
    Supports both mandate-backed checkouts and standard baseline checkouts.
    Enforces session-scoped idempotency, 15-minute expiration, and strict state transitions.
    Preserves financial audit records upon shopping session deletion (ON DELETE SET NULL).
    """

    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_payment_orders_session_idempotency"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    internal_order_number = Column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        default=generate_order_number,
    )
    session_id = Column(
        String(64),
        ForeignKey("shopping_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mandate_id = Column(
        String(64),
        ForeignKey("spending_mandates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    paypal_order_id = Column(
        String(128),
        nullable=True,
        unique=True,
        index=True,
    )
    paypal_request_id = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        default=generate_uuid,
    )
    paypal_capture_request_id = Column(
        String(64),
        nullable=True,
        unique=True,
        index=True,
    )
    idempotency_key = Column(
        String(128),
        nullable=False,
        index=True,
        default=generate_uuid,
    )
    status = Column(
        String(32),
        nullable=False,
        default=PaymentOrderStatus.CREATED.value,
        index=True,
    )
    total_amount = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=False,
    )
    currency = Column(
        String(3),
        nullable=False,
        default="USD",
    )
    payment_provider = Column(
        String(32),
        nullable=False,
        default="paypal_sandbox",
    )
    order_type = Column(
        String(32),
        nullable=False,
        default="SANDBOX_DEMO",
    )
    budget_override_applied = Column(
        Boolean,
        nullable=False,
        default=False,
    )
    original_budget_amount = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=True,
    )
    budget_exceeded_amount = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=True,
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship("User", back_populates="orders")
    session = relationship("ShoppingSession", back_populates="orders")
    mandate = relationship("SpendingMandate", back_populates="orders")
    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="joined",
    )
    captures = relationship(
        "PaymentCapture",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __init__(self, **kwargs: Any) -> None:
        if "id" not in kwargs:
            kwargs["id"] = generate_uuid()
        if "internal_order_number" not in kwargs:
            kwargs["internal_order_number"] = generate_order_number()
        if "status" not in kwargs:
            kwargs["status"] = PaymentOrderStatus.CREATED.value
        if "paypal_request_id" not in kwargs:
            kwargs["paypal_request_id"] = generate_uuid()
        if "idempotency_key" not in kwargs:
            kwargs["idempotency_key"] = generate_uuid()
        if "currency" not in kwargs:
            kwargs["currency"] = "USD"
        if "payment_provider" not in kwargs:
            kwargs["payment_provider"] = "paypal_sandbox"
        if "order_type" not in kwargs:
            kwargs["order_type"] = "SANDBOX_DEMO"
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(timezone.utc)
        if "updated_at" not in kwargs:
            kwargs["updated_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)

    def can_transition_to(self, target_status: PaymentOrderStatus | str) -> bool:
        """Check if transitioning to target_status is permissible under lifecycle rules."""
        target = PaymentOrderStatus(target_status) if isinstance(target_status, str) else target_status
        current_status = self.status or PaymentOrderStatus.CREATED.value
        current = PaymentOrderStatus(current_status)
        return target in VALID_ORDER_TRANSITIONS.get(current, set())

    def complete_with_verified_capture(self, capture: "PaymentCapture") -> None:
        """
        Authoritatively transition the order from CAPTURE_PENDING to COMPLETED
        using an independently verified PayPal capture record.

        Strict Financial Verification Invariants:
        1. Order must currently be in CAPTURE_PENDING state.
        2. Capture must have is_verified == True (verified against PayPal REST API).
        3. Capture amount must exactly match order total_amount.
        4. Capture currency must match order currency.
        5. Capture status must be 'COMPLETED'.
        6. Capture must reference this order.
        """
        current_status = self.status or PaymentOrderStatus.CREATED.value
        if current_status != PaymentOrderStatus.CAPTURE_PENDING.value:
            raise InvalidStateTransitionError(
                f"Cannot complete payment order from status '{current_status}'. "
                f"Order must be in '{PaymentOrderStatus.CAPTURE_PENDING.value}' before completion."
            )

        if not getattr(capture, "is_verified", False):
            raise UnverifiedCaptureError(
                f"Cannot complete payment order '{self.id}': capture '{capture.paypal_capture_id}' "
                f"has not been independently verified via PayPal API."
            )

        if capture.captured_amount != self.total_amount:
            raise CaptureAmountMismatchError(
                f"Capture amount mismatch for order '{self.id}': "
                f"order requires {self.currency} {self.total_amount}, "
                f"but capture settled {capture.currency} {capture.captured_amount}."
            )

        if capture.currency.upper() != self.currency.upper():
            raise CaptureCurrencyMismatchError(
                f"Capture currency mismatch for order '{self.id}': "
                f"order currency is '{self.currency}', but capture currency is '{capture.currency}'."
            )

        if capture.capture_status != "COMPLETED":
            raise PaymentVerificationError(
                f"Cannot complete payment order: capture status is '{capture.capture_status}', expected 'COMPLETED'."
            )

        if capture.payment_order_id and capture.payment_order_id != self.id:
            raise PaymentVerificationError(
                f"Capture '{capture.id}' belongs to order '{capture.payment_order_id}', not '{self.id}'."
            )

        if not capture.payment_order_id:
            capture.payment_order_id = self.id
        if capture not in self.captures:
            self.captures.append(capture)

        self.status = PaymentOrderStatus.COMPLETED.value
        self.updated_at = datetime.now(timezone.utc)

    def transition_to(
        self,
        target_status: PaymentOrderStatus | str,
        verified_capture: Any = None,
    ) -> None:
        """
        Transition order to target_status.
        Raises InvalidStateTransitionError if the transition violates the lifecycle graph.
        Transitioning to COMPLETED strictly requires a verified capture via complete_with_verified_capture.
        """
        target = PaymentOrderStatus(target_status) if isinstance(target_status, str) else target_status
        current_status = self.status or PaymentOrderStatus.CREATED.value
        current = PaymentOrderStatus(current_status)

        if not self.can_transition_to(target):
            raise InvalidStateTransitionError(
                f"Cannot transition PaymentOrder from '{current.value}' to '{target.value}'. "
                f"Permitted transitions from '{current.value}': "
                f"{sorted([s.value for s in VALID_ORDER_TRANSITIONS.get(current, set())])}"
            )

        if target == PaymentOrderStatus.COMPLETED:
            if verified_capture is None:
                raise InvalidStateTransitionError(
                    "Direct transition to 'COMPLETED' is prohibited. "
                    "Payment completion requires verified backend capture proof via complete_with_verified_capture(capture)."
                )
            self.complete_with_verified_capture(verified_capture)
            return

        self.status = target.value
        self.updated_at = datetime.now(timezone.utc)

    @property
    def is_expired(self) -> bool:
        """Check whether order has passed its 15-minute validity window."""
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return self.expires_at < now.replace(tzinfo=None)
        return self.expires_at < now


class OrderItem(Base):
    """
    Immutable product snapshot associated with a payment order.
    Preserves historical product name, retailer offer price, quantity, and subtotal
    so future catalog changes or deletions never alter historical purchase records.
    """

    __tablename__ = "order_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    payment_order_id = Column(
        String(36),
        ForeignKey("payment_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id = Column(
        String(64),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    retailer_offer_id = Column(
        String(64),
        ForeignKey("retailer_offers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_name = Column(
        String(255),
        nullable=False,
    )
    retailer_name = Column(
        String(128),
        nullable=True,
    )
    quantity = Column(
        Integer,
        nullable=False,
        default=1,
    )
    unit_price = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=False,
    )
    subtotal = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=False,
    )
    currency = Column(
        String(3),
        nullable=False,
        default="USD",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    order = relationship("PaymentOrder", back_populates="items")
    product = relationship("Product")
    offer = relationship("RetailerOffer")

    def __init__(self, **kwargs: Any) -> None:
        if "id" not in kwargs:
            kwargs["id"] = generate_uuid()
        if "quantity" not in kwargs:
            kwargs["quantity"] = 1
        if "currency" not in kwargs:
            kwargs["currency"] = "USD"
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)


class PaymentCapture(Base):
    """
    Authoritative settlement record of a verified PayPal Sandbox capture.
    Maintains cryptographic settlement proof, masked payer information, and audit metadata.
    """

    __tablename__ = "payment_captures"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    payment_order_id = Column(
        String(36),
        ForeignKey("payment_orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    paypal_order_id = Column(
        String(128),
        nullable=False,
        index=True,
    )
    paypal_capture_id = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )
    captured_amount = Column(
        Numeric(12, 2, asdecimal=True),
        nullable=False,
    )
    currency = Column(
        String(3),
        nullable=False,
        default="USD",
    )
    capture_status = Column(
        String(32),
        nullable=False,
        default="COMPLETED",
        index=True,
    )
    is_verified = Column(
        Boolean,
        nullable=False,
        default=False,
    )
    paypal_payer_id = Column(
        String(128),
        nullable=True,
    )
    paypal_payer_email_masked = Column(
        String(255),
        nullable=True,
    )
    raw_response_summary = Column(
        JSON,
        nullable=False,
        default=dict,
    )
    captured_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    order = relationship("PaymentOrder", back_populates="captures")

    def __init__(self, **kwargs: Any) -> None:
        if "id" not in kwargs:
            kwargs["id"] = generate_uuid()
        if "capture_status" not in kwargs:
            kwargs["capture_status"] = "COMPLETED"
        if "is_verified" not in kwargs:
            kwargs["is_verified"] = False
        if "currency" not in kwargs:
            kwargs["currency"] = "USD"
        if "raw_response_summary" not in kwargs:
            kwargs["raw_response_summary"] = {}
        if "captured_at" not in kwargs:
            kwargs["captured_at"] = datetime.now(timezone.utc)
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(timezone.utc)
        super().__init__(**kwargs)

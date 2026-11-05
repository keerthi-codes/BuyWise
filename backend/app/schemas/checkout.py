"""
AI-Powered Multilingual Shopping Intelligence Platform
Pydantic v2 Schemas for Checkout, Orders, and Payment Captures.
Enforces strict Decimal precision for financial amounts, UTC timestamps,
and data minimization (no card details, CVVs, or unmasked secrets).
"""

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


# -------------------------------------------------------------------------
# Order Item Schemas
# -------------------------------------------------------------------------

class OrderItemBase(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=255, description="Historical product name snapshot")
    quantity: int = Field(default=1, ge=1, description="Quantity of units ordered")
    unit_price: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2, description="Price per unit in transaction currency")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO-4217 currency code")
    product_id: str | None = Field(default=None, description="Optional catalog Product UUID reference")
    retailer_offer_id: str | None = Field(default=None, description="Optional RetailerOffer UUID reference")
    retailer_name: str | None = Field(default=None, max_length=128, description="Retailer merchant name snapshot")


class OrderItemCreate(OrderItemBase):
    """Payload to create an individual order item."""
    pass


class OrderItemResponse(OrderItemBase):
    """Detailed response schema for an order item."""
    id: str = Field(..., description="OrderItem UUID")
    payment_order_id: str = Field(..., description="Parent PaymentOrder UUID")
    subtotal: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2, description="Calculated subtotal (unit_price * quantity)")
    created_at: datetime = Field(..., description="Creation UTC timestamp")

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------------------------
# Payment Capture Schemas
# -------------------------------------------------------------------------

class PaymentCaptureResponse(BaseModel):
    """Response schema for a verified PayPal capture settlement."""
    id: str = Field(..., description="Capture UUID")
    payment_order_id: str = Field(..., description="Parent PaymentOrder UUID")
    paypal_order_id: str = Field(..., description="PayPal Order reference ID")
    paypal_capture_id: str = Field(..., description="PayPal financial capture ID")
    captured_amount: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2, description="Captured amount in settlement currency")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 settlement currency")
    capture_status: str = Field(..., description="PayPal capture status (e.g. COMPLETED)")
    is_verified: bool = Field(..., description="Whether capture has been independently verified via PayPal REST API")
    paypal_payer_id: str | None = Field(default=None, description="Anonymized PayPal buyer ID")
    paypal_payer_email_masked: str | None = Field(default=None, description="Masked buyer email (e.g. b***r@example.com)")
    captured_at: datetime = Field(..., description="UTC capture timestamp")

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------------------------
# Payment Order Schemas
# -------------------------------------------------------------------------

class CheckoutOrderCreate(BaseModel):
    """Payload to initiate a checkout attempt."""
    session_id: str | None = Field(default=None, description="Optional existing ShoppingSession UUID")
    session_identifier: str | None = Field(default=None, description="Guest session cookie/token if session_id is omitted")
    session_token: str | None = Field(default=None, description="Proof-of-possession token for guest shopping session")
    mandate_id: str | None = Field(default=None, description="Optional SpendingMandate UUID to govern purchase")
    items: list[OrderItemCreate] = Field(..., min_length=1, description="List of items to purchase")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO-4217 currency code")
    idempotency_key: str | None = Field(default=None, description="Client idempotency key to prevent duplicate checkouts")
    order_type: str = Field(default="SANDBOX_DEMO", description="Order track: SANDBOX_DEMO or EXTERNAL_TRACKED")
    budget_override_confirmed: bool = Field(
        default=False,
        description="Explicit user consent to exceed preferred budget ceiling",
    )
    acknowledged_budget_excess: Decimal | None = Field(
        default=None,
        description="The budget excess acknowledged by the user",
    )
    return_url: str | None = Field(
        default=None,
        description="Frontend callback URL to return to after PayPal buyer approval",
    )
    cancel_url: str | None = Field(
        default=None,
        description="Frontend callback URL to return to if buyer cancels checkout on PayPal",
    )


class PaymentOrderResponse(BaseModel):
    """Full detail response schema for a payment order."""
    id: str = Field(..., description="Internal PaymentOrder UUID")
    internal_order_number: str = Field(..., description="Human-friendly order code (e.g. ORD-20261102-XXXX)")
    session_id: str | None = Field(default=None, description="Bound ShoppingSession UUID (or None if session was purged)")
    mandate_id: str | None = Field(default=None, description="Bound SpendingMandate UUID, if mandate-backed")
    paypal_order_id: str | None = Field(default=None, description="PayPal Order ID returned by PayPal API")
    paypal_request_id: str = Field(..., description="Unique UUID header used for PayPal-Request-Id idempotency")
    paypal_capture_request_id: str | None = Field(default=None, description="Unique UUID header used for PayPal capture idempotency")
    idempotency_key: str = Field(..., description="Internal idempotency key")
    status: str = Field(..., description="Lifecycle status: CREATED, APPROVED, CAPTURE_PENDING, COMPLETED, FAILED, CANCELLED, EXPIRED")
    total_amount: Decimal = Field(..., gt=Decimal("0.00"), decimal_places=2, description="Total order amount")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 currency code")
    payment_provider: str = Field(..., description="Payment provider identifier")
    order_type: str = Field(..., description="Order track category")
    expires_at: datetime = Field(..., description="UTC expiration deadline (15-minute validity window)")
    created_at: datetime = Field(..., description="Creation UTC timestamp")
    updated_at: datetime = Field(..., description="Last update UTC timestamp")
    items: list[OrderItemResponse] = Field(default_factory=list, description="Ordered item snapshots")
    captures: list[PaymentCaptureResponse] = Field(default_factory=list, description="Settled capture records")
    approval_url: str | None = Field(default=None, description="PayPal buyer approval link for checkout popup/redirect")
    budget_override_applied: bool = Field(
        default=False,
        description="Whether this order required and received user budget override confirmation",
    )
    original_budget_amount: Decimal | None = Field(
        default=None,
        description="Original preferred spending mandate budget if override was applied",
    )
    budget_exceeded_amount: Decimal | None = Field(
        default=None,
        description="Amount by which order exceeded original preferred budget if override was applied",
    )

    model_config = ConfigDict(from_attributes=True)


class PaymentHistoryItemResponse(BaseModel):
    """Summary representation for shopping order history lists."""
    id: str = Field(..., description="PaymentOrder UUID")
    internal_order_number: str = Field(..., description="Human-friendly order number")
    status: str = Field(..., description="Order status")
    total_amount: Decimal = Field(..., decimal_places=2, description="Total purchase amount")
    currency: str = Field(..., description="ISO-4217 currency code")
    item_count: int = Field(..., description="Number of items in the order")
    paypal_order_id: str | None = Field(default=None, description="PayPal Order ID")
    paypal_capture_id: str | None = Field(default=None, description="PayPal Capture ID if settled")
    created_at: datetime = Field(..., description="Order creation timestamp")
    captured_at: datetime | None = Field(default=None, description="Settlement timestamp if completed")

    model_config = ConfigDict(from_attributes=True)


class PaymentOrderListResponse(BaseModel):
    """Paginated list of orders for a session."""
    orders: list[PaymentHistoryItemResponse] = Field(default_factory=list, description="List of historical orders")
    total: int = Field(default=0, ge=0, description="Total number of orders")
    session_id: str = Field(..., description="Target ShoppingSession UUID")


class RetailerRedirectResponse(BaseModel):
    """Returned when a user attempts to checkout an external Channel3 marketplace offer."""
    requires_external_checkout: bool = Field(default=True, description="Indicates this offer is fulfilled directly by the external merchant")
    retailer_name: str = Field(..., description="External retailer or marketplace name")
    product_url: str = Field(..., description="Direct outbound URL to complete purchase on retailer's website")
    message: str = Field(..., description="Notice explaining that purchase must be completed on the retailer's website")
    product_name: str = Field(..., description="Product title")
    price: Decimal = Field(..., description="Offer price")
    currency: str = Field(..., description="Offer currency")

# -------------------------------------------------------------------------
# Personal Shopping Analytics Schemas
# -------------------------------------------------------------------------

class CurrencySpendingSummary(BaseModel):
    """Settled financial spending summary for a single currency."""
    currency: str = Field(..., description="ISO-4217 currency code")
    total_spent: Decimal = Field(..., decimal_places=2, description="Total settled amount from verified captures")
    total_order_amount: Decimal = Field(..., decimal_places=2, description="Total recorded order amount")
    order_count: int = Field(..., ge=0, description="Number of completed orders settled in this currency")
    average_order_value: Decimal = Field(..., decimal_places=2, description="Average order value (total_spent / order_count)")
    discrepancy_explanation: str | None = Field(default=None, description="Explanation if captured settlement differs from order total")

    model_config = ConfigDict(from_attributes=True)


class MonthlySpendingTrend(BaseModel):
    """Spending aggregation for a specific month and currency."""
    month: str = Field(..., description="Year-Month formatted as YYYY-MM")
    currency: str = Field(..., description="ISO-4217 currency code")
    total_amount: Decimal = Field(..., decimal_places=2, description="Total settled amount for the month")
    order_count: int = Field(..., ge=0, description="Number of orders completed in this month")

    model_config = ConfigDict(from_attributes=True)


class CategorySpending(BaseModel):
    """Category-wise spending breakdown."""
    category: str = Field(..., description="Product category name")
    currency: str = Field(..., description="ISO-4217 currency code")
    total_amount: Decimal = Field(..., decimal_places=2, description="Total amount spent in this category")
    item_count: int = Field(..., ge=0, description="Total items purchased in this category")
    percentage_of_currency_total: float = Field(default=0.0, description="Share of total spending within this currency (0-100%)")

    model_config = ConfigDict(from_attributes=True)


class RetailerSpending(BaseModel):
    """Retailer-wise spending breakdown."""
    retailer_name: str = Field(..., description="Retailer merchant name")
    currency: str = Field(..., description="ISO-4217 currency code")
    total_amount: Decimal = Field(..., decimal_places=2, description="Total amount spent at this retailer")
    item_count: int = Field(..., ge=0, description="Total items purchased from this retailer")
    percentage_of_currency_total: float = Field(default=0.0, description="Share of total spending within this currency (0-100%)")

    model_config = ConfigDict(from_attributes=True)


class MandateBudgetUtilization(BaseModel):
    """Active spending mandate utilization metrics."""
    mandate_id: str = Field(..., description="Bound SpendingMandate UUID")
    currency: str = Field(..., description="Mandate currency code")
    total_budget: Decimal = Field(..., decimal_places=2, description="Total authorized budget limit")
    spent_amount: Decimal = Field(..., decimal_places=2, description="Amount spent towards this mandate in mandate currency")
    remaining_budget: Decimal = Field(..., decimal_places=2, description="Remaining unspent budget limit")
    utilization_percentage: float = Field(..., description="Budget utilization percentage (0-100%)")
    maximum_item_price: Decimal = Field(..., decimal_places=2, description="Per-item price ceiling limit")
    maximum_quantity: int = Field(..., ge=1, description="Per-order maximum item quantity")

    model_config = ConfigDict(from_attributes=True)


class PersonalAnalyticsResponse(BaseModel):
    """Authoritative personal shopping analytics for a guest shopping session."""
    session_id: str = Field(..., description="Target ShoppingSession UUID")
    session_identifier: str | None = Field(default=None, description="Guest session cookie/token identifier")
    completed_order_count: int = Field(default=0, ge=0, description="Total distinct completed orders across all currencies")
    total_spending_by_currency: list[CurrencySpendingSummary] = Field(default_factory=list, description="Total completed spending grouped by currency")
    average_order_value_by_currency: dict[str, Decimal] = Field(default_factory=dict, description="Average order value mapped by currency")
    monthly_spending_trends: list[MonthlySpendingTrend] = Field(default_factory=list, description="Monthly spending trends grouped by currency")
    category_spending: list[CategorySpending] = Field(default_factory=list, description="Category-wise spending breakdown")
    retailer_spending: list[RetailerSpending] = Field(default_factory=list, description="Retailer-wise spending breakdown")
    active_mandate_utilization: MandateBudgetUtilization | None = Field(default=None, description="Active mandate budget utilization if configured")

    model_config = ConfigDict(from_attributes=True)


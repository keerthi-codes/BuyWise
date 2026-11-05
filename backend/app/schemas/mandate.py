"""
AI-Powered Multilingual Shopping Intelligence Platform
Pydantic Schemas for Spending Mandates and Purchase Eligibility Validation.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


# -------------------------------------------------------------------------
# Spending Mandate Schemas
# -------------------------------------------------------------------------

class SpendingMandateBase(BaseModel):
    total_budget: float = Field(..., gt=0, description="Total maximum spending budget across all purchases")
    maximum_item_price: float = Field(..., gt=0, description="Maximum allowed unit price for any single item")
    maximum_quantity: int = Field(default=1, ge=1, description="Maximum allowed quantity per purchase item")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO-4217 currency code")


class SpendingMandateCreate(SpendingMandateBase):
    session_id: str | None = Field(
        default=None,
        description="Optional existing ShoppingSession UUID. If omitted, session_identifier is used.",
    )
    session_identifier: str | None = Field(
        default=None,
        description="Client guest session token or cookie identifier to bind or create session.",
    )
    session_token: str | None = Field(
        default=None,
        description="Proof-of-possession token for guest shopping session.",
    )


class SpendingMandateUpdate(BaseModel):
    total_budget: float | None = Field(default=None, gt=0, description="Updated total maximum spending budget")
    maximum_item_price: float | None = Field(default=None, gt=0, description="Updated maximum item price ceiling")
    maximum_quantity: int | None = Field(default=None, ge=1, description="Updated maximum quantity ceiling")
    currency: str | None = Field(default=None, min_length=3, max_length=3, description="Updated currency")
    session_token: str | None = Field(
        default=None,
        description="Proof-of-possession token for guest shopping session.",
    )


class SpendingMandateResponse(SpendingMandateBase):
    id: str
    session_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------------------------
# Policy Validation Schemas
# -------------------------------------------------------------------------

class PurchaseEvaluationRequest(BaseModel):
    product_id: str | None = Field(default=None, description="Optional product UUID being purchased")
    item_name: str | None = Field(default=None, description="Optional item commercial name")
    unit_price: float = Field(..., gt=0, description="Price per unit of the item")
    quantity: int = Field(default=1, ge=1, description="Number of units to purchase")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="Transaction currency code")


class PurchaseEvaluationResponse(BaseModel):
    decision: Literal["ALLOWED", "WARNING_OVERRIDE_REQUIRED", "BLOCKED"] = Field(
        description="Deterministic policy outcome: ALLOWED, WARNING_OVERRIDE_REQUIRED, or BLOCKED"
    )
    is_allowed: bool = Field(description="Boolean convenience flag for quick branching")
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable decision explanation detailing any policy violations",
    )
    purchase_amount: float = Field(description="Total calculated cost of this purchase (unit_price * quantity)")
    quantity: int = Field(description="Requested quantity")
    currency: str = Field(description="Transaction currency code")
    mandate_budget: float | None = Field(default=None, description="Active mandate total budget")
    mandate_max_item_price: float | None = Field(default=None, description="Active mandate maximum item price limit")
    mandate_max_quantity: int | None = Field(default=None, description="Active mandate maximum quantity limit")
    remaining_budget_after_purchase: float | None = Field(
        default=None,
        description="Remaining budget if purchase were approved (total_budget - purchase_amount)",
    )
    is_budget_exceeded: bool = Field(
        default=False,
        description="Whether purchase exceeds preferred budget or item ceiling",
    )
    budget_exceeded_amount: float | None = Field(
        default=None,
        description="Exact amount by which purchase exceeds preferred budget/item ceiling",
    )
    is_override_eligible: bool = Field(
        default=False,
        description="Whether user can explicitly confirm override to proceed",
    )

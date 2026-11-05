"""
AI-Powered Multilingual Shopping Intelligence Platform
Deterministic Spending Policy Engine.
Completely independent of AI / LLMs — enforces hard budgetary and safety constraints.
"""

from backend.app.models.shopping import SpendingMandate
from backend.app.schemas.mandate import PurchaseEvaluationResponse


class SpendingPolicyEngine:
    """
    Deterministic rule evaluation engine for spending mandates.
    CRITICAL SECURITY INVARIANT:
    AI models (e.g. Gemini) may suggest, translate, or explain, but this deterministic
    engine ALWAYS holds ultimate authority over purchase authorization.
    """

    @staticmethod
    def evaluate(
        mandate: SpendingMandate | None,
        unit_price: float,
        quantity: int,
        currency: str = "USD",
    ) -> PurchaseEvaluationResponse:
        """
        Evaluate a candidate purchase against the session's active spending mandate.
        Returns an authoritative ALLOWED, WARNING_OVERRIDE_REQUIRED, or BLOCKED decision
        with human-readable reasons, exact excess calculation, and override eligibility.
        """
        total_cost = round(unit_price * quantity, 2)
        norm_currency = currency.strip().upper()

        # Rule 0: Mandate must exist (Hard blocker)
        if mandate is None:
            return PurchaseEvaluationResponse(
                decision="BLOCKED",
                is_allowed=False,
                reasons=[
                    "No active spending mandate found for this session. "
                    "You must configure a spending mandate before initiating a purchase."
                ],
                purchase_amount=total_cost,
                quantity=quantity,
                currency=norm_currency,
                mandate_budget=None,
                mandate_max_item_price=None,
                mandate_max_quantity=None,
                remaining_budget_after_purchase=None,
                is_budget_exceeded=False,
                budget_exceeded_amount=None,
                is_override_eligible=False,
            )

        hard_reasons: list[str] = []
        soft_reasons: list[str] = []

        # Rule 1: Currency consistency (Hard blocker)
        if mandate.currency.upper() != norm_currency:
            hard_reasons.append(
                f"Purchase currency ({norm_currency}) does not match your active mandate currency "
                f"({mandate.currency.upper()}). Cross-currency conversion is not permitted under this mandate."
            )

        # Rule 3: Quantity ceiling (Hard blocker)
        if quantity > mandate.maximum_quantity:
            hard_reasons.append(
                f"Requested quantity of {quantity} item(s) exceeds your maximum permitted quantity limit "
                f"of {mandate.maximum_quantity} item(s)."
            )

        # Rule 2: Single item price ceiling (Soft budget preference)
        item_excess = 0.0
        if unit_price > mandate.maximum_item_price:
            item_excess = round(unit_price - mandate.maximum_item_price, 2)
            soft_reasons.append(
                f"Item unit price of {norm_currency} {unit_price:,.2f} exceeds your maximum permitted item price limit "
                f"of {norm_currency} {mandate.maximum_item_price:,.2f} by {norm_currency} {item_excess:,.2f}."
            )

        # Rule 4: Total budget ceiling (Soft budget preference)
        budget_excess = 0.0
        if total_cost > mandate.total_budget:
            budget_excess = round(total_cost - mandate.total_budget, 2)
            soft_reasons.append(
                f"Total purchase cost of {norm_currency} {total_cost:,.2f} exceeds your remaining mandate budget "
                f"of {norm_currency} {mandate.total_budget:,.2f} by {norm_currency} {budget_excess:,.2f}."
            )

        # If any hard blocker exists, purchase is BLOCKED unconditionally (no override permitted)
        if hard_reasons:
            all_reasons = hard_reasons + soft_reasons
            return PurchaseEvaluationResponse(
                decision="BLOCKED",
                is_allowed=False,
                reasons=all_reasons,
                purchase_amount=total_cost,
                quantity=quantity,
                currency=norm_currency,
                mandate_budget=mandate.total_budget,
                mandate_max_item_price=mandate.maximum_item_price,
                mandate_max_quantity=mandate.maximum_quantity,
                remaining_budget_after_purchase=round(mandate.total_budget - total_cost, 2),
                is_budget_exceeded=bool(soft_reasons),
                budget_exceeded_amount=max(item_excess, budget_excess) if soft_reasons else None,
                is_override_eligible=False,
            )

        # If soft budget restrictions are exceeded (and no hard blockers exist)
        if soft_reasons:
            max_excess = max(item_excess, budget_excess)
            return PurchaseEvaluationResponse(
                decision="WARNING_OVERRIDE_REQUIRED",
                is_allowed=False,
                reasons=soft_reasons,
                purchase_amount=total_cost,
                quantity=quantity,
                currency=norm_currency,
                mandate_budget=mandate.total_budget,
                mandate_max_item_price=mandate.maximum_item_price,
                mandate_max_quantity=mandate.maximum_quantity,
                remaining_budget_after_purchase=round(mandate.total_budget - total_cost, 2),
                is_budget_exceeded=True,
                budget_exceeded_amount=max_excess,
                is_override_eligible=True,
            )

        # Fully compliant purchase
        remaining_budget = round(mandate.total_budget - total_cost, 2)
        return PurchaseEvaluationResponse(
            decision="ALLOWED",
            is_allowed=True,
            reasons=["Purchase conforms to all spending mandate budget, item price, and quantity limits."],
            purchase_amount=total_cost,
            quantity=quantity,
            currency=norm_currency,
            mandate_budget=mandate.total_budget,
            mandate_max_item_price=mandate.maximum_item_price,
            mandate_max_quantity=mandate.maximum_quantity,
            remaining_budget_after_purchase=remaining_budget,
            is_budget_exceeded=False,
            budget_exceeded_amount=None,
            is_override_eligible=False,
        )

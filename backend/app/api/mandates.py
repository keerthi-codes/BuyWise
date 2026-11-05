"""
AI-Powered Multilingual Shopping Intelligence Platform
Spending Mandate & Policy Validation API Endpoints.
Routes:
  POST /api/mandates               - Create or configure spending mandate
  GET  /api/mandates/{session_id}  - Retrieve active spending mandate
  PUT  /api/mandates/{session_id}  - Update spending mandate configuration
  POST /api/mandates/{session_id}/validate - Deterministic purchase policy verification
"""

import logging
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.auth import get_current_user_optional
from backend.app.models.user import User
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.schemas.mandate import (
    SpendingMandateCreate,
    SpendingMandateUpdate,
    SpendingMandateResponse,
    PurchaseEvaluationRequest,
    PurchaseEvaluationResponse,
)
from backend.app.services.policy_engine import SpendingPolicyEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mandates", tags=["Spending Mandates"])


@router.post(
    "",
    response_model=SpendingMandateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Configure Spending Mandate",
    description="Establish a deterministic budget, max item price, and quantity limit for a guest shopping session.",
)
def create_mandate(
    payload: SpendingMandateCreate,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> SpendingMandateResponse:
    """Create or configure a session spending mandate."""
    if payload.maximum_item_price > payload.total_budget:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="maximum_item_price cannot exceed total_budget.",
        )

    # Resolve or create the guest session
    session = MandateRepository.get_or_create_session(
        db=db,
        session_id=payload.session_id,
        session_identifier=payload.session_identifier,
    )

    effective_token = x_guest_session_token or payload.session_token
    if session.user_id:
        if not current_user or session.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Spending mandate belongs to another account.",
            )
    elif not current_user and session.session_token:
        if not effective_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing guest session proof of possession.",
            )
        if session.session_token != effective_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid guest session proof of possession.",
            )
    elif current_user:
        # If user is authenticated, associate session with user
        session.user_id = current_user.id
        db.commit()

    mandate = MandateRepository.create_or_update_mandate(
        db=db,
        session_id=session.id,
        total_budget=payload.total_budget,
        maximum_item_price=payload.maximum_item_price,
        maximum_quantity=payload.maximum_quantity,
        currency=payload.currency,
    )

    return SpendingMandateResponse.model_validate(mandate)


@router.get(
    "/{session_id}",
    response_model=SpendingMandateResponse,
    summary="Retrieve Active Spending Mandate",
    description="Retrieve the configured spending mandate for a given session ID.",
)
def get_mandate(
    session_id: str,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> SpendingMandateResponse:
    """Fetch active mandate for session."""
    mandate = MandateRepository.get_mandate_by_session_id(db, session_id)
    if not mandate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No spending mandate found for session '{session_id}'.",
        )
    if mandate.session:
        if mandate.session.user_id:
            if not current_user or mandate.session.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Spending mandate belongs to another account.",
                )
        elif not current_user and mandate.session.session_token:
            if not x_guest_session_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing guest session proof of possession.",
                )
            if mandate.session.session_token != x_guest_session_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid guest session proof of possession.",
                )
    return SpendingMandateResponse.model_validate(mandate)


@router.put(
    "/{session_id}",
    response_model=SpendingMandateResponse,
    summary="Update Spending Mandate",
    description="Modify budget ceilings, item price limits, or maximum quantity for a session mandate.",
)
def update_mandate(
    session_id: str,
    payload: SpendingMandateUpdate,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> SpendingMandateResponse:
    """Update existing spending mandate."""
    existing = MandateRepository.get_mandate_by_session_id(db, session_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No spending mandate found for session '{session_id}'.",
        )
    effective_token = x_guest_session_token or payload.session_token
    if existing.session:
        if existing.session.user_id:
            if not current_user or existing.session.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Spending mandate belongs to another account.",
                )
        elif not current_user and existing.session.session_token:
            if not effective_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing guest session proof of possession.",
                )
            if existing.session.session_token != effective_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid guest session proof of possession.",
                )

    # Validate that maximum_item_price does not exceed total_budget after update
    effective_budget = payload.total_budget if payload.total_budget is not None else existing.total_budget
    effective_max_price = payload.maximum_item_price if payload.maximum_item_price is not None else existing.maximum_item_price
    if effective_max_price > effective_budget:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="maximum_item_price cannot exceed total_budget.",
        )

    updated = MandateRepository.update_mandate_fields(
        db=db,
        session_id=session_id,
        total_budget=payload.total_budget,
        maximum_item_price=payload.maximum_item_price,
        maximum_quantity=payload.maximum_quantity,
        currency=payload.currency,
    )

    return SpendingMandateResponse.model_validate(updated)


@router.post(
    "/{session_id}/validate",
    response_model=PurchaseEvaluationResponse,
    summary="Deterministic Policy Purchase Validation",
    description="Evaluate a candidate item purchase against the session's active spending mandate. Returns ALLOWED or BLOCKED.",
)
def validate_purchase(
    session_id: str,
    payload: PurchaseEvaluationRequest,
    x_guest_session_token: Annotated[str | None, Header(alias="X-Guest-Session-Token")] = None,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PurchaseEvaluationResponse:
    """
    Deterministic rule validation endpoint.
    Verifies budget ceiling, item price limits, and quantity restrictions.
    Independent of AI recommendation logic.
    """
    mandate = MandateRepository.get_mandate_by_session_id(db, session_id)
    if mandate and mandate.session:
        if mandate.session.user_id:
            if not current_user or mandate.session.user_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Spending mandate belongs to another account.",
                )
        elif not current_user and mandate.session.session_token:
            if not x_guest_session_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing guest session proof of possession.",
                )
            if mandate.session.session_token != x_guest_session_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid guest session proof of possession.",
                )

    evaluation = SpendingPolicyEngine.evaluate(
        mandate=mandate,
        unit_price=payload.unit_price,
        quantity=payload.quantity,
        currency=payload.currency,
    )

    return evaluation

    return evaluation

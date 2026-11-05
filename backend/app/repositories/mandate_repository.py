"""
AI-Powered Multilingual Shopping Intelligence Platform
Spending Mandate & Guest Session Repository.
Encapsulates database operations for guest sessions and spending mandates.
"""

from datetime import datetime, timezone
import secrets
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.models.shopping import ShoppingSession, SpendingMandate


class MandateRepository:
    """Repository handling ShoppingSession and SpendingMandate persistence."""

    @staticmethod
    def get_or_create_session(
        db: Session,
        session_id: str | None = None,
        session_identifier: str | None = None,
    ) -> ShoppingSession:
        """
        Retrieve an existing ShoppingSession by ID or identifier, or create a fresh one.
        """
        session: ShoppingSession | None = None

        if session_id:
            session = db.get(ShoppingSession, session_id)

        if not session and session_identifier:
            stmt = select(ShoppingSession).where(
                ShoppingSession.session_identifier == session_identifier
            )
            session = db.scalars(stmt).first()

        if not session:
            # Generate new guest session
            new_id = session_id or f"sess_{uuid.uuid4().hex[:16]}"
            new_identifier = session_identifier or f"guest_{uuid.uuid4().hex[:16]}"
            session = ShoppingSession(
                id=new_id,
                session_identifier=new_identifier,
                session_token=None,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(session)
            db.commit()
            db.refresh(session)

        return session

    @staticmethod
    def get_mandate_by_session_id(db: Session, session_id: str) -> SpendingMandate | None:
        """Fetch active spending mandate for a given session ID."""
        stmt = (
            select(SpendingMandate)
            .options(joinedload(SpendingMandate.session))
            .where(SpendingMandate.session_id == session_id)
        )
        return db.scalars(stmt).first()

    @staticmethod
    def create_or_update_mandate(
        db: Session,
        session_id: str,
        total_budget: float,
        maximum_item_price: float,
        maximum_quantity: int,
        currency: str = "USD",
    ) -> SpendingMandate:
        """
        Create a new spending mandate for session or update existing configuration.
        """
        mandate = MandateRepository.get_mandate_by_session_id(db, session_id)
        now = datetime.now(timezone.utc)

        if mandate:
            mandate.total_budget = total_budget
            mandate.maximum_item_price = maximum_item_price
            mandate.maximum_quantity = maximum_quantity
            mandate.currency = currency.upper()
            mandate.updated_at = now
        else:
            mandate = SpendingMandate(
                session_id=session_id,
                total_budget=total_budget,
                maximum_item_price=maximum_item_price,
                maximum_quantity=maximum_quantity,
                currency=currency.upper(),
                created_at=now,
                updated_at=now,
            )
            db.add(mandate)

        db.commit()
        db.refresh(mandate)
        return mandate

    @staticmethod
    def update_mandate_fields(
        db: Session,
        session_id: str,
        total_budget: float | None = None,
        maximum_item_price: float | None = None,
        maximum_quantity: int | None = None,
        currency: str | None = None,
    ) -> SpendingMandate | None:
        """Partially update spending mandate fields."""
        mandate = MandateRepository.get_mandate_by_session_id(db, session_id)
        if not mandate:
            return None

        if total_budget is not None:
            mandate.total_budget = total_budget
        if maximum_item_price is not None:
            mandate.maximum_item_price = maximum_item_price
        if maximum_quantity is not None:
            mandate.maximum_quantity = maximum_quantity
        if currency is not None:
            mandate.currency = currency.upper()

        mandate.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(mandate)
        return mandate

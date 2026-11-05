"""
AI-Powered Multilingual Shopping Intelligence Platform
SQLAlchemy 2.0 Core Shopping Domain Models.
Supports Products, Retailer Offers, Lightweight Guest Sessions, and Spending Mandates.
"""

from datetime import datetime, timezone
import uuid
from typing import Any
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


def generate_uuid() -> str:
    """Generate a UUID v4 string identifier."""
    return str(uuid.uuid4())


class Product(Base):
    """
    Authoritative product entity storing core metadata and specifications.
    One product can have multiple competing retailer offers.
    Supports both synthetic demonstration products and live aggregated products (e.g. Channel3).
    """

    __tablename__ = "products"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False, index=True)
    brand = Column(String(128), nullable=False, index=True)
    category = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    specifications = Column(JSON, nullable=False, default=dict)
    image_url = Column(String(512), nullable=True)
    provider = Column(String(64), nullable=False, default="synthetic", index=True)
    provider_product_id = Column(String(128), nullable=True, index=True)
    is_synthetic = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    offers = relationship(
        "RetailerOffer",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="joined",
    )
    price_snapshots = relationship(
        "PriceSnapshot",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="PriceSnapshot.recorded_at.desc()",
    )

    def lowest_price(self, currency: str | None = None) -> float | None:
        """
        Calculate the lowest price among available retailer offers.
        Currency-aware: filters by specified currency or evaluates within primary currency
        to prevent direct numeric comparison across different currencies (e.g. USD vs INR).
        """
        best = self.lowest_price_offer(currency=currency)
        return best.price if best else None

    def lowest_price_offer(self, currency: str | None = None) -> Any:
        """
        Retrieve the authoritative RetailerOffer yielding the lowest purchase price.
        Currency-aware:
          - Prioritizes in-stock items ('IN_STOCK', 'LIMITED_STOCK', 'LOW_STOCK').
          - Never mixes prices from disparate currencies without explicit filtering.
        """
        if not self.offers:
            return None

        target_currency = currency.strip().upper() if currency else None
        if not target_currency:
            currencies = [o.currency.strip().upper() for o in self.offers if o.currency]
            target_currency = currencies[0] if currencies else "USD"

        matching_offers = [
            o for o in self.offers
            if o.currency and o.currency.strip().upper() == target_currency and o.price and o.price > 0
        ]
        if not matching_offers:
            return None

        in_stock_offers = [
            o for o in matching_offers
            if o.availability.upper() in ("IN_STOCK", "LIMITED_STOCK", "LOW_STOCK")
        ]
        candidates = in_stock_offers if in_stock_offers else matching_offers
        return min(candidates, key=lambda o: o.price)


class RetailerOffer(Base):
    """
    Retailer-specific listing for a product, tracking price, store, availability, and URL.
    Supports tracking comparison prices, provider identifiers, and freshness timestamps.
    """

    __tablename__ = "retailer_offers"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    product_id = Column(
        String(64),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    retailer_name = Column(String(128), nullable=False, index=True)
    price = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    product_url = Column(String(512), nullable=False)
    availability = Column(String(64), nullable=False, default="IN_STOCK")
    compare_at_price = Column(Float, nullable=True)
    provider_offer_id = Column(String(128), nullable=True, index=True)
    is_synthetic = Column(Boolean, nullable=False, default=True)
    retrieved_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    product = relationship("Product", back_populates="offers")


class PriceSnapshot(Base):
    """
    Persistent historical price observation for an individual product and retailer.
    Preserves point-in-time price observations, stock availability, and retailer identity.
    Strictly isolated from synthetic demo products (only genuine live offers recorded).
    """

    __tablename__ = "price_snapshots"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    product_id = Column(
        String(64),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    retailer_name = Column(String(128), nullable=False, index=True)
    provider_offer_id = Column(String(128), nullable=True, index=True)
    price = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    availability = Column(String(64), nullable=False, default="IN_STOCK")
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    is_synthetic = Column(Boolean, nullable=False, default=False)

    # Relationships
    product = relationship("Product", back_populates="price_snapshots")

    __table_args__ = (
        Index("ix_price_snapshots_product_currency", "product_id", "currency"),
        Index("ix_price_snapshots_product_retailer", "product_id", "retailer_name"),
        Index("ix_price_snapshots_lookup", "product_id", "currency", "recorded_at"),
    )


class ShoppingSession(Base):
    """
    Lightweight guest browsing session for mandate association without requiring full user registration.
    """

    __tablename__ = "shopping_sessions"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    session_identifier = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(
        String(64),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_token = Column(
        String(64),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = relationship(
        "User",
        back_populates="sessions",
    )
    mandate = relationship(
        "SpendingMandate",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    orders = relationship(
        "PaymentOrder",
        back_populates="session",
    )


class SpendingMandate(Base):
    """
    Deterministic user spending limit policy.
    Enforces maximum total budget, per-item ceiling, and maximum quantity.
    """

    __tablename__ = "spending_mandates"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    session_id = Column(
        String(64),
        ForeignKey("shopping_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    total_budget = Column(Float, nullable=False)
    maximum_item_price = Column(Float, nullable=False)
    maximum_quantity = Column(Integer, nullable=False, default=1)
    currency = Column(String(3), nullable=False, default="USD")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    session = relationship("ShoppingSession", back_populates="mandate")
    orders = relationship("PaymentOrder", back_populates="mandate")

"""
AI-Powered Multilingual Shopping Intelligence Platform
Pydantic Schemas for Products, Retailer Offers, and Comparisons.
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


# -------------------------------------------------------------------------
# Retailer Offer Schemas
# -------------------------------------------------------------------------

class RetailerOfferBase(BaseModel):
    retailer_name: str = Field(..., description="Retailer merchant name (e.g. Amazon, BestBuy)")
    price: float = Field(..., gt=0, description="Offered purchase price")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO-4217 3-letter currency code")
    product_url: str = Field(..., description="Direct outbound product listing URL")
    availability: str = Field(default="IN_STOCK", description="Stock availability status")
    compare_at_price: float | None = Field(default=None, description="Original list price before discount")
    is_synthetic: bool = Field(default=True, description="Whether this offer is synthetic demo data")


class RetailerOfferCreate(RetailerOfferBase):
    product_id: str | None = None
    provider_offer_id: str | None = None


class RetailerOfferResponse(RetailerOfferBase):
    id: str
    product_id: str
    created_at: datetime
    retrieved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# -------------------------------------------------------------------------
# Product Schemas
# -------------------------------------------------------------------------

class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Product commercial name")
    brand: str = Field(..., min_length=1, max_length=128, description="Manufacturer brand name")
    category: str = Field(..., min_length=1, max_length=128, description="Primary product taxonomy category")
    description: str = Field(default="", description="Detailed marketing & technical description")
    specifications: dict[str, Any] = Field(default_factory=dict, description="Structured technical specifications")
    image_url: str | None = Field(default=None, max_length=512, description="Product image asset URL")
    is_synthetic: bool = Field(default=True, description="Whether this product is synthetic demo data")
    provider: str = Field(default="synthetic", description="Originating provider (e.g. synthetic, channel3)")
    provider_product_id: str | None = Field(default=None, description="Provider-assigned product identifier")
    rating: float | None = Field(default=None, ge=0.0, le=5.0, description="Average validated user rating score (0.0 to 5.0)")
    rating_count: int | None = Field(default=None, ge=0, description="Total count of customer reviews/ratings")
    data_quality_warnings: list[str] = Field(default_factory=list, description="Data quality or taxonomy warnings identified during ingestion")


class ProductCreate(ProductBase):
    offers: list[RetailerOfferCreate] = Field(default_factory=list, description="Initial retailer offers")


class ProductResponse(ProductBase):
    id: str
    created_at: datetime
    lowest_price: float | None = None
    offers: list[RetailerOfferResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ProductDetailResponse(ProductResponse):
    lowest_price_retailer: str | None = None
    total_offers: int = 0


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# -------------------------------------------------------------------------
# Comparison Schemas
# -------------------------------------------------------------------------

class ProductCompareItem(BaseModel):
    id: str
    name: str
    brand: str
    category: str
    image_url: str | None = None
    lowest_price: float | None = None
    currency: str = "USD"
    specifications: dict[str, Any] = Field(default_factory=dict)
    offers: list[RetailerOfferResponse] = Field(default_factory=list)
    best_offer_retailer: str | None = None
    overall_availability: str = "IN_STOCK"
    is_synthetic: bool = True
    provider: str = "synthetic"
    provider_product_id: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    data_quality_warnings: list[str] = Field(default_factory=list)


class ComparisonExplanation(BaseModel):
    """
    Grounded multi-product comparison summary and intended-use recommendation.
    """
    summary: str = Field(..., description="High-level overview of the compared items")
    performance_differences: list[str] = Field(default_factory=list, description="Evidenced technical differences")
    key_differences: list[str] = Field(default_factory=list, description="Key evidenced differences")
    value_tradeoffs: str = Field(default="", description="Analysis of price spreads and value trade-offs")
    tradeoffs: list[str] = Field(default_factory=list, description="Detailed trade-off points")
    best_choice_for_use_case: str = Field(default="", description="Recommended product for the specific user need")
    overall_winner: str | None = Field(default=None, description="Overall winning product for use case")
    best_value_pick: str | None = Field(default=None, description="Best value for money product")
    missing_specs_notes: list[str] = Field(default_factory=list, description="Noteworthy missing specs in comparison")
    currency_notes: str | None = Field(default=None, description="Notes on currency alignment or cross-currency cautions")
    currency_consistency_warning: str | None = Field(default=None, description="Warning if cross-currency comparisons lack FX conversion")


class ProductComparisonResponse(BaseModel):
    products: list[ProductCompareItem]
    compared_count: int
    comparison_matrix: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Attribute-based comparison matrix mapping specification keys to product values",
    )
    price_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="High-level price metrics including lowest price across products and price ranges",
    )
    ai_explanation: ComparisonExplanation | None = Field(
        default=None,
        description="Grounded AI multi-product comparison explanation, trade-offs, and intended-use recommendations",
    )


# -------------------------------------------------------------------------
# Product Intelligence Schemas (Phase 3B)
# -------------------------------------------------------------------------

class CurrentBestPrice(BaseModel):
    """Cheapest currently available offer for a specific currency."""
    currency: str = Field(..., description="ISO-4217 currency code")
    price: float = Field(..., gt=0, description="Lowest currently available price")
    retailer_name: str = Field(..., description="Retailer offering the lowest price")
    availability: str = Field(default="IN_STOCK", description="Stock status of the best offer")
    provider_offer_id: str | None = Field(default=None, description="Provider-assigned offer identifier")
    product_url: str | None = Field(default=None, description="Direct merchant offer URL")

    model_config = ConfigDict(from_attributes=True)


class HistoricalPricePoint(BaseModel):
    """Authentic point-in-time price extreme (all-time high or low) for a currency."""
    currency: str = Field(..., description="ISO-4217 currency code")
    price: float = Field(..., gt=0, description="Recorded historical extreme price")
    retailer_name: str = Field(..., description="Retailer associated with this snapshot")
    recorded_at: datetime = Field(..., description="UTC timestamp when this price was recorded")
    provider_offer_id: str | None = Field(default=None, description="Provider-assigned offer identifier")

    model_config = ConfigDict(from_attributes=True)


class OfferPriceChange(BaseModel):
    """Price difference between latest and preceding snapshot for a specific retailer offer."""
    retailer_name: str = Field(..., description="Retailer merchant name")
    currency: str = Field(..., description="ISO-4217 currency code")
    provider_offer_id: str | None = Field(default=None, description="Provider offer identifier")
    current_price: float = Field(..., gt=0, description="Latest recorded price")
    previous_price: float | None = Field(default=None, description="Preceding recorded price, if available")
    absolute_change: float | None = Field(default=None, description="Signed currency difference (current - previous)")
    percentage_change: float | None = Field(default=None, description="Signed percentage difference relative to previous price")
    status: str = Field(..., description="Status indicator: 'INCREASED' | 'DECREASED' | 'UNCHANGED' | 'INSUFFICIENT_DATA'")
    previous_recorded_at: datetime | None = Field(default=None, description="UTC timestamp of the preceding observation")
    latest_recorded_at: datetime | None = Field(default=None, description="UTC timestamp of the latest observation")
    observation_count: int = Field(default=1, ge=1, description="Number of historical observations for this offer")

    model_config = ConfigDict(from_attributes=True)


class CurrencyRetailerComparison(BaseModel):
    """Comparison across current active retailer offers within a single currency."""
    currency: str = Field(..., description="ISO-4217 currency code")
    cheapest_retailer: str | None = Field(default=None, description="Retailer with lowest currently available price")
    cheapest_price: float | None = Field(default=None, description="Lowest currently available price")
    highest_retailer: str | None = Field(default=None, description="Retailer with highest currently available price")
    highest_price: float | None = Field(default=None, description="Highest currently available price")
    price_difference: float | None = Field(default=None, description="Spread between highest and lowest available prices")
    potential_savings_percentage: float | None = Field(default=None, description="Potential percentage savings from choosing cheapest over highest")
    available_offers_count: int = Field(default=0, ge=0, description="Number of currently in-stock/available retailer offers")
    available_retailers: list[str] = Field(default_factory=list, description="Names of retailers with valid available stock")
    unavailable_retailers: list[str] = Field(default_factory=list, description="Names of retailers whose offers are out-of-stock")

    model_config = ConfigDict(from_attributes=True)


class PriceStability(BaseModel):
    """Statistical price stability evaluation for a currency based on authentic historical observations."""
    currency: str = Field(..., description="ISO-4217 currency code")
    status: str = Field(..., description="Price stability indicator: 'STABLE' | 'MODERATE' | 'VOLATILE' | 'INSUFFICIENT_DATA'")
    observation_count: int = Field(default=0, ge=0, description="Number of historical snapshot observations")
    volatility_score: float | None = Field(default=None, description="Coefficient of variation percentage, if sufficient observations exist")
    min_recorded_price: float | None = Field(default=None, description="Lowest authentic price recorded in this currency")
    max_recorded_price: float | None = Field(default=None, description="Highest authentic price recorded in this currency")
    price_range: float | None = Field(default=None, description="Absolute spread between highest and lowest recorded prices")
    explanation: str = Field(..., description="Transparent textual explanation of the stability classification")

    model_config = ConfigDict(from_attributes=True)


class PriceSnapshotPoint(BaseModel):
    """A single chronological price history data point."""
    id: str = Field(..., description="Snapshot record UUID")
    retailer_name: str = Field(..., description="Retailer merchant name")
    provider_offer_id: str | None = Field(default=None, description="Provider offer identifier")
    price: float = Field(..., gt=0, description="Recorded price at observation time")
    currency: str = Field(..., description="ISO-4217 currency code")
    availability: str = Field(default="IN_STOCK", description="Stock status at observation time")
    recorded_at: datetime = Field(..., description="UTC timestamp of the observation")

    model_config = ConfigDict(from_attributes=True)


class ProductIntelligenceResponse(BaseModel):
    """Authoritative Product Intelligence and Historical Price Analysis."""
    product_id: str = Field(..., description="Target Product UUID")
    name: str = Field(..., description="Product commercial name")
    brand: str = Field(..., description="Manufacturer brand name")
    category: str = Field(..., description="Product taxonomy category")
    image_url: str | None = Field(default=None, description="Product image asset URL")
    is_synthetic: bool = Field(..., description="Whether this product is synthetic demo data")
    provider: str = Field(..., description="Originating provider (e.g. channel3, synthetic)")
    provider_product_id: str | None = Field(default=None, description="Provider-assigned product identifier")

    tracking_start_date: datetime | None = Field(default=None, description="Earliest recorded snapshot timestamp (UTC)")
    total_genuine_observations: int = Field(default=0, ge=0, description="Total count of genuine historical snapshots")
    is_tracking_active: bool = Field(default=False, description="Whether active price tracking is collecting observations")

    current_offers: list[RetailerOfferResponse] = Field(default_factory=list, description="Current live retailer offers")
    best_current_prices: list[CurrentBestPrice] = Field(default_factory=list, description="Cheapest available current offer per currency")

    historical_lowest_prices: list[HistoricalPricePoint] = Field(default_factory=list, description="All-time lowest authentic recorded price per currency")
    historical_highest_prices: list[HistoricalPricePoint] = Field(default_factory=list, description="All-time highest authentic recorded price per currency")

    price_changes: list[OfferPriceChange] = Field(default_factory=list, description="Recent price changes per retailer and offer identity")
    retailer_comparisons: list[CurrencyRetailerComparison] = Field(default_factory=list, description="Cross-retailer price spread and savings per currency")
    price_stability: list[PriceStability] = Field(default_factory=list, description="Price stability assessment per currency")

    price_history: list[PriceSnapshotPoint] = Field(default_factory=list, description="Chronological price history snapshots")

    model_config = ConfigDict(from_attributes=True)

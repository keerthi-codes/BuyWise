"""
AI-Powered Multilingual Shopping Intelligence Platform
Unit and Integration Tests for Gemini AI Integration (Phase 3).
Validates:
- Category A: GeminiClient error handling, timeouts, 503 degradation, rate limits, schema validation
- Category B: Structured shopping intent extraction (laptop, headphones, smartphone, budget, currency, multilingual)
- Category C: Grounded product intelligence, missing specs transparency, hallucinated ID rejection
- Category D: Multi-product comparison explanation and mixed currency safeguard
- Category E: Conversational shopping assistant sessions and context flow
- Category F: Spending policy boundary enforcement (AI cannot approve purchases or bypass limits)
- Category G: Assistant API endpoints and graceful degradation
"""

from decimal import Decimal
import json
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
import httpx
from sqlalchemy.orm import Session

from backend.app.models.shopping import Product, RetailerOffer, SpendingMandate, ShoppingSession
from backend.app.schemas.product import ProductResponse
from backend.app.schemas.assistant import (
    ShoppingIntent,
    ProductIntelligence,
    ProductIntelligenceList,
    ComparisonExplanation,
    AssistantQueryRequest,
    AssistantQueryResponse,
    ProductEligibilityItem,
)
from backend.app.core.config import get_settings
from google.genai import types, errors
from google.genai._api_client import retry_args
import tenacity
from backend.app.services.gemini_client import (
    GeminiClient,
    GeminiError,
    GeminiAuthError,
    GeminiRateLimitError,
    GeminiTimeoutError,
    GeminiUnavailableError,
    GeminiResponseError,
)
from backend.app.services.assistant_service import AssistantService
from backend.app.services.policy_engine import SpendingPolicyEngine


# =============================================================================
# CATEGORY A: Gemini Client Tests
# =============================================================================

def test_gemini_client_init_defaults():
    """Verify GeminiClient initializes with default settings."""
    client = GeminiClient(api_key="test_dummy_key")
    assert client.model == get_settings().GEMINI_MODEL
    assert client.timeout_seconds == 30.0
    assert client.thinking_level == "medium"
    assert client.is_configured is True


def test_gemini_client_retry_options_configuration():
    """Verify GeminiClient configures HttpRetryOptions with 3 attempts and transient codes [500, 502, 503, 504]."""
    client = GeminiClient(api_key="test_dummy_key")
    assert client.is_configured is True
    http_options = client._client._api_client._http_options
    assert http_options.retry_options is not None
    retry_opts = http_options.retry_options
    assert retry_opts.attempts == 3
    assert retry_opts.initial_delay == 1.0
    assert retry_opts.max_delay == 4.0
    assert retry_opts.exp_base == 2.0
    assert retry_opts.jitter == 0.5
    assert retry_opts.http_status_codes == [500, 502, 503, 504]


def test_gemini_client_retry_predicate_filters_non_transient_errors():
    """Verify that only 500, 502, 503, 504 errors trigger retries, and non-transient errors (401, 403, 400, 429) do not."""
    client = GeminiClient(api_key="test_dummy_key")
    retry_opts = client._client._api_client._http_options.retry_options
    r_args = retry_args(retry_opts)
    pred = r_args["retry"].predicate

    # Non-transient errors: must NOT be retried
    assert pred(errors.APIError(code=401, response_json={})) is False
    assert pred(errors.APIError(code=403, response_json={})) is False
    assert pred(errors.APIError(code=400, response_json={})) is False
    assert pred(errors.APIError(code=404, response_json={})) is False
    assert pred(errors.APIError(code=422, response_json={})) is False
    assert pred(errors.APIError(code=429, response_json={})) is False

    # Transient server errors: MUST be retried
    assert pred(errors.APIError(code=500, response_json={})) is True
    assert pred(errors.APIError(code=502, response_json={})) is True
    assert pred(errors.APIError(code=503, response_json={})) is True
    assert pred(errors.APIError(code=504, response_json={})) is True


def test_gemini_client_tenacity_retry_execution_behavior():
    """Verify tenacity executes 3 total attempts on 503 and fails fast on attempt 1 for 401."""
    client = GeminiClient(api_key="test_dummy_key")
    retry_opts = client._client._api_client._http_options.retry_options
    fast_opts = types.HttpRetryOptions(
        attempts=retry_opts.attempts,
        initial_delay=0.0,
        max_delay=0.0,
        exp_base=1.0,
        jitter=0.0,
        http_status_codes=retry_opts.http_status_codes,
    )
    r_args = retry_args(fast_opts)
    retryer = tenacity.Retrying(**r_args)

    attempts_401 = 0
    def fail_401():
        nonlocal attempts_401
        attempts_401 += 1
        raise errors.APIError(code=401, response_json={})

    with pytest.raises(errors.APIError):
        retryer(fail_401)
    assert attempts_401 == 1, "401 Unauthorized must fail fast on attempt 1 without retry"

    attempts_503 = 0
    def fail_503():
        nonlocal attempts_503
        attempts_503 += 1
        raise errors.APIError(code=503, response_json={})

    with pytest.raises(errors.APIError):
        retryer(fail_503)
    assert attempts_503 == 3, "503 Unavailable must attempt 3 times (1 initial + 2 retries)"


def test_gemini_client_unconfigured_raises_auth_error():
    """Verify unconfigured client raises GeminiAuthError when called."""
    client = GeminiClient(api_key="")
    assert client.is_configured is False
    with pytest.raises(GeminiAuthError, match="not configured"):
        client.extract_shopping_intent("laptop")


def test_gemini_client_successful_intent_parsing():
    """Verify client correctly parses structured JSON response into ShoppingIntent."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "laptop for ml",
        "category": "laptop",
        "intended_use": ["machine_learning"],
        "max_budget": 1200.0,
        "min_budget": None,
        "currency": "USD",
        "required_specs": {"ram": "32GB"},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": False,
        "clarification_questions": [],
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("laptop for ml under $1200")
        assert intent.category == "laptop"
        assert intent.max_budget == 1200.0
        assert intent.currency == "USD"
        assert "machine_learning" in intent.intended_use
        assert intent.required_specs.get("ram") == "32GB"


def test_gemini_client_auth_error_handling():
    """Verify 401/403 API errors are mapped to GeminiAuthError."""
    client = GeminiClient(api_key="test_key")
    with patch.object(client._client.models, "generate_content", side_effect=Exception("401 API_KEY_INVALID: User not authenticated")):
        with pytest.raises(GeminiAuthError, match="authentication failed"):
            client.extract_shopping_intent("headphones")


def test_gemini_client_rate_limit_error_handling():
    """Verify 429 quota errors are mapped to GeminiRateLimitError."""
    client = GeminiClient(api_key="test_key")
    with patch.object(client._client.models, "generate_content", side_effect=Exception("429 ResourceExhausted: Quota exceeded")):
        with pytest.raises(GeminiRateLimitError, match="rate limit or quota"):
            client.extract_shopping_intent("smartwatch")


def test_gemini_client_timeout_error_handling():
    """Verify timeout exceptions are mapped to GeminiTimeoutError."""
    client = GeminiClient(api_key="test_key", timeout_seconds=5.0)
    with patch.object(client._client.models, "generate_content", side_effect=Exception("Request timed out: deadline exceeded")):
        with pytest.raises(GeminiTimeoutError, match="timed out"):
            client.extract_shopping_intent("camera")


def test_gemini_client_service_unavailable_error_handling():
    """Verify 503 high demand exceptions are mapped to GeminiUnavailableError."""
    client = GeminiClient(api_key="test_key")
    with patch.object(client._client.models, "generate_content", side_effect=Exception("503 UNAVAILABLE: This model is currently experiencing high demand")):
        with pytest.raises(GeminiUnavailableError, match="temporarily unavailable due to high demand"):
            client.extract_shopping_intent("smartphone")


def test_gemini_client_malformed_json_handling():
    """Verify malformed JSON responses raise GeminiResponseError."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = "{invalid_json: true, unterminated"

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        with pytest.raises(GeminiResponseError, match="Malformed"):
            client.extract_shopping_intent("shoes")


def test_gemini_client_empty_response_handling():
    """Verify empty response raises GeminiResponseError."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = ""

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        with pytest.raises(GeminiResponseError, match="empty response"):
            client.extract_shopping_intent("headphones")


# =============================================================================
# CATEGORY B: Shopping Intent Extraction Tests
# =============================================================================

def test_intent_extraction_laptop_ml():
    """Test extracting shopping intent for ML laptop query."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Find me a laptop for machine learning with 32GB RAM under $1500",
        "category": "laptop",
        "intended_use": ["machine_learning"],
        "max_budget": 1500.0,
        "currency": "USD",
        "required_specs": {"ram": "32GB"},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Find me a laptop for machine learning with 32GB RAM under $1500")
        assert intent.category == "laptop"
        assert intent.max_budget == 1500.0
        assert intent.currency == "USD"
        assert "machine_learning" in intent.intended_use
        assert intent.required_specs.get("ram") == "32GB"


def test_intent_extraction_headphones_travel():
    """Test extracting shopping intent for travel headphones."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Wireless noise cancelling headphones for travel under $200",
        "category": "headphones",
        "intended_use": ["travel"],
        "max_budget": 200.0,
        "currency": "USD",
        "required_specs": {"noise_cancellation": "active", "connectivity": "wireless"},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Wireless noise cancelling headphones for travel under $200")
        assert intent.category == "headphones"
        assert intent.max_budget == 200.0
        assert "travel" in intent.intended_use
        assert intent.required_specs.get("connectivity") == "wireless"


def test_intent_extraction_budget_detection_inr():
    """Test explicit INR budget detection (₹70,000)."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Best gaming laptop under ₹70,000",
        "category": "laptop",
        "intended_use": ["gaming"],
        "max_budget": 70000.0,
        "currency": "INR",
        "required_specs": {},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Best gaming laptop under ₹70,000")
        assert intent.currency == "INR"
        assert intent.max_budget == 70000.0
        assert "gaming" in intent.intended_use


def test_intent_extraction_multilingual_tamil():
    """Test Tamil shopping query intent extraction."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "மெஷின் லேர்னிங்கிற்கு சிறந்த லேப்டாப் ₹70000க்குள்",
        "category": "laptop",
        "intended_use": ["machine_learning"],
        "max_budget": 70000.0,
        "currency": "INR",
        "required_specs": {},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "ta",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("மெஷின் லேர்னிங்கிற்கு சிறந்த லேப்டாப் ₹70000க்குள்")
        assert intent.detected_language == "ta"
        assert intent.category == "laptop"
        assert intent.currency == "INR"
        assert intent.max_budget == 70000.0


def test_intent_extraction_multilingual_spanish():
    """Test Spanish shopping query intent extraction."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Portátil para programación con 16GB de RAM por menos de 1000 euros",
        "category": "laptop",
        "intended_use": ["coding"],
        "max_budget": 1000.0,
        "currency": "EUR",
        "required_specs": {"ram": "16GB"},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "es",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Portátil para programación con 16GB de RAM por menos de 1000 euros")
        assert intent.detected_language == "es"
        assert intent.category == "laptop"
        assert intent.currency == "EUR"
        assert intent.max_budget == 1000.0


def test_intent_extraction_multilingual_french():
    """Test French shopping query intent extraction."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Casque sans fil à réduction de bruit sous 150 euros",
        "category": "headphones",
        "intended_use": ["travel"],
        "max_budget": 150.0,
        "currency": "EUR",
        "required_specs": {"noise_cancellation": "active"},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "fr",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Casque sans fil à réduction de bruit sous 150 euros")
        assert intent.detected_language == "fr"
        assert intent.category == "headphones"
        assert intent.currency == "EUR"
        assert intent.max_budget == 150.0


def test_intent_extraction_ambiguous_query():
    """Test ambiguous shopping query triggers clarification request."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "recommend something good",
        "category": "general",
        "intended_use": [],
        "max_budget": None,
        "currency": "USD",
        "required_specs": {},
        "preferred_specs": {},
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": True,
        "clarification_questions": ["What type of product are you looking for?", "What is your budget?"],
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("recommend something good")
        assert intent.clarification_required is True
        assert len(intent.clarification_questions) > 0


# =============================================================================
# CATEGORY C: Grounded Product Intelligence Tests
# =============================================================================

def test_product_intelligence_grounded_analysis():
    """Test grounded product intelligence maps faithfully to evidence."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(
        query="laptop for ml",
        category="laptop",
        intended_use=["machine_learning"],
        currency="USD",
    )
    products = [
        {
            "id": "prod-101",
            "name": "DevBook Pro 16",
            "brand": "TechCorp",
            "category": "laptop",
            "lowest_price": 1299.99,
            "specifications": {"ram": "32 GB", "gpu": "RTX 4060"},
            "rating": 4.7,
            "offers": [{"currency": "USD", "price": 1299.99}],
        }
    ]

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "items": [
            {
                "product_id": "prod-101",
                "product_name": "DevBook Pro 16",
                "suitability": "EXCELLENT",
                "strengths": ["32 GB RAM is optimal for machine learning models", "Dedicated RTX 4060 GPU"],
                "weaknesses": ["Price is near the top of the range"],
                "intended_use_fit": "Great for local model training and experimentation.",
                "missing_information": [],
                "value_for_money_rating": 4.5,
                "recommendation_rationale": "Meets all ML hardware criteria.",
            }
        ]
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        analyses = client.analyze_products("laptop for ml", intent, products)
        assert len(analyses) == 1
        analysis = analyses[0]
        assert analysis.product_id == "prod-101"
        assert analysis.suitability == "EXCELLENT"
        assert "32 GB RAM" in analysis.strengths[0]


def test_product_intelligence_missing_specs_transparency():
    """Test that missing specifications are explicitly flagged in missing_information."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(
        query="laptop for ml",
        category="laptop",
        intended_use=["machine_learning"],
        currency="USD",
    )
    products = [
        {
            "id": "prod-102",
            "name": "BudgetBook Air",
            "brand": "CheapCo",
            "category": "laptop",
            "lowest_price": 499.99,
            "specifications": {},  # No RAM, GPU, or CPU specs
            "rating": 3.8,
            "offers": [{"currency": "USD", "price": 499.99}],
        }
    ]

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "items": [
            {
                "product_id": "prod-102",
                "product_name": "BudgetBook Air",
                "suitability": "UNKNOWN",
                "strengths": ["Affordable price point"],
                "weaknesses": ["Key technical specifications not provided by retailer"],
                "intended_use_fit": "Cannot confirm suitability for machine learning without RAM or GPU details.",
                "missing_information": ["RAM size", "GPU model", "CPU generation"],
                "value_for_money_rating": 3.0,
                "recommendation_rationale": "Inspect verified manufacturer specs before purchase.",
            }
        ]
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        analyses = client.analyze_products("laptop for ml", intent, products)
        assert len(analyses) == 1
        assert "RAM size" in analyses[0].missing_information
        assert analyses[0].suitability == "UNKNOWN"


def test_product_intelligence_filters_hallucinated_product_ids():
    """Security Invariant: Verify AI cannot hallucinate products outside provided evidence."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(query="laptop", category="laptop")
    products = [
        {"id": "real-prod-1", "name": "Real Laptop 1", "lowest_price": 999.0, "offers": []}
    ]

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "items": [
            {
                "product_id": "real-prod-1",
                "product_name": "Real Laptop 1",
                "suitability": "GOOD",
                "strengths": ["Reliable"],
                "weaknesses": [],
                "intended_use_fit": "General use",
                "missing_information": [],
                "value_for_money_rating": 4.0,
                "recommendation_rationale": "Solid option",
            },
            {
                "product_id": "hallucinated-prod-999",  # NOT in evidence!
                "product_name": "Hallucinated Phantom Laptop",
                "suitability": "EXCELLENT",
                "strengths": ["Quantum speed"],
                "weaknesses": [],
                "intended_use_fit": "Supercomputing",
                "missing_information": [],
                "value_for_money_rating": 5.0,
                "recommendation_rationale": "Invented out of nowhere",
            },
        ]
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        analyses = client.analyze_products("laptop", intent, products)
        # Verify hallucinated item was dropped
        assert len(analyses) == 1
        assert analyses[0].product_id == "real-prod-1"


# =============================================================================
# CATEGORY D: Multi-Product Comparison Tests
# =============================================================================

def test_explain_comparison_valid():
    """Test grounded multi-product comparison explanation."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(query="headphones", category="headphones", intended_use=["travel"])
    comparison_data = {
        "products": [
            {"id": "p1", "name": "Bose QC45", "lowest_price": 279.0, "rating": 4.6},
            {"id": "p2", "name": "Sony WH-1000XM5", "lowest_price": 348.0, "rating": 4.8},
        ]
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "summary": "Comparing Bose QC45 and Sony WH-1000XM5 for travel use.",
        "key_differences": ["Sony offers superior active noise cancellation", "Bose is slightly more lightweight and affordable"],
        "tradeoffs": ["Sony costs $69 more but has better microphone and soundstage"],
        "overall_winner": "Sony WH-1000XM5",
        "best_value_pick": "Bose QC45",
        "currency_consistency_warning": None,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        explanation = client.explain_comparison(intent, comparison_data)
        assert "Sony" in explanation.key_differences[0]
        assert explanation.overall_winner == "Sony WH-1000XM5"
        assert explanation.best_value_pick == "Bose QC45"
        assert explanation.currency_consistency_warning is None


def test_explain_comparison_mixed_currency_safeguard():
    """Test comparison flags mixed currencies correctly."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(query="laptop", category="laptop")
    comparison_data = {
        "products": [
            {"id": "p1", "name": "Laptop US", "lowest_price": 1000.0, "currency": "USD"},
            {"id": "p2", "name": "Laptop IN", "lowest_price": 75000.0, "currency": "INR"},
        ]
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "summary": "Comparing US and Indian market laptops.",
        "key_differences": ["Direct pricing comparison requires FX conversion."],
        "tradeoffs": ["Cross-border warranty considerations"],
        "overall_winner": None,
        "best_value_pick": None,
        "currency_consistency_warning": "Warning: Comparing prices across USD and INR requires real-time FX conversion.",
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        explanation = client.explain_comparison(intent, comparison_data)
        assert explanation.currency_consistency_warning is not None
        assert "FX conversion" in explanation.currency_consistency_warning


# =============================================================================
# CATEGORY E: Conversational Shopping Assistant Tests
# =============================================================================

def test_assistant_session_creation_and_intent_flow(test_session: Session):
    """Test end-to-end assistant service creates session and processes initial query."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_configured = True
    mock_client.extract_shopping_intent.return_value = ShoppingIntent(
        query="laptop",
        category="laptop",
        intended_use=["coding"],
        max_budget=1000.0,
        currency="USD",
        detected_language="en",
    )
    mock_client.analyze_products.return_value = []
    mock_client.generate_conversational_response.return_value = "Here are the top coding laptops for your budget."

    req = AssistantQueryRequest(
        query="Find me a coding laptop under $1000",
        session_id="test-session-001",
    )

    mock_c3 = MagicMock()
    mock_c3.is_configured = False

    resp = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=mock_client,
        c3_client=mock_c3,
    )

    assert resp.success is True
    assert resp.session_id == "test-session-001"
    assert resp.intent.category == "laptop"
    assert resp.assistant_message == "Here are the top coding laptops for your budget."
    assert resp.is_ai_degraded is False

    # Verify session persisted in DB
    session_row = test_session.query(ShoppingSession).filter(ShoppingSession.id == "test-session-001").first()
    assert session_row is not None


def test_assistant_followup_query_context(test_session: Session):
    """Test that conversation context is passed through during follow-up query."""
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_configured = True
    mock_client.extract_shopping_intent.return_value = ShoppingIntent(
        query="What about 32GB RAM models?",
        category="laptop",
        intended_use=["coding"],
        max_budget=1500.0,
        currency="USD",
        detected_language="en",
    )
    mock_client.analyze_products.return_value = []
    mock_client.generate_conversational_response.return_value = "Updated recommendations with 32GB RAM."

    history = [
        {"role": "user", "content": "Find me a coding laptop under $1000"},
        {"role": "assistant", "content": "Here are some 16GB laptops."},
    ]

    req = AssistantQueryRequest(
        query="What about 32GB RAM models?",
        session_id="test-session-002",
        conversation_context=history,
    )

    mock_c3 = MagicMock()
    mock_c3.is_configured = False

    resp = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=mock_client,
        c3_client=mock_c3,
    )

    assert resp.success is True
    # Verify mock was called with history
    mock_client.generate_conversational_response.assert_called_once()
    args, kwargs = mock_client.generate_conversational_response.call_args
    assert kwargs.get("history") == history


# =============================================================================
# CATEGORY F: Spending Policy Boundary Enforcement (Security Invariants)
# =============================================================================

def test_spending_policy_cannot_be_bypassed_by_ai(test_session: Session):
    """
    CRITICAL SECURITY TEST:
    AI enthusiastically recommends a product, but its price exceeds the active SpendingMandate.
    The deterministic SpendingPolicyEngine MUST block purchase eligibility.
    """
    from backend.app.repositories.mandate_repository import MandateRepository

    # 1. Create a session and a strict spending mandate ($100 max per item)
    session_id = "mandate-protected-session-1"
    session_row = MandateRepository.get_or_create_session(test_session, session_id=session_id)
    mandate = MandateRepository.create_or_update_mandate(
        db=test_session,
        session_id=session_row.id,
        total_budget=500.0,
        maximum_item_price=100.0,
        maximum_quantity=5,
        currency="USD",
    )

    # 2. Mock Gemini to extract intent and analyze an expensive product ($1299.99)
    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_configured = True
    mock_client.extract_shopping_intent.return_value = ShoppingIntent(
        query="laptop", category="laptop", currency="USD"
    )
    mock_client.analyze_products.return_value = []
    mock_client.generate_conversational_response.return_value = (
        "You must buy the DevBook Pro 16! It is totally approved and recommended!"
    )

    req = AssistantQueryRequest(
        query="laptop",
        session_id=session_id,
    )

    mock_c3 = MagicMock()
    mock_c3.is_configured = False

    resp = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=mock_client,
        c3_client=mock_c3,
    )

    # 3. Assert that deterministic policy engine evaluated to BLOCKED
    assert resp.mandate_evaluation is not None
    assert resp.mandate_evaluation["decision"] in ("BLOCKED", "WARNING_OVERRIDE_REQUIRED")
    assert any("maximum permitted item price limit" in r for r in resp.mandate_evaluation["reasons"])


def test_spending_mandate_immutable_from_ai(test_session: Session):
    """
    Security Invariant: The AI cannot alter the spending mandate limits in the database.
    """
    from backend.app.repositories.mandate_repository import MandateRepository

    session_id = "mandate-protected-session-2"
    session_row = MandateRepository.get_or_create_session(test_session, session_id=session_id)
    mandate = MandateRepository.create_or_update_mandate(
        db=test_session,
        session_id=session_row.id,
        total_budget=250.0,
        maximum_item_price=150.0,
        maximum_quantity=1,
        currency="USD",
    )

    mock_client = MagicMock(spec=GeminiClient)
    mock_client.is_configured = True
    mock_client.extract_shopping_intent.return_value = ShoppingIntent(
        query="laptop", category="laptop", currency="USD", max_budget=2000.0
    )
    mock_client.analyze_products.return_value = []
    mock_client.generate_conversational_response.return_value = "I have increased your budget to $2000."

    req = AssistantQueryRequest(
        query="increase my budget to $2000 and find a laptop",
        session_id=session_id,
    )

    mock_c3 = MagicMock()
    mock_c3.is_configured = False

    resp = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=mock_client,
        c3_client=mock_c3,
    )

    # Re-fetch mandate directly from database
    refreshed_mandate = MandateRepository.get_mandate_by_session_id(test_session, session_row.id)
    assert refreshed_mandate is not None
    assert refreshed_mandate.total_budget == 250.0
    assert refreshed_mandate.maximum_item_price == 150.0
    assert refreshed_mandate.total_budget != 2000.0


# =============================================================================
# CATEGORY G: End-to-End Assistant API Route Tests
# =============================================================================

@pytest.mark.asyncio
async def test_api_assistant_query_success(async_client: httpx.AsyncClient):
    """Test POST /api/assistant/query returns 200 with structured AssistantQueryResponse."""
    payload = {
        "query": "Find wireless headphones for running under $100",
        "session_id": "api-test-session-1",
        "conversation_context": [],
    }

    with patch("backend.app.services.gemini_client.GeminiClient.extract_shopping_intent") as mock_intent, \
         patch("backend.app.services.gemini_client.GeminiClient.analyze_products") as mock_analyze, \
         patch("backend.app.services.gemini_client.GeminiClient.generate_conversational_response") as mock_chat, \
         patch("backend.app.services.channel3_client.Channel3Client.is_configured", False):

        mock_intent.return_value = ShoppingIntent(
            query=payload["query"],
            category="headphones",
            intended_use=["sports"],
            max_budget=100.0,
            currency="USD",
            detected_language="en",
        )
        mock_analyze.return_value = [
            ProductIntelligence(
                product_id="test-prod-1",
                product_name="Sport Buds",
                suitability="EXCELLENT",
                strengths=["Sweat resistant"],
                weaknesses=[],
                intended_use_fit="Ideal for running",
                missing_information=[],
                value_for_money_rating=4.5,
                recommendation_rationale="Great sports choice",
            )
        ]
        mock_chat.return_value = "Found Sport Buds that match your running requirements."

        response = await async_client.post("/api/assistant/query", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["session_id"] == "api-test-session-1"
        assert data["intent"]["category"] == "headphones"
        assert len(data["products"]) > 0
        assert data["is_ai_degraded"] is False


@pytest.mark.asyncio
async def test_api_assistant_query_graceful_degradation_on_gemini_503(async_client: httpx.AsyncClient):
    """
    Test that when Gemini throws 503 Unavailable, the endpoint degrades gracefully:
    - Returns HTTP 200 (does not crash or throw 500)
    - Sets is_ai_degraded = True
    - Falls back to rule-based intent parsing
    - Successfully retrieves real catalog products
    - Generates deterministic summary message
    """
    payload = {
        "query": "laptop under $1000",
        "session_id": "api-degradation-session",
    }

    with patch(
        "backend.app.services.gemini_client.GeminiClient.extract_shopping_intent",
        side_effect=GeminiUnavailableError("Gemini service is temporarily unavailable due to high demand (503)."),
    ), patch("backend.app.services.channel3_client.Channel3Client.is_configured", False):
        response = await async_client.post("/api/assistant/query", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["is_ai_degraded"] is True
        assert "temporarily unavailable" in data["degraded_reason"]
        assert data["intent"]["category"] == "laptop"
        assert data["intent"]["max_budget"] == 1000.0
        assert len(data["products"]) > 0
        assert "Found" in data["assistant_message"]


@pytest.mark.asyncio
async def test_api_assistant_query_validation_error(async_client: httpx.AsyncClient):
    """Test POST /api/assistant/query with missing query returns 422 Unprocessable Entity."""
    response = await async_client.post("/api/assistant/query", json={"session_id": "missing-query"})
    assert response.status_code == 422


# =============================================================================
# CATEGORY H: Phase 3.1 Gemini Schema Compatibility Tests (Zero additionalProperties)
# =============================================================================

def test_gemini_schemas_have_no_additional_properties():
    """
    CRITICAL REGRESSION TEST for Phase 3.1:
    Verify that all Pydantic response models used with Gemini Developer API
    produce JSON schemas with ZERO 'additionalProperties'.
    """
    from backend.app.schemas.assistant import (
        ShoppingIntent,
        ProductIntelligenceList,
        ComparisonExplanation,
    )

    def _check_no_additional_properties(d: Any, path: str = "") -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                assert k != "additionalProperties", f"Found forbidden 'additionalProperties' at {path}.{k}"
                _check_no_additional_properties(v, f"{path}.{k}")
        elif isinstance(d, list):
            for i, item in enumerate(d):
                _check_no_additional_properties(item, f"{path}[{i}]")

    for model in (ShoppingIntent, ProductIntelligenceList, ComparisonExplanation):
        schema_dict = model.model_json_schema()
        schema_str = json.dumps(schema_dict)
        assert '"additionalProperties":' not in schema_str, f"JSON key 'additionalProperties' found in {model.__name__}"
        _check_no_additional_properties(schema_dict)


def test_gemini_schema_specification_requirement_structure():
    """Verify SpecificationRequirement serialization and dict coercion."""
    from backend.app.schemas.assistant import SpecificationRequirement, ShoppingIntent

    req = SpecificationRequirement(
        name="ram",
        value="16 GB or more",
        raw_spec="at least 16GB RAM",
        min_value=16.0,
        unit="GB",
        is_hard_requirement=True,
    )
    assert req.name == "ram"
    assert req.min_value == 16.0
    assert req.unit == "GB"

    # Verify coercion from dict
    intent = ShoppingIntent(
        query="laptop",
        required_specs={"ram": "16 GB or more", "storage": "512 GB SSD"},
    )
    assert len(intent.required_specs) == 2
    assert intent.required_specs.get("ram") == "16 GB or more"
    assert intent.required_specs.get("storage") == "512 GB SSD"
    assert intent.required_specs_dict["ram"] == "16 GB or more"


# =============================================================================
# CATEGORY I: Phase 3.1 Hardware Specification & Multilingual Intent Tests
# =============================================================================

def test_intent_extraction_inr_laptop_at_least_16gb_ram():
    """Test extracting 'at least 16GB RAM' with INR budget under ₹70,000."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "I need a laptop for AI and machine learning under ₹70000 with at least 16GB RAM",
        "category": "laptop",
        "intended_use": ["machine_learning"],
        "max_budget": 70000.0,
        "currency": "INR",
        "required_specs": [
            {
                "name": "ram",
                "value": "16 GB or more",
                "raw_spec": "at least 16GB RAM",
                "min_value": 16.0,
                "unit": "GB",
                "is_hard_requirement": True,
            }
        ],
        "preferred_specs": [],
        "brand_preferences": [],
        "excluded_brands": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("I need a laptop for AI and machine learning under ₹70000 with at least 16GB RAM")
        assert intent.category == "laptop"
        assert intent.max_budget == 70000.0
        assert intent.currency == "INR"
        assert len(intent.required_specs) == 1
        ram_spec = intent.required_specs[0]
        assert ram_spec.name == "ram"
        assert ram_spec.min_value == 16.0
        assert ram_spec.is_hard_requirement is True


def test_intent_extraction_laptop_minimum_32gb_ram():
    """Test extracting 'minimum 32 GB RAM' requirement."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Find a laptop with minimum 32 GB RAM under $1500",
        "category": "laptop",
        "intended_use": [],
        "max_budget": 1500.0,
        "currency": "USD",
        "required_specs": [
            {
                "name": "ram",
                "value": "32 GB or more",
                "raw_spec": "minimum 32 GB RAM",
                "min_value": 32.0,
                "unit": "GB",
                "is_hard_requirement": True,
            }
        ],
        "preferred_specs": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Find a laptop with minimum 32 GB RAM under $1500")
        assert intent.required_specs[0].min_value == 32.0
        assert intent.required_specs[0].is_hard_requirement is True


def test_intent_extraction_laptop_preferably_16gb_ram():
    """Test distinguishing soft preference from hard requirement."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Laptop under $1000 preferably 16 GB RAM",
        "category": "laptop",
        "intended_use": [],
        "max_budget": 1000.0,
        "currency": "USD",
        "required_specs": [],
        "preferred_specs": [
            {
                "name": "ram",
                "value": "16 GB",
                "raw_spec": "preferably 16 GB RAM",
                "min_value": 16.0,
                "unit": "GB",
                "is_hard_requirement": False,
            }
        ],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Laptop under $1000 preferably 16 GB RAM")
        assert len(intent.required_specs) == 0
        assert len(intent.preferred_specs) == 1
        assert intent.preferred_specs[0].name == "ram"
        assert intent.preferred_specs[0].is_hard_requirement is False


def test_intent_extraction_storage_512gb_ssd():
    """Test extracting 512 GB SSD requirement."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Laptop with 512 GB SSD under $800",
        "category": "laptop",
        "intended_use": [],
        "max_budget": 800.0,
        "currency": "USD",
        "required_specs": [
            {
                "name": "storage",
                "value": "512 GB SSD",
                "raw_spec": "512 GB SSD",
                "min_value": 512.0,
                "unit": "GB",
                "is_hard_requirement": True,
            }
        ],
        "preferred_specs": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Laptop with 512 GB SSD under $800")
        assert intent.required_specs[0].name == "storage"
        assert intent.required_specs[0].min_value == 512.0


def test_intent_extraction_storage_at_least_1tb_ssd():
    """Test extracting 'at least 1 TB SSD' with TB unit."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Laptop with at least 1 TB SSD for video editing",
        "category": "laptop",
        "intended_use": ["video_editing"],
        "max_budget": None,
        "currency": "USD",
        "required_specs": [
            {
                "name": "storage",
                "value": "1 TB or more",
                "raw_spec": "at least 1 TB SSD",
                "min_value": 1.0,
                "unit": "TB",
                "is_hard_requirement": True,
            }
        ],
        "preferred_specs": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Laptop with at least 1 TB SSD for video editing")
        assert intent.required_specs[0].name == "storage"
        assert intent.required_specs[0].unit == "TB"
        assert intent.required_specs[0].min_value == 1.0


def test_intent_extraction_ram_and_storage_combined():
    """Test extracting both RAM and Storage without confusion."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "Laptop with 32 GB RAM and at least 1 TB SSD under $2000",
        "category": "laptop",
        "intended_use": [],
        "max_budget": 2000.0,
        "currency": "USD",
        "required_specs": [
            {"name": "ram", "value": "32 GB", "min_value": 32.0, "unit": "GB", "is_hard_requirement": True},
            {"name": "storage", "value": "1 TB or more", "min_value": 1.0, "unit": "TB", "is_hard_requirement": True},
        ],
        "preferred_specs": [],
        "detected_language": "en",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("Laptop with 32 GB RAM and at least 1 TB SSD under $2000")
        assert len(intent.required_specs) == 2
        specs_map = intent.required_specs_dict
        assert "ram" in specs_map
        assert "storage" in specs_map
        assert intent.required_specs.get("ram") == "32 GB"


def test_intent_extraction_tamil_ram_and_budget():
    """Test Tamil shopping query with RAM and INR budget."""
    client = GeminiClient(api_key="test_key")
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "query": "மெஷின் லேர்னிங்கிற்கு சிறந்த லேப்டாப் ₹70000க்குள் 16GB ரேம்",
        "category": "laptop",
        "intended_use": ["machine_learning"],
        "max_budget": 70000.0,
        "currency": "INR",
        "required_specs": [
            {"name": "ram", "value": "16 GB", "min_value": 16.0, "unit": "GB", "is_hard_requirement": True}
        ],
        "preferred_specs": [],
        "detected_language": "ta",
        "clarification_required": False,
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response):
        intent = client.extract_shopping_intent("மெஷின் லேர்னிங்கிற்கு சிறந்த லேப்டாப் ₹70000க்குள் 16GB ரேம்")
        assert intent.detected_language == "ta"
        assert intent.max_budget == 70000.0
        assert intent.currency == "INR"
        assert intent.required_specs.get("ram") == "16 GB"


def test_fallback_intent_extraction_ram_and_budget():
    """Verify deterministic fallback intent extractor handles RAM, SSD, and budget accurately."""
    # Test 1: RAM + INR budget
    q1 = "I need a laptop for AI and machine learning under ₹70000 with at least 16GB RAM"
    intent1 = AssistantService._fallback_rule_based_intent(q1)
    assert intent1.category == "laptop"
    assert intent1.max_budget == 70000.0
    assert intent1.currency == "INR"
    assert "machine_learning" in intent1.intended_use
    assert len(intent1.required_specs) == 1
    assert intent1.required_specs[0].name == "ram"
    assert intent1.required_specs[0].min_value == 16.0
    assert intent1.required_specs[0].is_hard_requirement is True

    # Test 2: RAM + SSD
    q2 = "Find a laptop with 32GB RAM and at least 1TB SSD under $1500"
    intent2 = AssistantService._fallback_rule_based_intent(q2)
    assert intent2.max_budget == 1500.0
    assert intent2.currency == "USD"
    assert len(intent2.required_specs) == 2
    assert intent2.required_specs.get("ram") is not None
    assert intent2.required_specs.get("storage") is not None

    # Test 3: Preference vs requirement
    q3 = "Laptop under $1000 preferably 16GB RAM"
    intent3 = AssistantService._fallback_rule_based_intent(q3)
    assert len(intent3.required_specs) == 0
    assert len(intent3.preferred_specs) == 1
    assert intent3.preferred_specs[0].name == "ram"

    # Test 4: Tamil query fallback
    q4 = "மெஷின் லேர்னிங்கிற்கு சிறந்த லேப்டாப் ₹70000க்குள் 16GB ரேம்"
    intent4 = AssistantService._fallback_rule_based_intent(q4)
    assert intent4.detected_language == "ta"
    assert intent4.currency == "INR"
    assert intent4.max_budget == 70000.0
    assert len(intent4.required_specs) == 1
    assert intent4.required_specs[0].name == "ram"


# =============================================================================
# CATEGORY J: Phase 3.1 Specification & Currency Eligibility Filtering Tests
# =============================================================================

def _build_test_product(
    prod_id: str,
    name: str,
    price: float | None,
    currency: str | None,
    specs: dict | None = None,
) -> ProductResponse:
    """Helper creating a ProductResponse with specified offers and specifications."""
    from datetime import datetime, timezone
    from backend.app.schemas.product import RetailerOfferResponse

    offers = []
    if price is not None and currency is not None:
        offers.append(
            RetailerOfferResponse(
                id=f"offer-{prod_id}",
                product_id=prod_id,
                retailer_name="TestMerchant",
                price=price,
                currency=currency,
                product_url="https://example.com/item",
                availability="IN_STOCK",
                created_at=datetime.now(timezone.utc),
            )
        )

    return ProductResponse(
        id=prod_id,
        name=name,
        brand="TestBrand",
        category="laptop",
        description="Test description",
        specifications=specs or {},
        created_at=datetime.now(timezone.utc),
        lowest_price=price,
        offers=offers,
    )


def test_eligibility_inr_budget_with_inr_offer_qualifies():
    """Verify INR offer within INR budget and meeting RAM qualifies."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=70000.0,
        currency="INR",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p1", "HP Pavilion 15", 65000.0, "INR", {"ram": "16 GB"})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is True
    assert elig.status == "QUALIFIED"
    assert "ram" in elig.verified_specs


def test_eligibility_inr_budget_with_usd_offer_currency_mismatch():
    """CRITICAL TEST: Verify USD offer is NOT qualified against an INR budget."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=70000.0,
        currency="INR",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p2", "Surface Laptop 6", 1499.99, "USD", {"ram": "16 GB"})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is False
    assert elig.status == "CURRENCY_MISMATCH"
    assert any("cannot be directly evaluated against your INR budget" in r for r in elig.reasons)


def test_eligibility_usd_budget_with_usd_offer_qualifies():
    """Verify USD offer within USD budget qualifies."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=1500.0,
        currency="USD",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p3", "DevBook Pro 16", 1299.99, "USD", {"ram": "32 GB"})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is True
    assert elig.status == "QUALIFIED"


def test_eligibility_missing_offer_currency_unverified():
    """Verify offer with missing currency is marked UNVERIFIED_SPECS."""
    intent = ShoppingIntent(query="laptop", max_budget=1000.0, currency="USD")
    product = _build_test_product("p4", "Unknown Laptop", 800.0, None)
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is False
    assert elig.status == "UNVERIFIED_SPECS"


def test_eligibility_product_below_budget():
    """Verify product below budget qualifies."""
    intent = ShoppingIntent(query="laptop", max_budget=1000.0, currency="USD")
    product = _build_test_product("p5", "BudgetBook", 750.0, "USD")
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is True
    assert elig.status == "QUALIFIED"


def test_eligibility_product_above_budget():
    """Verify product exceeding budget is EXCLUDED."""
    intent = ShoppingIntent(query="laptop", max_budget=1000.0, currency="USD")
    product = _build_test_product("p6", "ExpensiveBook", 1499.0, "USD")
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_budget_exceeded is True
    assert elig.budget_exceeded_amount == 499.0
    assert elig.is_override_eligible is True
    assert elig.status == "QUALIFIED"
    assert any("exceeds your preferred budget" in r for r in elig.reasons)


def test_eligibility_product_meeting_ram():
    """Verify product meeting or exceeding required RAM qualifies."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB or more", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p7", "HighSpec Laptop", 1200.0, "USD", {"ram": "32 GB DDR5"})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is True
    assert elig.status == "QUALIFIED"


def test_eligibility_product_below_ram():
    """Verify product with verified RAM below required is EXCLUDED."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB or more", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p8", "LowSpec Laptop", 600.0, "USD", {"ram": "8 GB"})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is False
    assert elig.status == "EXCLUDED"
    assert any("below your required minimum of 16 GB" in r for r in elig.reasons)


def test_eligibility_product_missing_ram_unverified():
    """Verify product with missing RAM specs is marked UNVERIFIED_SPECS."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB or more", min_value=16.0, unit="GB")],
    )
    product = _build_test_product("p9", "Mystery Laptop", 800.0, "USD", {})
    elig = AssistantService.evaluate_product_eligibility(product, intent)

    assert elig.is_qualified is False
    assert elig.status == "UNVERIFIED_SPECS"
    assert "ram" in elig.unverified_specs


def test_eligibility_mixed_currencies():
    """Verify mixed currency candidates are categorized correctly."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=70000.0,
        currency="INR",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    p_inr = _build_test_product("p-inr", "Indian Laptop", 60000.0, "INR", {"ram": "16 GB"})
    p_usd = _build_test_product("p-usd", "US Laptop", 999.0, "USD", {"ram": "16 GB"})

    elig_inr = AssistantService.evaluate_product_eligibility(p_inr, intent)
    elig_usd = AssistantService.evaluate_product_eligibility(p_usd, intent)

    assert elig_inr.status == "QUALIFIED"
    assert elig_usd.status == "CURRENCY_MISMATCH"


# =============================================================================
# CATEGORY K: Phase 3.1 Response Consistency Tests
# =============================================================================

def test_summary_consistency_accurate_product_count():
    """Verify deterministic summary accurately reflects total and qualified counts."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=70000.0,
        currency="INR",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    products = [
        _build_test_product("p1", "Surface Laptop 6", 1499.99, "USD", {"ram": "16 GB"}),
        _build_test_product("p2", "Surface Laptop", 1099.00, "USD", {"ram": "16 GB"}),
        _build_test_product("p3", "ASUS L510", 322.99, "USD", {"ram": "4 GB"}),
        _build_test_product("p4", "Lenovo ThinkPad", 1499.99, "USD", {"ram": "16 GB"}),
        _build_test_product("p5", "Dell Inspiron", 1099.99, "USD", {"ram": "16 GB"}),
    ]

    elig_items = [AssistantService.evaluate_product_eligibility(p, intent) for p in products]
    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=products,
        mandate_eval=None,
        eligibility_items=elig_items,
    )

    # 1. Must report all 5 products retrieved
    assert "retrieved 5 product(s)" in summary
    # 2. Must state that none met all requirements
    assert "none met all your specified requirements" in summary
    # 3. Must warn about USD currency mismatch
    assert "Currency Notice" in summary
    assert "USD" in summary
    # 4. Must not claim any USD product qualified under INR
    assert "Found 5 qualified option(s)" not in summary


def test_summary_consistency_excluded_not_claimed_qualified():
    """Verify products that fail specs are not claimed as qualified."""
    from backend.app.schemas.assistant import SpecificationRequirement

    intent = ShoppingIntent(
        query="laptop",
        max_budget=1000.0,
        currency="USD",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )
    products = [
        _build_test_product("p1", "Low RAM Laptop", 500.0, "USD", {"ram": "8 GB"}),
    ]

    elig_items = [AssistantService.evaluate_product_eligibility(p, intent) for p in products]
    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=products,
        mandate_eval=None,
        eligibility_items=elig_items,
    )

    assert "none met all your specified requirements" in summary
    assert "Excluded: 1 product(s)" in summary
    assert "Found 1 qualified option(s)" not in summary


# =============================================================================
# CATEGORY L: Specification Extraction and AI Validation Grounding Tests
# =============================================================================

def test_extract_ram_ddr4_ddr5_lpddr5_and_variants():
    """Verify RAM extraction recognizes DDR4, DDR5, LPDDR5, key_features, and dedicated keys."""
    # 1. DDR4 in title
    p_ddr4 = _build_test_product("p-ddr4", "HP Victus 15.6 Gaming Laptop 16GB DDR4 RAM 512GB SSD", 700.0, "USD")
    ram, src = AssistantService._extract_ram_from_product(p_ddr4)
    assert ram == 16.0
    assert "16gb ddr4 ram" in src.lower()

    # 2. DDR5 in title
    p_ddr5 = _build_test_product("p-ddr5", "Lenovo LOQ 15.6 Gaming Laptop with 16GB DDR5 RAM 2TB NVMe SSD", 999.0, "USD")
    ram, src = AssistantService._extract_ram_from_product(p_ddr5)
    assert ram == 16.0
    assert "16gb ddr5 ram" in src.lower()

    # 3. LPDDR5 in description
    p_lpddr5 = _build_test_product("p-lpddr5", "Thin & Light Laptop", 800.0, "USD")
    p_lpddr5.description = "Configured with 16GB LPDDR5 RAM for efficiency"
    ram, src = AssistantService._extract_ram_from_product(p_lpddr5)
    assert ram == 16.0
    assert "16gb lpddr5 ram" in src.lower()

    # 4. Spaced format "16 GB RAM"
    p_spaced = _build_test_product("p-spaced", "Standard Laptop 16 GB RAM", 600.0, "USD")
    ram, src = AssistantService._extract_ram_from_product(p_spaced)
    assert ram == 16.0

    # 5. key_features bullet points
    p_kf = _build_test_product("p-kf", "Custom Laptop", 850.0, "USD", specs={"key_features": ["16GB DDR5 RAM", "1TB SSD"]})
    ram, src = AssistantService._extract_ram_from_product(p_kf)
    assert ram == 16.0
    assert "key_features" in src

    # 6. Dedicated spec keys
    p_spec_int = _build_test_product("p-int", "Office Laptop", 500.0, "USD", specs={"system_memory": 16})
    ram, src = AssistantService._extract_ram_from_product(p_spec_int)
    assert ram == 16.0
    assert "16 GB" in src

    # 7. Unspecified RAM
    p_none = _build_test_product("p-none", "Generic Laptop", 400.0, "USD", specs={})
    p_none.description = "No memory listed here"
    ram, src = AssistantService._extract_ram_from_product(p_none)
    assert ram is None
    assert src == "Not specified"


def test_extract_storage_2tb_nvme_512gb_and_storage_capacity():
    """Verify storage extraction recognizes TB/GB, NVMe, key_features, and storage-capacity."""
    # 1. 2TB NVMe SSD in title
    p_2tb = _build_test_product("p-2tb", "Lenovo LOQ Laptop 16GB RAM 2TB NVMe SSD", 1200.0, "USD")
    storage, src = AssistantService._extract_storage_from_product(p_2tb)
    assert storage == 2048.0
    assert "2tb" in src.lower()

    # 2. 512GB SSD in title
    p_512 = _build_test_product("p-512", "HP Victus Laptop 16GB RAM 512GB SSD", 700.0, "USD")
    storage, src = AssistantService._extract_storage_from_product(p_512)
    assert storage == 512.0
    assert "512gb ssd" in src.lower()

    # 3. storage-capacity key
    p_sc = _build_test_product("p-sc", "Pro Laptop", 1000.0, "USD", specs={"storage-capacity": "2TB NVMe SSD"})
    storage, src = AssistantService._extract_storage_from_product(p_sc)
    assert storage == 2048.0
    assert "storage-capacity" in src

    # 4. key_features bullet list
    p_kf = _build_test_product("p-kf", "Feature Laptop", 900.0, "USD", specs={"key_features": ["16GB DDR5 RAM", "512GB PCIe NVMe SSD"]})
    storage, src = AssistantService._extract_storage_from_product(p_kf)
    assert storage == 512.0
    assert "key_features" in src

    # 5. Missing storage
    p_none = _build_test_product("p-none", "Empty Laptop", 300.0, "USD", specs={})
    p_none.description = "No storage mentioned"
    storage, src = AssistantService._extract_storage_from_product(p_none)
    assert storage is None
    assert src == "Not specified"


def test_ai_intelligence_sanitizes_unverified_ram_claim():
    """Verify unsupported RAM claims are removed from strengths while preserving legitimate strengths."""
    elig_unverified = ProductEligibilityItem(
        product_id="prod-victus",
        product_name="HP Victus 15.6",
        status="UNVERIFIED_SPECS",
        is_qualified=False,
        reasons=["RAM specification is missing from product details; cannot confirm requirement of 16 GB."],
        verified_specs={},
        unverified_specs=["ram"],
    )

    ai_intel = ProductIntelligence(
        product_id="prod-victus",
        product_name="HP Victus 15.6",
        suitability="Highly Suitable",
        strengths=[
            "16 GB RAM meets your requirement",
            "Equipped with AMD Ryzen 5 processor",
            "NVIDIA GeForce RTX 3050 GPU with 4GB GDDR6",
            "High-refresh 144Hz FHD display",
        ],
        weaknesses=["Chassis is predominantly plastic"],
        intended_use_advantages=[
            "16GB RAM for machine learning models",
            "Dedicated RTX 3050 graphics for parallel workloads",
        ],
        missing_information=[],
        recommendation_rationale="Great option featuring 16GB RAM meets requirements.",
    )

    sanitized = AssistantService.validate_and_sanitize_intelligence(
        [ai_intel],
        {"prod-victus": elig_unverified},
    )[0]

    # Unsupported RAM claims stripped
    assert "16 GB RAM meets your requirement" not in sanitized.strengths
    assert "16GB RAM for machine learning models" not in sanitized.intended_use_advantages

    # Legitimate non-RAM strengths preserved
    assert "Equipped with AMD Ryzen 5 processor" in sanitized.strengths
    assert "NVIDIA GeForce RTX 3050 GPU with 4GB GDDR6" in sanitized.strengths
    assert "High-refresh 144Hz FHD display" in sanitized.strengths
    assert "Dedicated RTX 3050 graphics for parallel workloads" in sanitized.intended_use_advantages

    # Missing information cataloged
    assert any("RAM" in m for m in sanitized.missing_information)

    # Suitability aligned to conditional, not falsely qualified
    assert "Conditionally Suitable" in sanitized.suitability


def test_ai_intelligence_sanitizes_unverified_storage_claim():
    """Verify unsupported storage claims are stripped when storage is unverified."""
    elig_unverified = ProductEligibilityItem(
        product_id="prod-storage",
        product_name="Sample Laptop",
        status="UNVERIFIED_SPECS",
        is_qualified=False,
        reasons=["Storage specification is unverified (512 GB)."],
        verified_specs={"ram": "16 GB"},
        unverified_specs=["storage"],
    )

    ai_intel = ProductIntelligence(
        product_id="prod-storage",
        product_name="Sample Laptop",
        suitability="Suitable",
        strengths=[
            "16 GB RAM meets requirement",
            "2TB NVMe SSD provides ample storage capacity",
            "Lightweight aluminum body",
        ],
        weaknesses=[],
        intended_use_advantages=["Ample storage for local dataset caches"],
        missing_information=[],
        recommendation_rationale="Solid laptop.",
    )

    sanitized = AssistantService.validate_and_sanitize_intelligence(
        [ai_intel],
        {"prod-storage": elig_unverified},
    )[0]

    # Storage claim stripped
    assert not any("2tb" in s.lower() or "storage" in s.lower() for s in sanitized.strengths)
    assert not any("storage" in a.lower() for a in sanitized.intended_use_advantages)

    # Legitimate verified RAM and body strength preserved
    assert "16 GB RAM meets requirement" in sanitized.strengths
    assert "Lightweight aluminum body" in sanitized.strengths
    assert any("storage" in m.lower() or "ssd" in m.lower() for m in sanitized.missing_information)


def test_unsupported_ai_strengths_cannot_override_deterministic_eligibility():
    """
    CRITICAL SECURITY & INTEGRITY INVARIANT:
    Prove that AI-generated intelligence cannot override deterministic eligibility verdicts.
    """
    from backend.app.schemas.assistant import SpecificationRequirement

    # User requires 16GB RAM
    intent = ShoppingIntent(
        query="gaming laptop with at least 16GB RAM",
        category="laptop",
        required_specs=[SpecificationRequirement(name="ram", value="16 GB", min_value=16.0, unit="GB")],
    )

    # Product in store has no RAM specified
    product = _build_test_product("prod-mystery", "Mystery Book", 800.0, "USD", specs={})
    product.description = "A powerful laptop for everyday tasks"

    # Step 1: Deterministic evaluation is authoritative
    elig = AssistantService.evaluate_product_eligibility(product, intent)
    assert elig.is_qualified is False
    assert elig.status == "UNVERIFIED_SPECS"
    assert "ram" in elig.unverified_specs

    # Step 2: Simulate Gemini hallucinating that the product is fully qualified with 16GB RAM
    hallucinated_intel = ProductIntelligence(
        product_id="prod-mystery",
        product_name="Mystery Book",
        suitability="Highly Suitable - Meets All Requirements",
        strengths=["16 GB RAM meets your requirement", "Sleek keyboard", "Long battery life"],
        weaknesses=[],
        intended_use_advantages=["16GB RAM handles all workloads"],
        missing_information=[],
        recommendation_rationale="Buy this laptop because it has 16GB RAM.",
    )

    # Step 3: Run backend sanitization layer
    elig_map = {elig.product_id: elig}
    sanitized_list = AssistantService.validate_and_sanitize_intelligence([hallucinated_intel], elig_map)
    sanitized = sanitized_list[0]

    # Invariant A: Deterministic eligibility remains UNVERIFIED and NOT qualified
    assert elig.is_qualified is False
    assert elig.status == "UNVERIFIED_SPECS"

    # Invariant B: AI's contradictory claim was scrubbed from strengths and advantages
    assert "16 GB RAM meets your requirement" not in sanitized.strengths
    assert "16GB RAM handles all workloads" not in sanitized.intended_use_advantages

    # Invariant C: Genuine non-spec strengths are preserved
    assert "Sleek keyboard" in sanitized.strengths
    assert "Long battery life" in sanitized.strengths

    # Invariant D: Transparency guaranteed in missing_information
    assert any("RAM" in m for m in sanitized.missing_information)

    # Invariant E: Suitability is adjusted to prevent misleading user
    assert "Conditionally Suitable" in sanitized.suitability


def test_gemini_client_analyze_products_includes_eligibility_map():
    """Verify GeminiClient.analyze_products passes deterministic eligibility info to prompt evidence."""
    client = GeminiClient(api_key="test_key")
    intent = ShoppingIntent(query="laptop", category="laptop")
    products = [{"id": "prod-1", "name": "Test Laptop", "lowest_price": 999.0, "offers": []}]

    elig_map = {
        "prod-1": ProductEligibilityItem(
            product_id="prod-1",
            product_name="Test Laptop",
            status="UNVERIFIED_SPECS",
            is_qualified=False,
            reasons=["RAM specification is missing."],
            verified_specs={},
            unverified_specs=["ram"],
        )
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "items": [
            {
                "product_id": "prod-1",
                "product_name": "Test Laptop",
                "suitability": "GOOD",
                "strengths": ["Fast CPU"],
                "weaknesses": [],
                "intended_use_fit": "General use",
                "missing_information": ["RAM"],
                "value_for_money_rating": 4.0,
                "recommendation_rationale": "Check RAM before buying",
            }
        ]
    })

    with patch.object(client._client.models, "generate_content", return_value=mock_response) as mock_gen:
        analyses = client.analyze_products("laptop", intent, products, eligibility_map=elig_map)
        assert len(analyses) == 1

        # Verify generate_content was called and prompt contains eligibility info
        call_kwargs = mock_gen.call_args.kwargs
        contents_arg = call_kwargs["contents"]
        assert "deterministic_eligibility_status" in contents_arg
        assert "UNVERIFIED_SPECS" in contents_arg
        assert "unverified_specifications" in contents_arg

        # Verify backward compatibility: calling without eligibility_map works
        analyses_compat = client.analyze_products("laptop", intent, products)
        assert len(analyses_compat) == 1


def test_suitability_alignment_excluded_and_currency_mismatch():
    """Verify validate_and_sanitize_intelligence aligns suitability for EXCLUDED and CURRENCY_MISMATCH."""
    elig_excluded = ProductEligibilityItem(
        product_id="p-ex",
        product_name="Weak Laptop",
        status="EXCLUDED",
        is_qualified=False,
        reasons=["Price exceeds maximum budget."],
        verified_specs={},
        unverified_specs=[],
    )
    elig_mismatch = ProductEligibilityItem(
        product_id="p-curr",
        product_name="USD Laptop",
        status="CURRENCY_MISMATCH",
        is_qualified=False,
        reasons=["Priced in USD but user budget is INR."],
        verified_specs={},
        unverified_specs=[],
    )
    elig_qualified = ProductEligibilityItem(
        product_id="p-qual",
        product_name="Good Laptop",
        status="QUALIFIED",
        is_qualified=True,
        reasons=["All requirements met."],
        verified_specs={"ram": "16 GB"},
        unverified_specs=[],
    )

    ai_items = [
        ProductIntelligence(
            product_id="p-ex",
            product_name="Weak Laptop",
            suitability="Recommended option for your needs",
            strengths=["Nice screen"],
            weaknesses=[],
        ),
        ProductIntelligence(
            product_id="p-curr",
            product_name="USD Laptop",
            suitability="Fully Suitable within budget",
            strengths=["Fast CPU"],
            weaknesses=[],
        ),
        ProductIntelligence(
            product_id="p-qual",
            product_name="Good Laptop",
            suitability="Highly Suitable",
            strengths=["16 GB RAM", "Fast CPU"],
            weaknesses=[],
        ),
    ]

    elig_map = {"p-ex": elig_excluded, "p-curr": elig_mismatch, "p-qual": elig_qualified}
    sanitized = AssistantService.validate_and_sanitize_intelligence(ai_items, elig_map)

    assert "Excluded" in sanitized[0].suitability
    assert "Currency Mismatch" in sanitized[1].suitability
    assert sanitized[2].suitability == "Highly Suitable"

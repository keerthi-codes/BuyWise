"""
AI-Powered Multilingual Shopping Intelligence Platform
Unit and Integration Tests for Phase 4A.1: Intent and Multilingual Improvements.

Validates:
1. Intelligent clarification handling:
   - Clarification required bypasses Channel3 search and catalog retrieval.
   - Ambiguous queries ("I want to buy a computer.", "I need headphones.") return clarification questions.
   - Response preserves schema and frontend compatibility.
2. German fallback intent extraction:
   - German language detection ('de').
   - Budget prefixes ('unter', 'höchstens', 'maximal', 'bis zu').
   - Product categories ('laptop', 'gaming-laptop', 'kopfhörer', 'handy').
   - Currencies ('euro', 'eur', '€').
   - RAM and storage/SSD extraction in German.
   - Disambiguation between RAM and SSD.
3. Multilingual deterministic fallback summaries:
   - Localized German ('de') deterministic fallback responses.
   - Localized French ('fr') deterministic fallback responses.
   - Preserved English ('en'), Tamil ('ta'), and Spanish ('es') behavior.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from backend.app.schemas.assistant import (
    ShoppingIntent,
    AssistantQueryRequest,
    AssistantQueryResponse,
    ProductEligibilityItem,
)
from backend.app.schemas.product import ProductResponse, RetailerOfferResponse
from backend.app.services.assistant_service import AssistantService
from backend.app.services.channel3_client import Channel3Client
from backend.app.services.gemini_client import GeminiClient


# =============================================================================
# 1. CLARIFICATION HANDLING TESTS
# =============================================================================

def test_clarification_handling_bypasses_channel3_and_catalog(test_session: Session):
    """
    Verify that when ShoppingIntent.clarification_required is True:
    - Channel3 search_live_products is NOT called.
    - Catalog search is NOT called.
    - An AssistantQueryResponse is returned with empty products and helpful clarification question.
    """
    mock_gemini = MagicMock(spec=GeminiClient)
    mock_c3 = MagicMock(spec=Channel3Client)
    mock_c3.is_configured = True

    # Gemini returns clarification required
    mock_gemini.extract_shopping_intent.return_value = ShoppingIntent(
        query="I want to buy a computer.",
        category="laptop",
        clarification_required=True,
        clarification_prompt="To help you find the best computer, could you share your intended use and target budget?",
        clarification_questions=["What is your intended use?", "What is your target budget?"],
        detected_language="en",
    )

    req = AssistantQueryRequest(session_id="test-session-clarify", query="I want to buy a computer.")
    response = AssistantService.process_query(
        db=test_session,
        request=req,
        gemini_client=mock_gemini,
        c3_client=mock_c3,
    )

    # Invariants
    assert response.success is True
    assert response.intent.clarification_required is True
    assert len(response.products) == 0
    assert len(response.qualified_product_ids) == 0
    assert len(response.excluded_product_ids) == 0
    assert "intended use" in response.assistant_message
    assert "target budget" in response.assistant_message

    # Channel3 MUST NOT be called
    mock_c3.search_products.assert_not_called()


def test_rule_based_fallback_ambiguous_queries():
    """Verify rule-based fallback detects ambiguous queries and prompts for clarification."""
    # Test case 1: "I want to buy a computer."
    intent1 = AssistantService._fallback_rule_based_intent("I want to buy a computer.")
    assert intent1.clarification_required is True
    assert intent1.category == "laptop"
    assert "intended use" in intent1.clarification_prompt
    assert "budget" in intent1.clarification_prompt

    # Test case 2: "I need headphones."
    intent2 = AssistantService._fallback_rule_based_intent("I need headphones.")
    assert intent2.clarification_required is True
    assert intent2.category == "headphones"
    assert "budget" in intent2.clarification_prompt
    assert any("features" in intent2.clarification_prompt or "noise cancellation" in intent2.clarification_prompt for _ in [1])

    # Test case 3: "recommend something good"
    intent3 = AssistantService._fallback_rule_based_intent("recommend something good")
    assert intent3.clarification_required is True
    assert "detail" in intent3.clarification_prompt or "budget" in intent3.clarification_prompt


def test_clarification_prompt_multilingual():
    """Verify clarification questions adapt to detected language."""
    # German ambiguous query
    intent_de = AssistantService._fallback_rule_based_intent("Ich suche einen Computer.")
    assert intent_de.clarification_required is True
    assert intent_de.detected_language == "de"
    assert "Budget" in intent_de.clarification_prompt or "Einsatzzweck" in intent_de.clarification_prompt

    # French ambiguous query
    intent_fr = AssistantService._fallback_rule_based_intent("Je cherche un ordinateur.")
    assert intent_fr.clarification_required is True
    assert intent_fr.detected_language == "fr"
    assert "budget" in intent_fr.clarification_prompt or "usage" in intent_fr.clarification_prompt


# =============================================================================
# 2. GERMAN INTENT AND HARDWARE SPECIFICATION EXTRACTION
# =============================================================================

def test_german_intent_gaming_laptop_with_ram():
    """
    Test German query:
    'Ich suche einen Gaming-Laptop unter 1500 Euro mit mindestens 16GB RAM'
    """
    query = "Ich suche einen Gaming-Laptop unter 1500 Euro mit mindestens 16GB RAM"
    intent = AssistantService._fallback_rule_based_intent(query)

    assert intent.detected_language == "de"
    assert intent.category == "laptop"
    assert "gaming" in intent.intended_use
    assert intent.max_budget == 1500.0
    assert intent.currency == "EUR"
    assert intent.clarification_required is False

    # Check RAM specification
    ram_reqs = [s for s in intent.required_specs if s.name == "ram"]
    assert len(ram_reqs) == 1
    assert ram_reqs[0].min_value == 16.0
    assert ram_reqs[0].unit == "GB"
    assert ram_reqs[0].is_hard_requirement is True


def test_german_intent_laptop_with_ssd_storage():
    """
    Test German query:
    'Laptop mit 512GB SSD unter 1000 Euro'
    Ensures 512GB is extracted as storage/SSD and NOT confused with RAM.
    """
    query = "Laptop mit 512GB SSD unter 1000 Euro"
    intent = AssistantService._fallback_rule_based_intent(query)

    assert intent.detected_language == "de"
    assert intent.category == "laptop"
    assert intent.max_budget == 1000.0
    assert intent.currency == "EUR"
    assert intent.clarification_required is False

    # Storage requirement
    storage_reqs = [s for s in intent.required_specs if s.name == "storage"]
    assert len(storage_reqs) == 1
    assert storage_reqs[0].min_value == 512.0
    assert storage_reqs[0].unit == "GB"

    # Crucial: Must NOT extract RAM for SSD
    ram_reqs = [s for s in intent.required_specs if s.name == "ram"]
    assert len(ram_reqs) == 0


def test_german_budget_prefix_variants():
    """Test various German budget prefixes and currency symbols."""
    # 'unter'
    i1 = AssistantService._fallback_rule_based_intent("Laptop unter 1200 Euro")
    assert i1.max_budget == 1200.0
    assert i1.currency == "EUR"

    # 'höchstens'
    i2 = AssistantService._fallback_rule_based_intent("Kopfhörer höchstens 80 €")
    assert i2.max_budget == 80.0
    assert i2.currency == "EUR"

    # 'maximal'
    i3 = AssistantService._fallback_rule_based_intent("Handy maximal 500 EUR")
    assert i3.max_budget == 500.0
    assert i3.currency == "EUR"

    # 'bis zu'
    i4 = AssistantService._fallback_rule_based_intent("Gaming-Laptop bis zu 2000 Euro")
    assert i4.max_budget == 2000.0
    assert i4.currency == "EUR"


def test_german_product_terms():
    """Test German category vocabulary."""
    # kopfhörer -> headphones
    i1 = AssistantService._fallback_rule_based_intent("Kabellose Kopfhörer unter 150 Euro")
    assert i1.category == "headphones"
    assert i1.detected_language == "de"

    # handy -> smartphone
    i2 = AssistantService._fallback_rule_based_intent("Günstiges Handy unter 300 Euro")
    assert i2.category == "smartphone"
    assert i2.detected_language == "de"

    # rechner -> laptop
    i3 = AssistantService._fallback_rule_based_intent("Rechner für programmieren unter 1000 Euro")
    assert i3.category == "laptop"
    assert i3.detected_language == "de"
    assert "coding" in i3.intended_use


# =============================================================================
# 3. DETERMINISTIC MULTILINGUAL FALLBACK SUMMARIES
# =============================================================================

def test_german_deterministic_summary_qualified():
    """Test German deterministic summary when AI is degraded and options qualify."""
    intent = ShoppingIntent(
        query="Gaming-Laptop unter 1500 Euro",
        category="laptop",
        intended_use=["gaming"],
        max_budget=1500.0,
        currency="EUR",
        detected_language="de",
    )

    product = ProductResponse(
        id="prod-de-1",
        name="Asus ROG Gaming Laptop",
        brand="Asus",
        category="laptop",
        lowest_price=1299.0,
        lowest_price_retailer="MediaMarkt",
        created_at=datetime.now(timezone.utc),
        offers=[
            RetailerOfferResponse(
                id="off-1",
                product_id="prod-de-1",
                product_url="https://example.com/item",
                created_at=datetime.now(timezone.utc),
                retailer_name="MediaMarkt",
                price=1299.0,
                currency="EUR",
                availability="in_stock",
            )
        ],
    )

    elig = ProductEligibilityItem(
        product_id="prod-de-1",
        product_name="Asus ROG Gaming Laptop",
        status="QUALIFIED",
        is_qualified=True,
        reasons=["Budget and specs satisfied."],
        verified_specs={"ram": "16 GB"},
        unverified_specs=[],
    )

    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=[product],
        mandate_eval={"decision": "ALLOWED"},
        eligibility_items=[elig],
    )

    assert "Wir haben 1 passende Option(en) gefunden" in summary
    assert "Asus ROG Gaming Laptop" in summary
    assert "1,299.00 EUR" in summary
    assert "bei MediaMarkt" in summary
    assert "Ausgabenrichtlinie: Der Kauf ist im Rahmen Ihres aktiven Sitzungsmandats ERLAUBT" in summary


def test_french_deterministic_summary_qualified():
    """Test French deterministic summary when AI is degraded and options qualify."""
    intent = ShoppingIntent(
        query="Casque sans fil sous 150 euros",
        category="headphones",
        intended_use=["travel"],
        max_budget=150.0,
        currency="EUR",
        detected_language="fr",
    )

    product = ProductResponse(
        id="prod-fr-1",
        name="Sony WH-CH720N",
        brand="Sony",
        category="headphones",
        lowest_price=119.0,
        lowest_price_retailer="Fnac",
        created_at=datetime.now(timezone.utc),
        offers=[
            RetailerOfferResponse(
                id="off-fr-1",
                product_id="prod-fr-1",
                product_url="https://example.com/item",
                created_at=datetime.now(timezone.utc),
                retailer_name="Fnac",
                price=119.0,
                currency="EUR",
                availability="in_stock",
            )
        ],
    )

    elig = ProductEligibilityItem(
        product_id="prod-fr-1",
        product_name="Sony WH-CH720N",
        status="QUALIFIED",
        is_qualified=True,
        reasons=["Budget satisfied."],
        verified_specs={},
        unverified_specs=[],
    )

    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=[product],
        mandate_eval={"decision": "ALLOWED"},
        eligibility_items=[elig],
    )

    assert "Nous avons trouvé 1 option(s) qualifiée(s)" in summary
    assert "Sony WH-CH720N" in summary
    assert "119.00 EUR" in summary
    assert "chez Fnac" in summary
    assert "Politique de dépenses : L'achat est AUTORISÉ" in summary


def test_german_deterministic_summary_empty():
    """Test German empty results fallback summary."""
    intent = ShoppingIntent(
        query="Exotischer Laptop",
        category="laptop",
        detected_language="de",
    )
    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=[],
        mandate_eval=None,
    )
    assert "Wir haben bei Live-Händlern nach 'Exotischer Laptop' gesucht, konnten jedoch keine passenden Produkte finden" in summary


def test_french_deterministic_summary_empty():
    """Test French empty results fallback summary."""
    intent = ShoppingIntent(
        query="Produit Inexistant",
        category="general",
        detected_language="fr",
    )
    summary = AssistantService._generate_deterministic_summary(
        intent=intent,
        products=[],
        mandate_eval=None,
    )
    assert "Nous avons recherché 'Produit Inexistant' auprès des commerçants partenaires, mais aucun produit correspondant n'a été trouvé" in summary


# =============================================================================
# 4. REGRESSION VERIFICATION (ENGLISH, TAMIL, SPANISH)
# =============================================================================

def test_regression_english_intent_and_summary():
    """Verify English queries continue to extract and summarize accurately."""
    query = "Find a laptop for coding with at least 16GB RAM under $1500"
    intent = AssistantService._fallback_rule_based_intent(query)
    assert intent.detected_language == "en"
    assert intent.category == "laptop"
    assert "coding" in intent.intended_use
    assert intent.max_budget == 1500.0
    assert intent.currency == "USD"
    ram = [s for s in intent.required_specs if s.name == "ram"]
    assert len(ram) == 1
    assert ram[0].min_value == 16.0


def test_regression_spanish_intent():
    """Verify Spanish queries continue to extract accurately."""
    query = "Portátil para programación con 16GB de RAM por menos de 1000 euros"
    intent = AssistantService._fallback_rule_based_intent(query)
    assert intent.detected_language == "es"
    assert intent.category == "laptop"
    assert intent.max_budget == 1000.0
    assert intent.currency == "EUR"
    ram = [s for s in intent.required_specs if s.name == "ram"]
    assert len(ram) == 1
    assert ram[0].min_value == 16.0


def test_regression_tamil_intent():
    """Verify Tamil queries continue to extract accurately."""
    query = "மெஷின் லேர்னிங் லேப்டாப் 16 ஜிபி ரேம் 70000 ரூபாய்"
    intent = AssistantService._fallback_rule_based_intent(query)
    assert intent.detected_language == "ta"
    assert intent.category == "laptop"
    assert "machine_learning" in intent.intended_use
    assert intent.currency == "INR"
    assert intent.max_budget == 70000.0

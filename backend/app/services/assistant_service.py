"""
AI-Powered Multilingual Shopping Intelligence Platform
AI Shopping Assistant Orchestrator.
Coordinates user queries, Gemini intent extraction, Channel3 product search,
deterministic spending policy evaluation, grounded product intelligence, and specification/currency eligibility filtering.
Enforces the core security boundary: AI suggests, Backend validates, User approves, PayPal processes.
"""

import logging
import re
from typing import Any
from sqlalchemy.orm import Session

from backend.app.models.shopping import ShoppingSession
from backend.app.repositories.mandate_repository import MandateRepository
from backend.app.repositories.product_repository import ProductRepository
from backend.app.schemas.assistant import (
    ShoppingIntent,
    SpecificationRequirement,
    ProductEligibilityItem,
    ProductIntelligence,
    AssistantQueryRequest,
    AssistantQueryResponse,
)
from backend.app.schemas.product import ProductResponse
from backend.app.services.channel3_client import Channel3Client, Channel3Error
from backend.app.services.gemini_client import (
    GeminiClient,
    GeminiError,
    GeminiUnavailableError,
    GeminiTimeoutError,
    GeminiRateLimitError,
    GeminiAuthError,
)
from backend.app.services.policy_engine import SpendingPolicyEngine
from backend.app.services.product_service import ProductService

logger = logging.getLogger(__name__)

TARGET_ASSISTANT_RETRIEVAL_LIMIT: int = 20
MAX_ASSISTANT_GEMINI_ANALYSIS: int = 6
MAX_ASSISTANT_COMPARISON_LIMIT: int = 5


class AssistantService:
    """
    Central business logic coordinator for the AI Shopping Assistant.
    """

    @classmethod
    def _build_clarification_message(cls, intent: ShoppingIntent) -> str:
        """
        Synthesize a helpful, conversational clarification message when a query is ambiguous.
        Guides the user to specify intended use, budget, or key preferences.
        """
        if intent.clarification_prompt and intent.clarification_prompt.strip():
            return intent.clarification_prompt.strip()

        if intent.clarification_questions:
            return " ".join(q.strip() for q in intent.clarification_questions if q.strip())

        lang = (intent.detected_language or "en").lower()
        cat = (intent.category or "general").lower()

        if lang == "de":
            if cat in ("laptop", "computer"):
                return "Um den passenden Computer oder Laptop zu finden, teilen Sie uns bitte Ihren geplanten Einsatzzweck (z. B. Gaming, Arbeit oder Studium) und Ihr ungefähres Budget mit."
            elif cat in ("headphones", "kopfhörer"):
                return "Um die passenden Kopfhörer für Sie zu finden, wie hoch ist Ihr ungefähres Budget und welche Eigenschaften sind Ihnen wichtig (z. B. Geräuschunterdrückung, kabellos oder für unterwegs)?"
            elif cat in ("smartphone", "handy"):
                return "Um das passende Smartphone für Sie auszuwählen, welches Budget planen Sie ein und worauf legen Sie besonderen Wert (z. B. Kamera, Akkulaufzeit)?"
            return "Könnten Sie bitte näher beschreiben, nach welcher Art von Produkt Sie suchen, wofür Sie es nutzen möchten und welches Budget Sie einplanen?"

        if lang == "fr":
            if cat in ("laptop", "computer", "ordinateur"):
                return "Pour vous orienter vers le meilleur ordinateur, pourriez-vous préciser votre usage principal (ex. programmation, gaming, bureautique) et votre budget approximatif ?"
            elif cat in ("headphones", "casque"):
                return "Pour trouver le casque idéal, quel est votre budget et quelles fonctionnalités privilégiez-vous (ex. réduction de bruit, sans fil, usage nomade) ?"
            return "Pourriez-vous préciser le type de produit recherché, son utilisation prévue ainsi que votre budget approximatif ?"

        if lang == "es":
            if cat in ("laptop", "computer", "portátil", "computadora"):
                return "Para ayudarte a encontrar la mejor computadora o portátil, ¿podrías indicarnos tu uso previsto (ej. programación, juegos, trabajo) y tu presupuesto aproximado?"
            elif cat in ("headphones", "auriculares"):
                return "Para recomendarte los auriculares más adecuados, ¿cuál es tu presupuesto aproximado y qué características buscas (ej. cancelación de ruido, inalámbricos)?"
            return "¿Podrías dar más detalles sobre lo que buscas, como el uso previsto y tu presupuesto aproximado?"

        if lang == "ta":
            return "உங்களுக்கு மிகவும் பொருத்தமான பொருளை பரிந்துரைக்க, உங்கள் பயன்பாடு மற்றும் தோராயமான பட்ஜெட் விவரங்களைக் குறிப்பிட முடியுமா?"

        # Default English
        if cat in ("laptop", "computer"):
            return "To help you find the best computer or laptop, could you share your intended use (e.g., coding, gaming, general work) and your approximate budget?"
        elif cat in ("headphones", "headset", "earphones"):
            return "To help you find the right headphones, what is your approximate budget and what features are most important to you (e.g., active noise cancellation, wireless, travel)?"
        elif cat in ("smartphone", "phone"):
            return "To recommend the best smartphone, what is your target budget and are there key features you need (e.g., camera quality, battery life, operating system)?"
        return "Could you share a bit more detail about what you're looking for, such as your intended use and approximate budget?"

    @classmethod
    def _fallback_rule_based_intent(cls, query: str) -> ShoppingIntent:
        """
        Deterministic rule-based intent extractor used when Gemini is temporarily unavailable.
        Extracts numbers, currency symbols, basic category tokens, hardware specifications (RAM, storage),
        and language without pretending to be LLM reasoning.
        """
        clean_q = query.strip()
        lower_q = clean_q.lower()

        if not clean_q:
            return ShoppingIntent(
                query="",
                category="general",
                clarification_required=True,
                clarification_prompt="Please specify what product you are looking for, along with any budget or preferences.",
                clarification_questions=["What product are you looking to purchase?", "What is your target budget?"],
            )

        # Language detection (Tamil unicode range 0x0B80 - 0x0BFF, Spanish, French, German)
        detected_language = "en"
        if re.search(r"[஀-௿]", clean_q):
            detected_language = "ta"
        elif any(w in lower_q for w in ["para", "con", "por menos", "portátil", "computadora", "auriculares"]):
            detected_language = "es"
        elif any(w in lower_q for w in ["pour", "avec", "sous", "casque", "ordinateur", "écouteurs"]):
            detected_language = "fr"
        elif any(
            w in lower_q
            for w in [
                "ich suche", "suche", "brauche", "unter", "höchstens", "maximal",
                "bis zu", "mindestens", "kopfhörer", "handy", "für", "günstig",
                "billig", "kaufen", "rechner", "arbeitsspeicher", "festplatte",
                "gaming-laptop", "laufschuhe",
            ]
        ):
            detected_language = "de"

        # Currency detection
        currency = "USD"
        if "₹" in clean_q or "rs" in lower_q or "inr" in lower_q or "rupee" in lower_q or "ரூபாய்" in clean_q:
            currency = "INR"
        elif "€" in clean_q or "eur" in lower_q or "euro" in lower_q:
            currency = "EUR"
        elif "£" in clean_q or "gbp" in lower_q:
            currency = "GBP"

        # Budget extraction (e.g. under 70000, under $100, under ₹5,000, ₹70000க்குள், unter 1500 Euro)
        max_budget = None
        b_patterns = [
            r"(?:under|below|less than|max(?:imum)?|por menos de|sous|unter|höchstens|maximal|bis zu)\s*[$₹€£]?\s*([\d,]+(?:\.\d+)?)",
            r"[$₹€£]\s*([\d,]+(?:\.\d+)?)",
            r"([\d,]+(?:\.\d+)?)\s*(?:inr|rupees?|ரூபாய்|க்குள்|euro|eur|€)",
        ]
        for pat in b_patterns:
            bm = re.search(pat, lower_q)
            if bm:
                try:
                    num_str = bm.group(1).replace(",", "")
                    parsed_val = float(num_str)
                    # Disambiguate from RAM sizes (like 16 or 32) unless currency symbol was present
                    if (
                        parsed_val > 64
                        or "₹" in clean_q
                        or "$" in clean_q
                        or "€" in clean_q
                        or "euro" in lower_q
                        or "eur" in lower_q
                    ):
                        max_budget = parsed_val
                        break
                except ValueError:
                    pass

        # Intended use
        intended_use = []
        if (
            "machine learning" in lower_q
            or "ai" in lower_q
            or "மெஷின் லேர்னிங்" in clean_q
            or "deep learning" in lower_q
        ):
            intended_use.append("machine_learning")
        if (
            "gaming" in lower_q
            or "zocken" in lower_q
            or "spiel" in lower_q
            or "விளையாட்டு" in clean_q
        ):
            intended_use.append("gaming")
        if (
            "travel" in lower_q
            or "பயணம்" in clean_q
            or "viaje" in lower_q
            or "voyage" in lower_q
            or "reise" in lower_q
            or "unterwegs" in lower_q
        ):
            intended_use.append("travel")
        if (
            "coding" in lower_q
            or "programming" in lower_q
            or "programación" in lower_q
            or "programmation" in lower_q
            or "programmieren" in lower_q
            or "entwickler" in lower_q
        ):
            intended_use.append("coding")

        # Category matching
        category = "general"
        categories = [
            "gaming-laptop",
            "laptop",
            "notebook",
            "computer",
            "portátil",
            "ordinateur",
            "rechner",
            "headphones",
            "headset",
            "earphones",
            "casque",
            "kopfhörer",
            "smartphone",
            "phone",
            "handy",
            "smartwatch",
            "watch",
            "camera",
        ]
        for cat in categories:
            if cat in lower_q:
                if cat == "gaming-laptop":
                    category = "laptop"
                    if "gaming" not in intended_use:
                        intended_use.append("gaming")
                elif cat in ["notebook", "computer", "portátil", "ordinateur", "rechner"]:
                    category = "laptop"
                elif cat in ["headset", "earphones", "casque", "kopfhörer"]:
                    category = "headphones"
                elif cat in ["phone", "handy"]:
                    category = "smartphone"
                elif cat in ["watch"]:
                    category = "smartwatch"
                else:
                    category = cat
                break
        if "லேப்டாப்" in clean_q:
            category = "laptop"

        # Hardware Specification Extraction (RAM & Storage)
        required_specs: list[SpecificationRequirement] = []
        preferred_specs: list[SpecificationRequirement] = []

        # 1. RAM extraction (guard against capturing SSD / Storage values)
        ram_prefix_pat = r"(?:at least|minimum|min|must have|preferably|prefer|con al menos|avec au moins|mindestens|mindest|mit mindestens|mit|ab)"
        ram_kw_pat = r"(?:ram|memory|memoria|mémoire|ரேம்|arbeitsspeicher|speicher)"

        ram_match = re.search(
            rf"(?:({ram_prefix_pat})\s+)?(\d+)\s*(?:gb|gigabytes?|ஜிபி)\s*(?:of\s+)?(?:de\s+)?{ram_kw_pat}\b",
            lower_q,
        )
        if not ram_match and not any(w in lower_q for w in ["ssd", "nvme", "hdd", "festplatte"]):
            ram_match = re.search(
                rf"(?:({ram_prefix_pat})\s+)?(\b[48]|16|32|64|128)\s*(?:gb|gigabytes?|ஜிபி)\b",
                lower_q,
            )

        if ram_match:
            prefix = ram_match.group(1)
            num = float(ram_match.group(2))
            is_pref = prefix in ("preferably", "prefer") if prefix else False
            val_str = (
                f"{int(num)} GB or more"
                if (
                    prefix
                    and prefix
                    in (
                        "at least",
                        "minimum",
                        "min",
                        "must have",
                        "con al menos",
                        "avec au moins",
                        "mindestens",
                        "mindest",
                        "mit mindestens",
                        "ab",
                    )
                )
                else f"{int(num)} GB"
            )
            ram_item = SpecificationRequirement(
                name="ram",
                value=val_str,
                raw_spec=ram_match.group(0).strip(),
                min_value=num,
                unit="GB",
                is_hard_requirement=not is_pref,
            )
            if is_pref:
                preferred_specs.append(ram_item)
            else:
                required_specs.append(ram_item)

        # 2. Storage / SSD extraction
        storage_kw_pat = r"(?:ssd|storage|nvme|hdd|festplatte|speicher)"
        storage_match = re.search(
            rf"(?:({ram_prefix_pat})\s+)?(\d+)\s*(gb|tb)\s*{storage_kw_pat}",
            lower_q,
        )
        if storage_match:
            prefix = storage_match.group(1)
            num = float(storage_match.group(2))
            unit = storage_match.group(3).upper()
            is_pref = prefix in ("preferably", "prefer") if prefix else False
            val_str = (
                f"{int(num)} {unit} or more"
                if (
                    prefix
                    and prefix
                    in (
                        "at least",
                        "minimum",
                        "min",
                        "must have",
                        "con al menos",
                        "avec au moins",
                        "mindestens",
                        "mindest",
                        "mit mindestens",
                        "ab",
                    )
                )
                else f"{int(num)} {unit}"
            )
            storage_item = SpecificationRequirement(
                name="storage",
                value=val_str,
                raw_spec=storage_match.group(0).strip(),
                min_value=num,
                unit=unit,
                is_hard_requirement=not is_pref,
            )
            if is_pref:
                preferred_specs.append(storage_item)
            else:
                required_specs.append(storage_item)

        # Clarification detection for ambiguous or underspecified queries
        clarification_required = False
        clarification_prompt = None
        clarification_questions: list[str] = []

        is_generic = any(
            re.search(pat, lower_q)
            for pat in [
                r"^(?:recommend|suggest|find)\s+(?:me\s+)?something(?:\s+good|\s+nice)?\b",
                r"^(?:help\s+me\s+buy\s+a\s+gift|gift\s+ideas?|gift\s+recommendations?)\b",
                r"^(?:i\s+(?:want|need)\s+(?:to\s+buy\s+)?something)\b",
                r"^(?:what\s+should\s+i\s+buy|buy\s+something)\b",
            ]
        )

        is_bare_category = (
            not max_budget
            and not required_specs
            and not preferred_specs
            and not intended_use
            and any(
                re.search(pat, lower_q)
                for pat in [
                    r"^(?:i\s+(?:want|need|am\s+looking(?:\s+for)?)\s+(?:to\s+buy\s+)?(?:a\s+|an\s+)?(computer|laptop|headphones?|headset|earphones?|phone|smartphone|smartwatch|watch|tv|camera))\b[.\s?!]*$",
                    r"^(?:looking\s+for\s+(?:a\s+|an\s+)?(computer|laptop|headphones?|headset|earphones?|phone|smartphone|smartwatch|watch|tv|camera))\b[.\s?!]*$",
                    r"^(?:ich\s+(?:suche|brauche)\s+(?:einen?|ein)?\s*(computer|rechner|laptop|kopfhörer|handy))\b[.\s?!]*$",
                    r"^(?:suche\s+(?:nach\s+)?(?:einen?|ein)?\s*(computer|rechner|laptop|kopfhörer|handy))\b[.\s?!]*$",
                    r"^(?:je\s+(?:cherche|veux\s+acheter)\s+(?:un\s+|une\s+|des\s+)?(ordinateur|portable|casque|écouteurs|téléphone|smartphone))\b[.\s?!]*$",
                    r"^(?:quiero\s+comprar\s+(?:un\s+|una\s+)?(computadora|portátil|auriculares|teléfono))\b[.\s?!]*$",
                ]
            )
        )

        if is_generic or is_bare_category:
            clarification_required = True
            dummy_intent = ShoppingIntent.model_construct(
                query=clean_q,
                category=category,
                detected_language=detected_language,
            )
            clarification_prompt = cls._build_clarification_message(dummy_intent)
            if category in ("laptop", "computer"):
                clarification_questions = ["What is your intended use?", "What is your target budget?"]
            elif category == "headphones":
                clarification_questions = ["What is your target budget?", "What features do you prefer (e.g., wireless, noise cancellation)?"]
            else:
                clarification_questions = ["What product type or use case are you focusing on?", "What is your target budget?"]

        # Brand extraction (preferred brands and excluded brands)
        brand_preferences: list[str] = []
        excluded_brands: list[str] = []

        recognized_brands_map = {
            "apple": "Apple",
            "sony": "Sony",
            "dell": "Dell",
            "hp": "HP",
            "lenovo": "Lenovo",
            "asus": "Asus",
            "acer": "Acer",
            "samsung": "Samsung",
            "bose": "Bose",
            "sennheiser": "Sennheiser",
            "anker": "Anker",
            "soundcore": "Soundcore",
            "logitech": "Logitech",
            "microsoft": "Microsoft",
            "google": "Google",
            "jbl": "JBL",
            "audio-technica": "Audio-Technica",
            "beats": "Beats",
            "razer": "Razer",
            "msi": "MSI",
        }

        # Check for exclusions first: e.g. "no hp", "not dell", "avoid beats", "exclude acer", "without lenovo", "kein hp", "ohne beats"
        for b_lower, b_canonical in recognized_brands_map.items():
            excl_pat = rf"\b(?:no|not|avoid|without|exclude|kein|keine|ohne|pas\s+de|sans|sin)\s+{re.escape(b_lower)}\b"
            if re.search(excl_pat, lower_q):
                if b_canonical not in excluded_brands:
                    excluded_brands.append(b_canonical)

        # Check for preferences or presence (if not excluded)
        for b_lower, b_canonical in recognized_brands_map.items():
            if b_canonical in excluded_brands:
                continue
            pref_pat = rf"\b(?:prefer|preferably|preferiblemente|bevorzuge|brand|von|de\s+la\s+marque|préfère)\s+{re.escape(b_lower)}\b"
            mention_pat = rf"\b{re.escape(b_lower)}\b"
            if re.search(pref_pat, lower_q) or re.search(mention_pat, lower_q):
                if b_canonical not in brand_preferences:
                    brand_preferences.append(b_canonical)

        return ShoppingIntent(
            query=clean_q,
            category=category,
            intended_use=intended_use,
            max_budget=max_budget,
            currency=currency,
            required_specs=required_specs,
            preferred_specs=preferred_specs,
            brand_preferences=brand_preferences,
            excluded_brands=excluded_brands,
            detected_language=detected_language,
            clarification_required=clarification_required,
            clarification_prompt=clarification_prompt,
            clarification_questions=clarification_questions,
        )

    @staticmethod
    def _extract_ram_from_product(product: ProductResponse) -> tuple[float | None, str]:
        """Extract RAM in GB from product specifications, key_features, title, or description."""
        specs = product.specifications or {}

        # 1. Check dedicated RAM spec keys
        ram_keys = (
            "ram", "RAM", "memory", "Memory", "system_memory", "System Memory (RAM)",
            "ram_memory", "system_memory_ram", "ram_size", "installed_ram", "memory_size", "ram_gb",
        )
        for key in ram_keys:
            if key in specs and specs[key] is not None:
                val = specs[key]
                if isinstance(val, (int, float)) and 2 <= val <= 256:
                    return float(val), f"specs.{key}: {val} GB"
                val_str = str(val).lower()
                m = re.search(r"(\d+)\s*(?:gb|gigabytes?)", val_str)
                if m:
                    return float(m.group(1)), f"specs.{key}: {specs[key]}"

        # 2. Check structured key_features, features, or highlights
        for feat_key in ("key_features", "features", "highlights", "bullet_points"):
            feats = specs.get(feat_key)
            if feats:
                feat_list = feats if isinstance(feats, list) else [feats]
                for item in feat_list:
                    item_str = str(item).strip()
                    item_lower = item_str.lower()
                    m = (
                        re.search(r"(\d+)\s*(?:gb|gigabytes?)(?:\s+(?:ddr[345]|lpddr[45][a-z]?|sdram|unified))?\s*(?<!v)(?:ram|memory\b)", item_lower)
                        or re.search(r"(\d+)\s*(?:gb|gigabytes?)\s+(?:ddr[345]|lpddr[45][a-z]?)\b", item_lower)
                        or re.search(r"(?<!v)(?:ram|memory)\s*:\s*(\d+)\s*(?:gb|gigabytes?)", item_lower)
                    )
                    if m:
                        num = next(g for g in m.groups() if g and g.isdigit())
                        return float(num), f"specs.{feat_key}: {item_str}"

        # 3. Check product title and description
        for text, src in [(product.name, "product title"), (product.description, "description")]:
            if text:
                lower_text = text.lower()
                m = (
                    re.search(r"(\d+)\s*(?:gb|gigabytes?)(?:\s+(?:ddr[345]|lpddr[45][a-z]?|sdram|unified))?\s*(?<!v)(?:ram|memory\b)", lower_text)
                    or re.search(r"(\d+)\s*(?:gb|gigabytes?)\s+(?:ddr[345]|lpddr[45][a-z]?)\b", lower_text)
                    or re.search(r"(?<!v)(?:ram|memory)\s*:\s*(\d+)\s*(?:gb|gigabytes?)", lower_text)
                    or re.search(r"(\d+)\s*(?:gb|gigabytes?)\s+of\s+(?:system\s+)?(?<!v)(?:ram|memory)", lower_text)
                )
                if m:
                    num = next(g for g in m.groups() if g and g.isdigit())
                    return float(num), f"{src}: {m.group(0)}"

        return None, "Not specified"

    @staticmethod
    def _extract_storage_from_product(product: ProductResponse) -> tuple[float | None, str]:
        """Extract storage in GB from product specifications, key_features, title, or description."""
        specs = product.specifications or {}

        # 1. Check dedicated storage spec keys
        storage_keys = (
            "storage", "Storage", "storage-capacity", "storage_capacity",
            "storage-drive-capacity", "storage_drive_capacity",
            "ssd", "SSD", "hard_drive", "drive", "capacity", "internal_storage",
            "solid_state_drive_capacity", "ssd_capacity", "hdd_capacity"
        )
        for key in storage_keys:
            if key in specs and specs[key] is not None:
                val = specs[key]
                if isinstance(val, (int, float)):
                    if val in (128, 256, 512, 1000, 1024, 2000, 2048, 4000, 4096):
                        return float(val), f"specs.{key}: {val} GB"
                    if val in (1, 2, 4, 8) and ("tb" in key.lower() or val <= 4):
                        return float(val) * 1024.0, f"specs.{key}: {val} TB"

                val_str = str(val).lower()
                tb_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:tb|terabytes?)", val_str)
                if tb_m:
                    return float(tb_m.group(1)) * 1024.0, f"specs.{key}: {specs[key]}"
                gb_m = re.search(r"(\d+)\s*(?:gb|gigabytes?)", val_str)
                if gb_m:
                    return float(gb_m.group(1)), f"specs.{key}: {specs[key]}"

        # 2. Check structured key_features, features, or highlights
        for feat_key in ("key_features", "features", "highlights", "bullet_points"):
            feats = specs.get(feat_key)
            if feats:
                feat_list = feats if isinstance(feats, list) else [feats]
                for item in feat_list:
                    item_str = str(item).strip()
                    item_lower = item_str.lower()
                    tb_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:tb|terabytes?)(?:\s+(?:pcie|nvme|m\.2))*\s*(?:ssd|storage|nvme|hdd|drive)?", item_lower)
                    if tb_m:
                        return float(tb_m.group(1)) * 1024.0, f"specs.{feat_key}: {item_str}"
                    gb_m = re.search(r"(\d+)\s*(?:gb|gigabytes?)(?:\s+(?:pcie|nvme|gen\s*\d|m\.2))*\s*(?:ssd|storage|nvme|hdd|drive|emmc)", item_lower)
                    if gb_m:
                        return float(gb_m.group(1)), f"specs.{feat_key}: {item_str}"

        # 3. Check product title and description
        for text, src in [(product.name, "product title"), (product.description, "description")]:
            if text:
                lower_text = text.lower()
                tb_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:tb|terabytes?)(?:\s+(?:pcie|nvme|gen\s*\d|m\.2))*\s*(?:ssd|storage|nvme|hdd|drive)?", lower_text)
                if tb_m:
                    return float(tb_m.group(1)) * 1024.0, f"{src}: {tb_m.group(0)}"
                gb_m = re.search(r"(\d+)\s*(?:gb|gigabytes?)(?:\s+(?:pcie|nvme|gen\s*\d|m\.2))*\s*(?:ssd|storage|nvme|hdd|drive|emmc)", lower_text)
                if gb_m:
                    return float(gb_m.group(1)), f"{src}: {gb_m.group(0)}"

        return None, "Not specified"

    @classmethod
    def evaluate_product_eligibility(
        cls,
        product: ProductResponse,
        intent: ShoppingIntent,
    ) -> ProductEligibilityItem:
        """
        Authoritative deterministic product eligibility evaluator.
        Enforces specification verification and currency/budget constraints:
        - If user specifies INR budget, USD offers cannot qualify without verified FX conversion.
        - If user requires at least 16GB RAM:
            - Verified RAM >= 16GB qualifies.
            - Verified RAM < 16GB is excluded.
            - Unknown RAM is marked UNVERIFIED_SPECS.
        """
        reasons: list[str] = []
        verified_specs: dict[str, Any] = {}
        unverified_specs: list[str] = []
        status = "QUALIFIED"
        is_budget_exceeded = False
        budget_exceeded_amount = None
        is_override_eligible = False

        # Step A: Evaluate Hard Specification Requirements
        for req in intent.required_specs:
            spec_name = req.name.lower()
            if spec_name == "ram":
                ram_gb, evidence_src = cls._extract_ram_from_product(product)
                if ram_gb is None:
                    status = "UNVERIFIED_SPECS"
                    unverified_specs.append("ram")
                    reasons.append(
                        f"RAM specification is missing from product details; cannot confirm requirement of {req.value}."
                    )
                else:
                    verified_specs["ram"] = f"{int(ram_gb)} GB"
                    if req.min_value is not None:
                        if ram_gb < req.min_value:
                            status = "EXCLUDED"
                            reasons.append(
                                f"Verified RAM of {int(ram_gb)} GB is below your required minimum of {int(req.min_value)} GB."
                            )
                        else:
                            reasons.append(
                                f"Verified RAM of {int(ram_gb)} GB meets your required minimum of {int(req.min_value)} GB ({evidence_src})."
                            )
            elif spec_name in ("storage", "ssd"):
                storage_gb, storage_src = cls._extract_storage_from_product(product)
                if storage_gb is None:
                    if status == "QUALIFIED":
                        status = "UNVERIFIED_SPECS"
                    unverified_specs.append("storage")
                    reasons.append(f"Storage specification is unverified ({req.value}).")
                else:
                    verified_specs["storage"] = f"{storage_gb} GB"
                    if req.min_value is not None:
                        min_gb = req.min_value * 1024.0 if req.unit == "TB" else req.min_value
                        if storage_gb < min_gb:
                            status = "EXCLUDED"
                            reasons.append(
                                f"Verified storage of {int(storage_gb)} GB is below your required minimum of {int(min_gb)} GB."
                            )
                        else:
                            reasons.append(
                                f"Verified storage of {int(storage_gb)} GB meets your required minimum ({storage_src})."
                            )

        # Step B: Evaluate Currency and Budget Constraints
        if intent.max_budget is not None:
            lowest_price = product.lowest_price
            offer_currency = product.offers[0].currency if product.offers and product.offers[0].currency else None

            if lowest_price is None or not offer_currency:
                if status == "QUALIFIED":
                    status = "UNVERIFIED_SPECS"
                reasons.append("Retailer pricing or offer currency is unavailable.")
            elif offer_currency.upper() != intent.currency.upper():
                # Cross-currency rule: Cannot compare directly without verified FX rate
                if status != "EXCLUDED":
                    status = "CURRENCY_MISMATCH"
                reasons.append(
                    f"Offer is priced in {offer_currency.upper()} ({lowest_price:,.2f} {offer_currency.upper()}), "
                    f"which cannot be directly evaluated against your {intent.currency.upper()} budget "
                    f"({intent.max_budget:,.2f} {intent.currency.upper()}) without verified real-time FX conversion."
                )
            else:
                # Same currency: evaluate numerical budget limit (soft budget preference)
                if lowest_price > intent.max_budget:
                    is_budget_exceeded = True
                    budget_exceeded_amount = round(lowest_price - intent.max_budget, 2)
                    is_override_eligible = True
                    reasons.append(
                        f"Price of {lowest_price:,.2f} {offer_currency.upper()} exceeds your preferred budget of "
                        f"{intent.max_budget:,.2f} {intent.currency.upper()} by {budget_exceeded_amount:,.2f} {offer_currency.upper()} (user override permitted)."
                    )
                else:
                    reasons.append(
                        f"Price of {lowest_price:,.2f} {offer_currency.upper()} is within your budget of "
                        f"{intent.max_budget:,.2f} {intent.currency.upper()}."
                    )

        # Step C: Evaluate Mandatory Brand Exclusions (Safeguard 2)
        if intent.excluded_brands:
            prod_brand_lower = (product.brand or "").strip().lower()
            prod_name_lower = (product.name or "").strip().lower()
            for excl in intent.excluded_brands:
                excl_clean = excl.strip().lower()
                if not excl_clean:
                    continue
                is_brand_excluded = (
                    excl_clean == prod_brand_lower
                    or re.search(rf"\b{re.escape(excl_clean)}\b", prod_brand_lower)
                    or re.search(rf"\b{re.escape(excl_clean)}\b", prod_name_lower)
                )
                if is_brand_excluded:
                    status = "EXCLUDED"
                    reasons.append(
                        f"Excluded: Brand '{product.brand}' was explicitly excluded by user preferences."
                    )
                    break

        is_qualified = (status == "QUALIFIED")
        if status == "EXCLUDED":
            is_override_eligible = False

        elig_interim = ProductEligibilityItem(
            product_id=product.id,
            product_name=product.name,
            status=status,
            is_qualified=is_qualified,
            reasons=reasons,
            verified_specs=verified_specs,
            unverified_specs=unverified_specs,
            is_budget_exceeded=is_budget_exceeded,
            budget_exceeded_amount=budget_exceeded_amount,
            is_override_eligible=is_override_eligible,
        )

        # Calculate deterministic recommendation score (Safeguards 1-4)
        rec_score, rec_reasons = cls.calculate_product_recommendation_score(product, intent, elig_interim)
        elig_interim.recommendation_score = rec_score
        elig_interim.score_reasons = rec_reasons
        return elig_interim

    @classmethod
    def calculate_product_recommendation_score(
        cls,
        product: ProductResponse,
        intent: ShoppingIntent,
        eligibility: ProductEligibilityItem,
    ) -> tuple[float, list[str]]:
        """
        Deterministic recommendation score calculator (Safeguards 1-4).
        Returns a bounded float score between 0.0 and 100.0 and an explainable list of score reasons.

        Strict Invariants:
        1. Bounded: 0.0 <= score <= 100.0 at all stages.
        2. Excluded Brands: Explicitly excluded brands receive 0.0 score and cannot be recommended.
        3. Decoupled from Mandate: Spending mandate ALLOWED/BLOCKED status does NOT alter recommendation score.
        4. Verified Features Only: Points awarded ONLY when merchant data supports the requested feature.
           Never infer features from product name alone or generate unsupported claims.
        """
        # Safeguard 2: Deterministic 0.0 score for excluded brands
        if intent.excluded_brands:
            prod_brand_lower = (product.brand or "").strip().lower()
            prod_name_lower = (product.name or "").strip().lower()
            for excl in intent.excluded_brands:
                excl_clean = excl.strip().lower()
                if excl_clean and (
                    excl_clean == prod_brand_lower
                    or re.search(rf"\b{re.escape(excl_clean)}\b", prod_brand_lower)
                    or re.search(rf"\b{re.escape(excl_clean)}\b", prod_name_lower)
                ):
                    return 0.0, [f"Excluded: Brand '{product.brand}' was explicitly excluded by user preferences (Score: 0.0)"]

        score_reasons: list[str] = []

        # 1. Base score by hard eligibility tier (0.0 to 50.0)
        status_upper = eligibility.status.upper()
        if status_upper == "QUALIFIED":
            base_score = 50.0
            if eligibility.is_budget_exceeded:
                score_reasons.append("Base: 50.0 pts (Meets hard specifications; exceeds preferred budget)")
            else:
                score_reasons.append("Base: 50.0 pts (Meets all hard specifications and budget ceiling)")
        elif status_upper == "UNVERIFIED_SPECS":
            base_score = 30.0
            score_reasons.append("Base: 30.0 pts (Key specifications pending merchant verification)")
        elif status_upper == "CURRENCY_MISMATCH":
            base_score = 20.0
            score_reasons.append("Base: 20.0 pts (Priced in alternative currency; FX conversion required)")
        else:  # EXCLUDED
            base_score = 10.0
            score_reasons.append("Base: 10.0 pts (Does not meet required specifications or budget)")

        running_score = base_score

        # 2. Preferred Specifications Matching (up to +25.0 pts)
        specs = product.specifications or {}
        key_features: list[str] = []
        for kf_key in ("key_features", "features", "highlights", "bullet_points"):
            raw_kf = specs.get(kf_key)
            if raw_kf:
                if isinstance(raw_kf, list):
                    key_features.extend(str(item).lower() for item in raw_kf)
                elif isinstance(raw_kf, str):
                    key_features.append(raw_kf.lower())

        for pref in intent.preferred_specs:
            pref_name = pref.name.lower()
            if pref_name == "ram":
                ram_gb, ram_src = cls._extract_ram_from_product(product)
                if ram_gb is not None:
                    if pref.min_value is not None and ram_gb >= pref.min_value:
                        running_score += 15.0
                        score_reasons.append(f"+15.0 pts: Meets preferred RAM of {pref.value} ({int(ram_gb)} GB verified in {ram_src})")
                    elif pref.min_value is not None and ram_gb >= 16.0:
                        running_score += 5.0
                        score_reasons.append(f"+5.0 pts: Partial RAM match ({int(ram_gb)} GB verified vs preferred {pref.value})")
                else:
                    score_reasons.append(f"0.0 pts: Preferred RAM ({pref.value}) is unconfirmed in merchant specifications")
            elif pref_name in ("storage", "ssd"):
                storage_gb, storage_src = cls._extract_storage_from_product(product)
                if storage_gb is not None:
                    min_gb = pref.min_value * 1024.0 if pref.unit == "TB" and pref.min_value else (pref.min_value or 0.0)
                    if storage_gb >= min_gb:
                        running_score += 10.0
                        score_reasons.append(f"+10.0 pts: Meets preferred Storage of {pref.value} ({int(storage_gb)} GB verified)")
                else:
                    score_reasons.append(f"0.0 pts: Preferred Storage ({pref.value}) is unconfirmed in merchant specifications")
            else:
                pref_val_lower = (pref.value or pref.name).lower()
                merchant_text = " ".join([str(v) for k, v in specs.items() if k not in ("bullet_points", "key_features")] + key_features).lower()
                if pref_val_lower in merchant_text or any(token in merchant_text for token in pref_val_lower.split() if len(token) > 3):
                    running_score += 5.0
                    score_reasons.append(f"+5.0 pts: Verified merchant feature matches preference '{pref.value}'")
                else:
                    score_reasons.append(f"0.0 pts: Preferred feature '{pref.value}' is unconfirmed in merchant specifications")

        # 3. Brand Preferences Matching (up to +15.0 pts)
        if intent.brand_preferences:
            prod_brand_lower = (product.brand or "").strip().lower()
            matched_brand = None
            for b in intent.brand_preferences:
                b_clean = b.strip().lower()
                if b_clean and (b_clean == prod_brand_lower or re.search(rf"\b{re.escape(b_clean)}\b", prod_brand_lower)):
                    matched_brand = b
                    break
            if matched_brand:
                running_score += 15.0
                score_reasons.append(f"+15.0 pts: Matches preferred brand '{product.brand}'")
            else:
                score_reasons.append(f"0.0 pts: Brand '{product.brand}' does not match preferred brands ({', '.join(intent.brand_preferences)})")

        # 4. Budget Efficiency (up to +10.0 pts) - Only for same-currency qualified products
        if status_upper == "QUALIFIED" and intent.max_budget and intent.max_budget > 0:
            offer_curr = product.offers[0].currency.upper() if product.offers and product.offers[0].currency else ""
            if offer_curr == intent.currency.upper() and product.lowest_price is not None:
                if product.lowest_price <= intent.max_budget:
                    savings_ratio = max(0.0, 1.0 - (product.lowest_price / intent.max_budget))
                    eff_pts = round(savings_ratio * 10.0, 1)
                    if eff_pts > 0:
                        running_score += eff_pts
                        score_reasons.append(f"+{eff_pts} pts: Budget efficiency ({product.lowest_price:,.2f} {offer_curr} vs {intent.max_budget:,.2f} budget)")

        # Safeguard 1: Strict score boundary clamping
        final_score = max(0.0, min(100.0, round(running_score, 1)))
        return final_score, score_reasons

    @classmethod
    def validate_and_sanitize_intelligence(
        cls,
        analyses: list[ProductIntelligence],
        eligibility_map: dict[str, ProductEligibilityItem],
    ) -> list[ProductIntelligence]:
        """
        Authoritative backend validation and sanitization layer for AI product intelligence.
        Guarantees that Gemini output never contradicts deterministic eligibility results:
        1. Deterministic eligibility is the single source of truth.
        2. Specifications marked unverified (e.g. RAM, storage) must NEVER be presented as confirmed strengths.
        3. Removes unsupported specification claims from strengths and advantages, while preserving
           all legitimate evidenced strengths (CPU, GPU, display, chassis, etc.).
        4. Accurately reflects deterministic status in suitability:
           - QUALIFIED: Retains suitable rating.
           - UNVERIFIED_SPECS: Marks as conditionally suitable pending verification (does not mark as excluded).
           - EXCLUDED: Marks as not recommended / excluded due to failing hard specs or budget.
           - CURRENCY_MISMATCH: Notes currency disparity requiring FX conversion.
        """
        sanitized_list: list[ProductIntelligence] = []

        ram_claim_patterns = [
            re.compile(r"\b(?:\d+\s*(?:gb|gigabytes?)\s*(?:ddr[345]|lpddr[45][a-z]?|sdram|unified)?\s*)?(?<!v)(?<!g)ram\b", re.IGNORECASE),
            re.compile(r"\b(?:\d+\s*(?:gb|gigabytes?)\s+(?:ddr[345]|lpddr[45][a-z]?))\b", re.IGNORECASE),
            re.compile(r"\b(?:\d+\s*(?:gb|gigabytes?)\s+of\s+system\s+memory)\b", re.IGNORECASE),
            re.compile(r"\b(?:system\s+memory|installed\s+memory)\b", re.IGNORECASE),
            re.compile(r"\b(?:sufficient|ample|generous|adequate)\s+ram\b", re.IGNORECASE),
        ]

        storage_claim_patterns = [
            re.compile(r"\b(?:\d+\s*(?:tb|terabytes?|gb|gigabytes?)\s*(?:pcie|nvme|m\.2)?\s*)?(?:ssd|nvme|solid\s+state|hard\s+drive|hdd|storage\s+capacity)\b", re.IGNORECASE),
            re.compile(r"\b(?:\d+\s*tb\s+storage)\b", re.IGNORECASE),
            re.compile(r"\b(?:sufficient|ample|generous|adequate|large)\s+storage\b", re.IGNORECASE),
        ]

        for item in analyses:
            elig = eligibility_map.get(item.product_id)
            if not elig:
                sanitized_list.append(item)
                continue

            unverified = [s.lower() for s in elig.unverified_specs]
            has_unverified_ram = "ram" in unverified
            has_unverified_storage = any(s in ("storage", "ssd") for s in unverified)

            # 1. Filter strengths
            clean_strengths: list[str] = []
            for st in item.strengths:
                st_lower = st.lower()
                is_invalid_ram = False
                if has_unverified_ram:
                    if ("vram" in st_lower or "gddr" in st_lower or "graphics memory" in st_lower) and not ("system memory" in st_lower or "system ram" in st_lower):
                        is_invalid_ram = False
                    else:
                        is_invalid_ram = any(p.search(st) for p in ram_claim_patterns)

                is_invalid_storage = False
                if has_unverified_storage:
                    is_invalid_storage = any(p.search(st) for p in storage_claim_patterns)

                if not is_invalid_ram and not is_invalid_storage:
                    clean_strengths.append(st)
                else:
                    logger.info(
                        "Sanitized unverified spec claim from product %s strengths: '%s'",
                        item.product_id,
                        st,
                    )

            # 2. Filter intended_use_advantages
            clean_advantages: list[str] = []
            for adv in item.intended_use_advantages:
                adv_lower = adv.lower()
                is_invalid_ram = False
                if has_unverified_ram:
                    if ("vram" in adv_lower or "gddr" in adv_lower or "graphics memory" in adv_lower) and not ("system memory" in adv_lower or "system ram" in adv_lower):
                        is_invalid_ram = False
                    else:
                        is_invalid_ram = any(p.search(adv) for p in ram_claim_patterns)

                is_invalid_storage = False
                if has_unverified_storage:
                    is_invalid_storage = any(p.search(adv) for p in storage_claim_patterns)

                if not is_invalid_ram and not is_invalid_storage:
                    clean_advantages.append(adv)

            # 3. Ensure missing_information explicitly notes unverified specs
            clean_missing = list(item.missing_information)
            if has_unverified_ram:
                if not any("ram" in m.lower() or "memory" in m.lower() for m in clean_missing):
                    clean_missing.append("RAM capacity is not confirmed in merchant product specifications.")
            if has_unverified_storage:
                if not any("storage" in m.lower() or "ssd" in m.lower() for m in clean_missing):
                    clean_missing.append("Storage / SSD capacity is unverified in merchant product specifications.")

            # 4. Align suitability with deterministic status
            updated_suitability = item.suitability
            status = elig.status.upper()
            if status == "UNVERIFIED_SPECS":
                suit_lower = item.suitability.lower()
                if any(kw in suit_lower for kw in ("highly suitable", "excellent", "perfect", "fully suitable", "best match", "ideal")):
                    updated_suitability = "Conditionally Suitable (Key specifications pending merchant verification)"
                elif "unverified" not in suit_lower and "pending" not in suit_lower and "conditional" not in suit_lower:
                    updated_suitability = f"Conditionally Suitable - Pending verification of {', '.join(unverified).upper()}"
            elif status == "EXCLUDED":
                suit_lower = item.suitability.lower()
                if any(kw in suit_lower for kw in ("suitable", "recommended", "excellent", "good")):
                    updated_suitability = "Excluded (Does not meet required specifications or budget)"
            elif status == "CURRENCY_MISMATCH":
                suit_lower = item.suitability.lower()
                if "currency" not in suit_lower:
                    updated_suitability = "Currency Mismatch (Direct budget comparison requires FX conversion)"

            # 4b. Align suitability and scrub claims if spending mandate is BLOCKED
            if getattr(elig, "mandate_status", None) == "BLOCKED":
                mandate_approval_patterns = [
                    re.compile(r"\b(?:mandate\s+approved|approved\s+for\s+purchase|purchase\s+eligible|within\s+(?:your\s+)?mandate|policy\s+approved|complies\s+with\s+(?:your\s+)?mandate|policy\s+compliant|mandate\s+compliant|compliant\s+with\s+(?:your\s+)?(?:spending\s+)?(?:policy|mandate))\b", re.IGNORECASE),
                ]
                clean_strengths = [s for s in clean_strengths if not any(p.search(s) for p in mandate_approval_patterns)]
                clean_advantages = [a for a in clean_advantages if not any(p.search(a) for p in mandate_approval_patterns)]

                suit_lower = updated_suitability.lower()
                if any(kw in suit_lower for kw in ("approved", "purchase eligible", "policy compliant", "highly suitable", "recommended option")):
                    updated_suitability = "Blocked by Spending Mandate (Exceeds session mandate limits)"
                elif "blocked" not in suit_lower and "mandate" not in suit_lower:
                    updated_suitability += " — Exceeds spending mandate limits"

            # 5. Sanitize recommendation_rationale if it claims unverified specs
            updated_rationale = item.recommendation_rationale
            if has_unverified_ram and ("16 gb ram" in updated_rationale.lower() or "16gb ram" in updated_rationale.lower()):
                updated_rationale = re.sub(
                    r"(?i)(?:with|featuring|including)?\s*\d+\s*gb\s*(?:ddr\d\s*)?ram\s*(?:meets\s+requirements)?",
                    "with unverified RAM specification",
                    updated_rationale,
                )

            sanitized_item = item.model_copy(
                update={
                    "strengths": clean_strengths,
                    "intended_use_advantages": clean_advantages,
                    "missing_information": clean_missing,
                    "suitability": updated_suitability,
                    "recommendation_rationale": updated_rationale,
                }
            )
            sanitized_list.append(sanitized_item)

        return sanitized_list

    @classmethod
    def process_query(
        cls,
        db: Session,
        request: AssistantQueryRequest,
        gemini_client: GeminiClient | None = None,
        c3_client: Channel3Client | None = None,
    ) -> AssistantQueryResponse:
        """
        Execute the complete assistant query workflow:
        1. Extract structured shopping intent via Gemini (or safe fallback).
        2. Execute backend-controlled product search via Channel3 or catalog.
        3. Deterministically evaluate product eligibility (RAM, specs, budget, currency).
        4. Evaluate active spending mandate deterministically via SpendingPolicyEngine.
        5. Generate grounded product intelligence and consistent conversational guidance.
        """
        g_client = gemini_client or GeminiClient()
        c_client = c3_client or Channel3Client()

        # Step 1: Ensure session exists or create it
        session_obj = MandateRepository.get_or_create_session(db, request.session_id)
        active_mandate = MandateRepository.get_mandate_by_session_id(db, session_obj.id)

        # Step 2: Intent Extraction
        intent: ShoppingIntent
        is_ai_degraded = False
        degraded_reason = None

        try:
            intent = g_client.extract_shopping_intent(request.query)
        except (GeminiUnavailableError, GeminiTimeoutError, GeminiRateLimitError, GeminiAuthError, GeminiError) as exc:
            logger.warning(
                "Gemini intent extraction degraded [%s]: %s. Utilizing rule-based intent fallback.",
                type(exc).__name__,
                exc,
            )
            intent = cls._fallback_rule_based_intent(request.query)
            is_ai_degraded = True
            degraded_reason = f"Gemini reasoning temporarily unavailable: {str(exc)}"

        # Step 2.5: Immediate Clarification Handling (Bypass live search / product retrieval)
        if intent.clarification_required:
            clarification_msg = cls._build_clarification_message(intent)
            return AssistantQueryResponse(
                success=True,
                session_id=request.session_id,
                intent=intent,
                products=[],
                qualified_product_ids=[],
                excluded_product_ids=[],
                unverified_product_ids=[],
                currency_mismatch_product_ids=[],
                eligibility_details=[],
                ai_analyses=[],
                assistant_message=clarification_msg,
                mandate_evaluation=None,
                is_ai_degraded=is_ai_degraded,
                degraded_reason=degraded_reason,
            )

        # Step 3: Product Search (Controlled by Backend with Brand Preference Support)
        search_query = request.query
        if intent.category and intent.category != "general":
            if intent.intended_use:
                search_query = f"{intent.category} {intent.intended_use[0]}"
            else:
                search_query = intent.category

        primary_brand = intent.brand_preferences[0].strip() if intent.brand_preferences else None
        if primary_brand and primary_brand.lower() not in search_query.lower():
            search_query = f"{primary_brand} {search_query}".strip()

        retrieved_products: list[ProductResponse] = []
        try:
            if c_client.is_configured:
                search_res = ProductService.search_live_products(
                    db=db, query=search_query, limit=TARGET_ASSISTANT_RETRIEVAL_LIMIT, client=c_client
                )
                retrieved_products = search_res.items

                # Safe fallback: if brand query returned 0 items, search broader category query
                if not retrieved_products and primary_brand:
                    broad_query = intent.category if intent.category != "general" else request.query
                    logger.info("Brand search '%s' returned 0 results; falling back to broad query '%s'", search_query, broad_query)
                    broad_res = ProductService.search_live_products(
                        db=db, query=broad_query, limit=TARGET_ASSISTANT_RETRIEVAL_LIMIT, client=c_client
                    )
                    retrieved_products = broad_res.items

            if not retrieved_products:
                catalog_res = ProductService.search_products(
                    db=db, query=search_query, page_size=TARGET_ASSISTANT_RETRIEVAL_LIMIT
                )
                retrieved_products = catalog_res.items
                if not retrieved_products:
                    broad_query = intent.category if intent.category != "general" else request.query
                    catalog_res = ProductService.search_products(
                        db=db, query=broad_query, page_size=TARGET_ASSISTANT_RETRIEVAL_LIMIT
                    )
                    retrieved_products = catalog_res.items
        except Channel3Error as exc:
            logger.error("Channel3 product search failed during assistant workflow: %s", exc)
            broad_query = intent.category if intent.category != "general" else request.query
            catalog_res = ProductService.search_products(
                db=db, query=broad_query, page_size=TARGET_ASSISTANT_RETRIEVAL_LIMIT
            )
            retrieved_products = catalog_res.items

        # Deduplicate retrieved products by id and provider_product_id
        seen_ids: set[str] = set()
        seen_provider_ids: set[str] = set()
        deduped_products: list[ProductResponse] = []
        for prod in retrieved_products:
            if prod.id in seen_ids:
                continue
            prov_id = getattr(prod, "provider_product_id", None)
            if prov_id and prov_id in seen_provider_ids:
                continue
            seen_ids.add(prod.id)
            if prov_id:
                seen_provider_ids.add(prov_id)
            deduped_products.append(prod)
        retrieved_products = deduped_products[:TARGET_ASSISTANT_RETRIEVAL_LIMIT]

        # Step 4: Deterministic Product Eligibility Evaluation (RAM, Specs, Budget, Currency)
        eligibility_details: list[ProductEligibilityItem] = []
        qualified_product_ids: list[str] = []
        excluded_product_ids: list[str] = []
        unverified_product_ids: list[str] = []
        currency_mismatch_product_ids: list[str] = []

        for prod in retrieved_products:
            elig = cls.evaluate_product_eligibility(prod, intent)
            eligibility_details.append(elig)
            if elig.is_qualified:
                qualified_product_ids.append(prod.id)
            elif elig.status == "CURRENCY_MISMATCH":
                currency_mismatch_product_ids.append(prod.id)
            elif elig.status == "UNVERIFIED_SPECS":
                unverified_product_ids.append(prod.id)
            else:
                excluded_product_ids.append(prod.id)

        elig_map = {e.product_id: e for e in eligibility_details}

        # Safeguard 3: Deterministic Product Ranking
        # 1. Hard-qualified products (QUALIFIED) rank before unverified, currency mismatch, or excluded products.
        # 2. Within qualification tier, rank by recommendation_score descending.
        # 3. Use lowest price ONLY as tie-breaker between products in the SAME currency.
        def _rank_sort_key(p: ProductResponse) -> tuple[int, int, float, float, str]:
            e = elig_map.get(p.id)
            tier_rank = {
                "QUALIFIED": 0,
                "UNVERIFIED_SPECS": 1,
                "CURRENCY_MISMATCH": 2,
                "EXCLUDED": 3,
            }.get(e.status if e else "EXCLUDED", 3)

            # Sub-sort within tier: products within preferred budget rank before over-budget products
            budget_tier = 1 if (e and e.is_budget_exceeded) else 0

            score_inv = -round(e.recommendation_score if e else 0.0, 1)

            offer_curr = p.offers[0].currency.upper() if p.offers and p.offers[0].currency else ""
            if offer_curr == intent.currency.upper() and p.lowest_price is not None:
                tie_price = p.lowest_price
            else:
                tie_price = float("inf")
            return (tier_rank, budget_tier, score_inv, tie_price, p.id)

        retrieved_products.sort(key=_rank_sort_key)
        eligibility_details = [elig_map[p.id] for p in retrieved_products if p.id in elig_map]

        # Step 5: Authoritative Deterministic Mandate Policy Evaluation (Individually per product)
        mandate_eval_summary = None
        mandate_context = None
        per_product_mandate_evals: dict[str, dict[str, Any]] = {}

        if active_mandate is not None:
            for prod in retrieved_products:
                elig = elig_map.get(prod.id)
                if not elig:
                    continue

                if prod.lowest_price is not None:
                    currency_to_eval = (
                        prod.offers[0].currency if prod.offers and prod.offers[0].currency else "USD"
                    )
                    eval_res = SpendingPolicyEngine.evaluate(
                        mandate=active_mandate,
                        unit_price=prod.lowest_price,
                        quantity=1,
                        currency=currency_to_eval,
                    )
                    elig.mandate_status = eval_res.decision  # "ALLOWED", "WARNING_OVERRIDE_REQUIRED", or "BLOCKED"
                    elig.mandate_reasons = eval_res.reasons
                    if eval_res.is_budget_exceeded:
                        elig.is_budget_exceeded = True
                        elig.budget_exceeded_amount = eval_res.budget_exceeded_amount
                        elig.is_override_eligible = eval_res.is_override_eligible
                    per_product_mandate_evals[prod.id] = eval_res.model_dump()
                else:
                    elig.mandate_status = "BLOCKED"
                    elig.mandate_reasons = ["Product pricing is unavailable to evaluate against active spending mandate."]
                    per_product_mandate_evals[prod.id] = {
                        "decision": "BLOCKED",
                        "is_allowed": False,
                        "reasons": elig.mandate_reasons,
                        "purchase_amount": 0.0,
                        "quantity": 1,
                        "currency": active_mandate.currency,
                        "mandate_budget": active_mandate.total_budget,
                        "mandate_max_item_price": active_mandate.maximum_item_price,
                        "mandate_max_quantity": active_mandate.maximum_quantity,
                        "remaining_budget_after_purchase": active_mandate.total_budget,
                    }

            # Select target product for overall session evaluation summary
            # Prefer top qualified product that is ALLOWED; else top qualified; else first product that is ALLOWED; else first product
            eval_target_prod = None
            if qualified_product_ids:
                eval_target_prod = next(
                    (p for p in retrieved_products if p.id in qualified_product_ids and elig_map[p.id].mandate_status == "ALLOWED"),
                    None,
                )
                if not eval_target_prod:
                    eval_target_prod = next((p for p in retrieved_products if p.id in qualified_product_ids), None)

            if not eval_target_prod and retrieved_products:
                eval_target_prod = next(
                    (p for p in retrieved_products if elig_map[p.id].mandate_status == "ALLOWED"),
                    retrieved_products[0],
                )

            if eval_target_prod and eval_target_prod.id in per_product_mandate_evals:
                mandate_eval_summary = per_product_mandate_evals[eval_target_prod.id].copy()
                mandate_eval_summary["allowed_product_ids"] = [
                    p.id for p in retrieved_products if elig_map[p.id].mandate_status == "ALLOWED"
                ]
                mandate_eval_summary["warning_override_product_ids"] = [
                    p.id for p in retrieved_products if elig_map[p.id].mandate_status == "WARNING_OVERRIDE_REQUIRED"
                ]
                mandate_eval_summary["blocked_product_ids"] = [
                    p.id for p in retrieved_products if elig_map[p.id].mandate_status == "BLOCKED"
                ]
                mandate_eval_summary["per_product_evaluations"] = per_product_mandate_evals

            mandate_context = {
                "has_active_mandate": True,
                "mandate_budget": active_mandate.total_budget,
                "mandate_max_item_price": active_mandate.maximum_item_price,
                "mandate_currency": active_mandate.currency,
                "per_product_evaluations": [
                    {
                        "product_id": p.id,
                        "name": p.name,
                        "price": p.lowest_price,
                        "mandate_status": elig_map[p.id].mandate_status if p.id in elig_map else "NOT_APPLICABLE",
                        "reasons": elig_map[p.id].mandate_reasons if p.id in elig_map else [],
                    }
                    for p in retrieved_products[:MAX_ASSISTANT_GEMINI_ANALYSIS]
                ],
            }
        else:
            for prod in retrieved_products:
                elig = elig_map.get(prod.id)
                if elig:
                    elig.mandate_status = "NOT_APPLICABLE"
                    elig.mandate_reasons = []
            mandate_eval_summary = None
            mandate_context = {"has_active_mandate": False}

        # Step 6: Grounded Product Intelligence & Analysis (Top MAX_ASSISTANT_GEMINI_ANALYSIS)
        ai_analyses: list[ProductIntelligence] = []
        top_prods_for_analysis = retrieved_products[:MAX_ASSISTANT_GEMINI_ANALYSIS]
        if top_prods_for_analysis and not is_ai_degraded:
            prod_dicts = [p.model_dump() for p in top_prods_for_analysis]
            try:
                ai_analyses = g_client.analyze_products(
                    query=request.query,
                    intent=intent,
                    products=prod_dicts,
                    eligibility_map=elig_map,
                )
                ai_analyses = cls.validate_and_sanitize_intelligence(ai_analyses, elig_map)
            except GeminiError as exc:
                logger.warning("Product intelligence analysis failed: %s", exc)
                is_ai_degraded = True
                degraded_reason = f"Product intelligence degraded: {str(exc)}"

        # Step 6.5: Grounded Multi-Product Comparison Matrix & Technical Explanation
        comparison_explanation = None
        comparison_matrix_data = None
        comparison_price_summary = None
        comparison_context = None

        if len(retrieved_products) >= 2:
            top_compare_prods = retrieved_products[:MAX_ASSISTANT_COMPARISON_LIMIT]
            top_compare_ids = [p.id for p in top_compare_prods]
            try:
                compare_res = ProductService.compare_products(
                    db=db,
                    product_ids=top_compare_ids,
                    include_ai=not is_ai_degraded,
                    intent=intent,
                    gemini_client=g_client,
                    products_override=top_compare_prods,
                )
                comparison_matrix_data = compare_res.comparison_matrix
                comparison_price_summary = compare_res.price_summary
                comparison_explanation = compare_res.ai_explanation
                comparison_context = {
                    "comparison_matrix": comparison_matrix_data,
                    "price_summary": comparison_price_summary,
                    "comparison_explanation": comparison_explanation.model_dump() if comparison_explanation else None,
                }
            except Exception as comp_exc:
                logger.warning("Assistant comparison matrix generation failed: %s", comp_exc)

        # Prepare enriched product evidence for conversational synthesis
        enriched_prods_for_synthesis = []
        for p in retrieved_products:
            p_dict = p.model_dump()
            if p.id in elig_map:
                p_dict["eligibility"] = elig_map[p.id].model_dump()
            enriched_prods_for_synthesis.append(p_dict)

        # Step 7: Conversational Message Synthesis (Enforcing Absolute Consistency)
        assistant_message: str
        if not is_ai_degraded and retrieved_products:
            try:
                try:
                    assistant_message = g_client.generate_conversational_response(
                        query=request.query,
                        history=request.conversation_context,
                        intent=intent,
                        products=enriched_prods_for_synthesis,
                        analyses=ai_analyses,
                        mandate_context=mandate_context,
                        comparison_context=comparison_context,
                    )
                except TypeError:
                    assistant_message = g_client.generate_conversational_response(
                        query=request.query,
                        history=request.conversation_context,
                        intent=intent,
                        products=enriched_prods_for_synthesis,
                        analyses=ai_analyses,
                        mandate_context=mandate_context,
                    )
            except GeminiError:
                assistant_message = cls._generate_deterministic_summary(
                    intent=intent,
                    products=retrieved_products,
                    mandate_eval=mandate_eval_summary,
                    eligibility_items=eligibility_details,
                )
        else:
            assistant_message = cls._generate_deterministic_summary(
                intent=intent,
                products=retrieved_products,
                mandate_eval=mandate_eval_summary,
                eligibility_items=eligibility_details,
            )

        return AssistantQueryResponse(
            success=True,
            session_id=request.session_id,
            intent=intent,
            products=retrieved_products,
            qualified_product_ids=qualified_product_ids,
            excluded_product_ids=excluded_product_ids,
            unverified_product_ids=unverified_product_ids,
            currency_mismatch_product_ids=currency_mismatch_product_ids,
            eligibility_details=eligibility_details,
            ai_analyses=ai_analyses,
            assistant_message=assistant_message,
            mandate_evaluation=mandate_eval_summary,
            comparison_explanation=comparison_explanation,
            comparison_matrix=comparison_matrix_data,
            price_summary=comparison_price_summary,
            is_ai_degraded=is_ai_degraded,
            degraded_reason=degraded_reason,
        )

    @staticmethod
    def _generate_deterministic_summary(
        intent: ShoppingIntent,
        products: list[ProductResponse],
        mandate_eval: dict[str, Any] | None,
        eligibility_items: list[ProductEligibilityItem] | None = None,
    ) -> str:
        """
        Generate consistent, transparent conversational summary.
        - Accurately reports product counts.
        - Highlights qualified recommendations.
        - Explains excluded, unverified, or currency-mismatched items.
        - Never claims an excluded product qualified.
        - Keeps all currency labels visible.
        - Supports German, French, and English deterministic summaries.
        """
        lang = (intent.detected_language or "en").lower()

        # ---------------------------------------------------------------------
        # German (de) deterministic fallback
        # ---------------------------------------------------------------------
        if lang == "de":
            if not products:
                return (
                    f"Wir haben bei Live-Händlern nach '{intent.query}' gesucht, konnten jedoch keine passenden Produkte finden. "
                    f"Versuchen Sie, Ihre Suchbegriffe zu erweitern oder Ihr Budget anzupassen."
                )

            eligibility_items = eligibility_items or []
            eligibility_map = {item.product_id: item for item in eligibility_items}

            qualified = [p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].is_qualified]
            currency_mismatch = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "CURRENCY_MISMATCH"
            ]
            unverified = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "UNVERIFIED_SPECS"
            ]
            excluded = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "EXCLUDED"
            ]

            lines: list[str] = []

            if qualified:
                lines.append(f"Wir haben {len(qualified)} passende Option(en) gefunden, die alle Ihre Anforderungen erfüllen:")
                for idx, p in enumerate(qualified, 1):
                    offer = p.offers[0] if p.offers else None
                    curr = offer.currency if offer else "EUR"
                    price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Preis nicht verfügbar"
                    retailer_str = f"bei {offer.retailer_name}" if offer else ""
                    lines.append(f"{idx}. {p.name} — {price_str} {retailer_str}".strip())
            else:
                req_desc = []
                if intent.max_budget:
                    req_desc.append(f"Budget unter {intent.max_budget:,.2f} {intent.currency}")
                for r in intent.required_specs:
                    req_desc.append(f"{r.name} {r.value}")
                req_str = " (" + ", ".join(req_desc) + ")" if req_desc else ""

                lines.append(
                    f"Wir haben {len(products)} Produkt(e) für '{intent.query}' abgerufen, aber keines erfüllte alle Ihre Anforderungen{req_str}:"
                )

            if currency_mismatch:
                lines.append(
                    f"\n⚠️ Währungshinweis: {len(currency_mismatch)} Produkt(e) werden von verfügbaren Händlern in USD angeboten. "
                    f"Da kein verifizierter Echtzeit-Wechselkursdienst konfiguriert ist, können diese USD-Angebote nicht für Ihr {intent.currency}-Budget zertifiziert werden:"
                )
                for p in currency_mismatch[:5]:
                    offer = p.offers[0] if p.offers else None
                    curr = offer.currency if offer else "USD"
                    price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Preis nicht verfügbar"
                    retailer_str = f"bei {offer.retailer_name}" if offer else ""
                    lines.append(f"  • {p.name} — {price_str} {retailer_str}".strip())

            if unverified:
                lines.append(
                    f"\nℹ️ Nicht verifizierte Spezifikationen: Für {len(unverified)} Produkt(e) fehlen detaillierte Händlerangaben zu Ihren geforderten Hardware-Kriterien."
                )

            if excluded:
                lines.append(
                    f"\n❌ Ausgeschlossen: {len(excluded)} Produkt(e) erfüllen nicht Ihre Mindestanforderungen an Hardware oder Budget."
                )

            if mandate_eval:
                allowed_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "ALLOWED"]
                blocked_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "BLOCKED"]

                if (allowed_items and not blocked_items) or (not blocked_items and mandate_eval.get("decision") == "ALLOWED"):
                    lines.append("\n✅ Ausgabenrichtlinie: Der Kauf ist im Rahmen Ihres aktiven Sitzungsmandats ERLAUBT.")
                elif allowed_items and blocked_items:
                    lines.append(
                        f"\n📋 Ausgabenrichtlinie: {len(allowed_items)} Produkt(e) im Rahmen Ihres Mandats ERLAUBT; "
                        f"{len(blocked_items)} Produkt(e) überschreiten die Preis- oder Budgetgrenzen."
                    )
                else:
                    reasons = " ".join(mandate_eval.get("reasons", []))
                    lines.append(f"\n⚠️ Ausgabenrichtlinie: BLOCKIERT durch Ihr Sitzungsmandat. {reasons}")

            return "\n".join(lines)

        # ---------------------------------------------------------------------
        # French (fr) deterministic fallback
        # ---------------------------------------------------------------------
        elif lang == "fr":
            if not products:
                return (
                    f"Nous avons recherché '{intent.query}' auprès des commerçants partenaires, mais aucun produit correspondant n'a été trouvé. "
                    f"Essayez d'élargir votre recherche ou d'ajuster votre budget."
                )

            eligibility_items = eligibility_items or []
            eligibility_map = {item.product_id: item for item in eligibility_items}

            qualified = [p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].is_qualified]
            currency_mismatch = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "CURRENCY_MISMATCH"
            ]
            unverified = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "UNVERIFIED_SPECS"
            ]
            excluded = [
                p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "EXCLUDED"
            ]

            lines = []

            if qualified:
                lines.append(f"Nous avons trouvé {len(qualified)} option(s) qualifiée(s) correspondant à tous vos critères :")
                for idx, p in enumerate(qualified, 1):
                    offer = p.offers[0] if p.offers else None
                    curr = offer.currency if offer else "EUR"
                    price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Prix non disponible"
                    retailer_str = f"chez {offer.retailer_name}" if offer else ""
                    lines.append(f"{idx}. {p.name} — {price_str} {retailer_str}".strip())
            else:
                req_desc = []
                if intent.max_budget:
                    req_desc.append(f"budget inférieur à {intent.max_budget:,.2f} {intent.currency}")
                for r in intent.required_specs:
                    req_desc.append(f"{r.name} {r.value}")
                req_str = " (" + ", ".join(req_desc) + ")" if req_desc else ""

                lines.append(
                    f"Nous avons récupéré {len(products)} produit(s) pour '{intent.query}', mais aucun ne répond à l'ensemble de vos critères{req_str} :"
                )

            if currency_mismatch:
                lines.append(
                    f"\n⚠️ Avis de devise : {len(currency_mismatch)} produit(s) sont tarifés en USD par les commerçants disponibles. "
                    f"Faute de service de conversion de devises en temps réel vérifié, ces offres en USD ne peuvent être certifiées conformes à votre budget en {intent.currency} :"
                )
                for p in currency_mismatch[:5]:
                    offer = p.offers[0] if p.offers else None
                    curr = offer.currency if offer else "USD"
                    price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Prix non disponible"
                    retailer_str = f"chez {offer.retailer_name}" if offer else ""
                    lines.append(f"  • {p.name} — {price_str} {retailer_str}".strip())

            if unverified:
                lines.append(
                    f"\nℹ️ Spécifications non vérifiées : {len(unverified)} produit(s) ne comportent pas les détails techniques fournis par le commerçant pour vos critères requis."
                )

            if excluded:
                lines.append(
                    f"\n❌ Exclus : {len(excluded)} produit(s) ne répondent pas à vos exigences minimales de matériel ou de budget."
                )

            if mandate_eval:
                allowed_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "ALLOWED"]
                blocked_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "BLOCKED"]

                if (allowed_items and not blocked_items) or (not blocked_items and mandate_eval.get("decision") == "ALLOWED"):
                    lines.append("\n✅ Politique de dépenses : L'achat est AUTORISÉ selon votre mandat de session actif.")
                elif allowed_items and blocked_items:
                    lines.append(
                        f"\n📋 Politique de dépenses : {len(allowed_items)} produit(s) AUTORISÉ(S) selon votre mandat ; "
                        f"{len(blocked_items)} produit(s) dépassent les restrictions de prix ou de budget."
                    )
                else:
                    reasons = " ".join(mandate_eval.get("reasons", []))
                    lines.append(f"\n⚠️ Politique de dépenses : BLOQUÉ selon votre mandat de session. {reasons}")

            return "\n".join(lines)

        # ---------------------------------------------------------------------
        # English (default) deterministic summary (preserves existing behavior)
        # ---------------------------------------------------------------------
        if not products:
            return (
                f"We searched live retailers for '{intent.query}', but could not find any available matching products. "
                f"Try broadening your search terms or adjusting your budget ceiling."
            )

        eligibility_items = eligibility_items or []
        eligibility_map = {item.product_id: item for item in eligibility_items}

        qualified = [p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].is_qualified]
        currency_mismatch = [
            p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "CURRENCY_MISMATCH"
        ]
        unverified = [
            p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "UNVERIFIED_SPECS"
        ]
        excluded = [
            p for p in products if eligibility_map.get(p.id) and eligibility_map[p.id].status == "EXCLUDED"
        ]

        lines = []

        if qualified:
            lines.append(f"Found {len(qualified)} qualified option(s) matching all your requirements:")
            for idx, p in enumerate(qualified, 1):
                offer = p.offers[0] if p.offers else None
                curr = offer.currency if offer else "USD"
                price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Price unavailable"
                retailer_str = f"at {offer.retailer_name}" if offer else ""
                lines.append(f"{idx}. {p.name} — {price_str} {retailer_str}".strip())
        else:
            req_desc = []
            if intent.max_budget:
                req_desc.append(f"budget under {intent.max_budget:,.2f} {intent.currency}")
            for r in intent.required_specs:
                req_desc.append(f"{r.name} {r.value}")
            req_str = " (" + ", ".join(req_desc) + ")" if req_desc else ""

            lines.append(
                f"We retrieved {len(products)} product(s) for '{intent.query}', but none met all your specified requirements{req_str}:"
            )

        # Mention currency mismatches if present
        if currency_mismatch:
            lines.append(
                f"\n⚠️ Currency Notice: {len(currency_mismatch)} product(s) are priced in USD by available retailers. "
                f"Because no verified real-time FX conversion service is configured, these USD offers cannot be certified as within your {intent.currency} budget:"
            )
            for p in currency_mismatch[:5]:
                offer = p.offers[0] if p.offers else None
                curr = offer.currency if offer else "USD"
                price_str = f"{p.lowest_price:,.2f} {curr}" if p.lowest_price else "Price unavailable"
                retailer_str = f"at {offer.retailer_name}" if offer else ""
                lines.append(f"  • {p.name} — {price_str} {retailer_str}".strip())

        # Mention unverified specifications if present
        if unverified:
            lines.append(
                f"\nℹ️ Unverified Specifications: {len(unverified)} product(s) lack merchant-provided specification details for your required hardware criteria."
            )

        # Mention excluded items if present
        if excluded:
            lines.append(
                f"\n❌ Excluded: {len(excluded)} product(s) did not meet your required hardware minimums or budget limits."
            )

        # Spending Mandate policy section
        if mandate_eval:
            allowed_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "ALLOWED"]
            blocked_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "BLOCKED"]
            warning_items = [e for e in (eligibility_items or []) if getattr(e, "mandate_status", None) == "WARNING_OVERRIDE_REQUIRED"]

            if (allowed_items and not blocked_items and not warning_items) or (not blocked_items and not warning_items and mandate_eval.get("decision") == "ALLOWED"):
                lines.append("\n✅ Spending Policy: Purchase is ALLOWED under your active session mandate.")
            elif mandate_eval.get("decision") == "WARNING_OVERRIDE_REQUIRED" or (warning_items and not blocked_items):
                reasons = " ".join(mandate_eval.get("reasons", []))
                lines.append(f"\n⚠️ Spending Policy: Exceeds preferred budget limit. Purchase requires your explicit budget override confirmation to proceed. {reasons}")
            elif allowed_items and blocked_items:
                lines.append(
                    f"\n📋 Spending Policy: {len(allowed_items)} product(s) are ALLOWED under your active session mandate; "
                    f"{len(blocked_items)} product(s) exceed mandate price or budget restrictions."
                )
            else:
                reasons = " ".join(mandate_eval.get("reasons", []))
                lines.append(f"\n⚠️ Spending Policy: BLOCKED under your session mandate. {reasons}")

        return "\n".join(lines)

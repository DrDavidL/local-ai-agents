"""openFDA Drug Label API client.

Free API, no key required. Returns FDA drug labeling data (SPL format)
for both prescription and OTC medications.

API docs: https://open.fda.gov/apis/drug/label/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov/drug/label.json"
TIMEOUT = 15.0

# Common drug abbreviations → FDA generic names (unambiguous only)
DRUG_ABBREVIATIONS: dict[str, str] = {
    "hctz": "hydrochlorothiazide",
    "apap": "acetaminophen",
    "asa": "aspirin",
    "inh": "isoniazid",
    "mtx": "methotrexate",
    "5-fu": "fluorouracil",
    "6-mp": "mercaptopurine",
    "azt": "zidovudine",
    "tmp-smx": "sulfamethoxazole-trimethoprim",
    "tmp/smx": "sulfamethoxazole-trimethoprim",
    "smz-tmp": "sulfamethoxazole-trimethoprim",
    "mmf": "mycophenolate mofetil",
    "epi": "epinephrine",
    "ntg": "nitroglycerin",
}

# Sections to extract from prescription drug labels
RX_SECTIONS = [
    "indications_and_usage",
    "dosage_and_administration",
    "contraindications",
    "warnings_and_cautions",
    "boxed_warning",
    "adverse_reactions",
    "drug_interactions",
    "mechanism_of_action",
    "overdosage",
    "pregnancy",
    "geriatric_use",
    "pediatric_use",
]

# OTC labels use different section names
OTC_SECTIONS = [
    "indications_and_usage",
    "dosage_and_administration",
    "active_ingredient",
    "purpose",
    "warnings",
    "do_not_use",
    "ask_doctor",
    "ask_doctor_or_pharmacist",
    "stop_use",
    "pregnancy_or_breast_feeding",
    "keep_out_of_reach_of_children",
    "overdosage",
]


@dataclass
class DrugLabel:
    brand_name: str
    generic_name: str
    sections: dict[str, str] = field(default_factory=dict)


def _extract_text(value: list | str | None, max_chars: int = 8000) -> str:
    """Extract text from an API field (usually a single-element list).

    The cap is generous — display-level truncation happens in the bot.
    """
    if not value:
        return ""
    if isinstance(value, list):
        return " ".join(value)[:max_chars]
    return str(value)[:max_chars]


def search_drug(name: str) -> DrugLabel | None:
    """Search openFDA for a drug label by name.

    Returns a single label. If multiple products match, returns None —
    use search_drug_options() to get the full list for user selection.
    """
    options = search_drug_options(name)
    if len(options) == 1:
        return options[0]
    return None


def search_drug_options(name: str, limit: int = 5) -> list[DrugLabel]:
    """Search openFDA and return deduplicated product options.

    Resolves common abbreviations (e.g., HCTZ → hydrochlorothiazide),
    then tries brand_name, generic_name, and substance_name searches.
    Deduplicates by normalized generic name so each distinct product
    (single-ingredient vs. combos) appears once.
    """
    # Resolve abbreviations before searching
    name = DRUG_ABBREVIATIONS.get(name.lower().strip(), name)

    search_strategies = [
        f'openfda.brand_name:"{name}"',
        f'openfda.generic_name:"{name}"',
        f'openfda.substance_name:"{name}"',
    ]

    for search_query in search_strategies:
        try:
            resp = httpx.get(
                BASE_URL,
                params={"search": search_query, "limit": limit},
                timeout=TIMEOUT,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                return _deduplicate([_parse_label(r) for r in results])
        except httpx.HTTPStatusError:
            continue
        except Exception as exc:
            logger.error("openFDA search failed for '%s': %s", name, exc)
            return []

    return []


def _normalize_generic(name: str) -> str:
    """Normalize generic name for dedup: uppercase, hyphens → AND."""
    return name.upper().strip().replace("-", " AND ").replace("  ", " ")


def _deduplicate(labels: list[DrugLabel]) -> list[DrugLabel]:
    """Deduplicate labels by normalized generic name.

    Keeps the first occurrence of each unique generic name.
    Sorts single-ingredient products before combinations.
    """
    seen: set[str] = set()
    unique: list[DrugLabel] = []
    for label in labels:
        key = _normalize_generic(label.generic_name)
        if key and key not in seen:
            seen.add(key)
            unique.append(label)

    # Sort: single-ingredient first (no "AND" in normalized name)
    unique.sort(key=lambda label: " AND " in _normalize_generic(label.generic_name))
    return unique


def _parse_label(result: dict) -> DrugLabel:
    """Parse an openFDA result into a DrugLabel."""
    openfda = result.get("openfda", {})
    brand_names = openfda.get("brand_name", [])
    generic_names = openfda.get("generic_name", [])

    brand = brand_names[0] if brand_names else ""
    generic = generic_names[0] if generic_names else ""

    # Determine if OTC or Rx based on available sections
    is_otc = "purpose" in result or "do_not_use" in result
    section_keys = OTC_SECTIONS if is_otc else RX_SECTIONS

    sections: dict[str, str] = {}
    for key in section_keys:
        text = _extract_text(result.get(key))
        if text:
            sections[key] = text

    # Also grab 'warnings' if present (OTC uses this instead of warnings_and_cautions)
    if "warnings" in result and "warnings" not in sections and "warnings_and_cautions" not in sections:
        sections["warnings"] = _extract_text(result.get("warnings"))

    return DrugLabel(brand_name=brand, generic_name=generic, sections=sections)

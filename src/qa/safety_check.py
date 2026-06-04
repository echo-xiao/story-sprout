"""Safety checks for generated picture book content."""

import logging
import re
from typing import Any

from src.agent.gemini_client import generate_json
from src.config import AGE_PRESETS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists for fast pre-screening
# ---------------------------------------------------------------------------

VIOLENCE_KEYWORDS: list[str] = [
    "kill", "murder", "stab", "blood", "gore", "wound", "slash", "strangle",
    "decapitate", "torture", "weapon", "gun", "knife", "sword", "shoot",
    "beat up", "punch", "slaughter", "dismember", "corpse", "dead body",
]

FEAR_KEYWORDS: list[str] = [
    "nightmare", "horror", "terrif", "scream", "demon", "devil", "hell",
    "monster ate", "devour", "eaten alive", "haunted", "possessed", "ghost",
    "zombie", "skeleton attack", "blood-curdling",
]

ADULT_KEYWORDS: list[str] = [
    "sex", "nude", "naked", "drug", "alcohol", "drunk", "cocaine", "heroin",
    "cigarette", "smoking", "beer", "wine", "vodka", "whiskey", "marijuana",
    "suicide", "self-harm", "abuse", "assault", "profanity",
]

_ALL_KEYWORD_GROUPS: dict[str, list[str]] = {
    "violence": VIOLENCE_KEYWORDS,
    "fear": FEAR_KEYWORDS,
    "adult_themes": ADULT_KEYWORDS,
}


def _keyword_scan(text: str) -> list[dict[str, str]]:
    """Return list of {keyword, category} found in *text*."""
    text_lower = text.lower()
    hits: list[dict[str, str]] = []
    for category, keywords in _ALL_KEYWORD_GROUPS.items():
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw)}", text_lower):
                hits.append({"keyword": kw, "category": category})
    return hits


def _gemini_safety_check(text: str, age_group: str) -> dict[str, Any]:
    """Use Gemini zero-shot classification to evaluate content safety.

    Returns a dict with ``is_safe`` (bool) and ``reason`` (str).
    """
    age_desc = AGE_PRESETS.get(age_group, {}).get("description", age_group)
    prompt = (
        "You are a child-safety content reviewer.\n"
        f"Target audience: children aged {age_group} ({age_desc}).\n\n"
        "Evaluate the following picture-book text for child-appropriateness. "
        "Consider violence, fear, adult themes, inappropriate language, and "
        "anything that could upset or confuse a young child.\n\n"
        f"TEXT:\n{text}\n\n"
        "Respond in JSON with exactly these fields:\n"
        '  "is_safe": true/false,\n'
        '  "score": 0.0-1.0 (1 = perfectly safe),\n'
        '  "reason": "brief explanation"\n'
    )
    try:
        result = generate_json(prompt)
        return {
            "is_safe": bool(result.get("is_safe", True)),
            "score": float(result.get("score", 1.0)),
            "reason": str(result.get("reason", "")),
        }
    except Exception as e:
        logger.warning("Gemini safety check failed, falling back to keyword-only: %s", e)
        return {"is_safe": True, "score": 1.0, "reason": "Gemini unavailable; keyword check only."}


def check_safety(pages: list[dict], age_group: str) -> dict[str, Any]:
    """Run safety checks on every page of the picture book.

    Args:
        pages: List of page dicts, each expected to have a ``text`` key.
        age_group: One of the keys in ``AGE_PRESETS`` (e.g. ``"2-4"``).

    Returns:
        {
            is_safe: bool,
            flagged_pages: [{page: int, reason: str}, ...],
            overall_score: float   # 0.0 - 1.0 (1 = perfectly safe)
        }
    """
    flagged_pages: list[dict[str, Any]] = []
    page_scores: list[float] = []

    # Collect all text for a single Gemini call (cheaper & faster)
    all_text = "\n\n".join(
        f"[Page {i + 1}] {p.get('text', '')}" for i, p in enumerate(pages)
    )

    # --- keyword pre-screen per page ---
    for idx, page in enumerate(pages):
        text = page.get("text", "")
        hits = _keyword_scan(text)
        if hits:
            reasons = ", ".join(f"{h['keyword']} ({h['category']})" for h in hits)
            flagged_pages.append({"page": idx + 1, "reason": f"Keyword flags: {reasons}"})

    # --- Gemini zero-shot classification (whole book at once) ---
    gemini_result = _gemini_safety_check(all_text, age_group)
    page_scores.append(gemini_result["score"])

    if not gemini_result["is_safe"]:
        # Gemini flagged the overall text — add a note
        flagged_pages.append({"page": 0, "reason": f"Gemini: {gemini_result['reason']}"})

    overall_score = min(page_scores) if page_scores else 1.0
    # Keyword hits also degrade the score
    if flagged_pages:
        keyword_penalty = min(len(flagged_pages) * 0.15, 0.6)
        overall_score = max(0.0, overall_score - keyword_penalty)

    is_safe = overall_score >= 0.6 and all(
        "Keyword flags" not in fp.get("reason", "") for fp in flagged_pages
    )

    return {
        "is_safe": is_safe,
        "flagged_pages": flagged_pages,
        "overall_score": round(overall_score, 2),
    }

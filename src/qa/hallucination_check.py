"""Hallucination check: detect entities in the picture book that are absent from the original."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    _SPACY_AVAILABLE = False
    logger.info("spaCy not installed; hallucination check will use basic heuristics.")


def _load_spacy_model() -> Any:
    """Load the spaCy model, downloading if needed."""
    from src.config import SPACY_MODEL
    try:
        return spacy.load(SPACY_MODEL)
    except OSError:
        logger.info("Downloading spaCy model '%s'...", SPACY_MODEL)
        spacy.cli.download(SPACY_MODEL)  # type: ignore[attr-defined]
        return spacy.load(SPACY_MODEL)


def _extract_entities_spacy(text: str, nlp: Any) -> set[str]:
    """Extract named entities using spaCy NER."""
    doc = nlp(text)
    entities: set[str] = set()
    for ent in doc.ents:
        # Normalize: lowercase and strip
        normalized = ent.text.strip().lower()
        if len(normalized) > 1:  # Skip single-char "entities"
            entities.add(normalized)
    return entities


def _extract_entities_basic(text: str) -> set[str]:
    """Cheap fallback: extract capitalized multi-word phrases as probable entities."""
    import re
    # Match sequences of capitalized words (2+ chars each)
    pattern = r"\b([A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,})*)\b"
    matches = re.findall(pattern, text)
    return {m.strip().lower() for m in matches if len(m.strip()) > 1}


# Common words that frequently appear as false-positive "new entities"
_COMMON_WORDS: set[str] = {
    "the", "a", "an", "this", "that", "he", "she", "it", "they", "we",
    "one", "two", "three", "first", "last", "once", "upon", "time",
    "day", "night", "morning", "today", "tomorrow", "yesterday",
    "little", "big", "old", "new", "good", "great", "dear",
    "mr", "mrs", "miss", "sir", "page", "chapter", "book",
}


def check_hallucinations(
    pages: list[dict],
    original_text: str,
    acceptable_threshold: float = 0.3,
) -> dict[str, Any]:
    """Compare entities in the picture book against the original text.

    Entities that appear in the picture book but NOT in the original are
    flagged as potential hallucinations.

    Args:
        pages: Picture book pages, each with a ``text`` key.
        original_text: The full original source text.
        acceptable_threshold: Max fraction of new entities before flagging.

    Returns:
        {
            hallucination_score: float,   # 0.0 (no hallucinations) - 1.0
            new_entities: [str, ...],
            is_acceptable: bool,
        }
    """
    book_text = " ".join(p.get("text", "") for p in pages)

    if not book_text.strip():
        return {"hallucination_score": 0.0, "new_entities": [], "is_acceptable": True}

    if _SPACY_AVAILABLE:
        nlp = _load_spacy_model()
        book_entities = _extract_entities_spacy(book_text, nlp)
        original_entities = _extract_entities_spacy(original_text, nlp)
    else:
        book_entities = _extract_entities_basic(book_text)
        original_entities = _extract_entities_basic(original_text)

    # Also check if entity text appears anywhere in the original (substring match)
    original_lower = original_text.lower()
    truly_new: list[str] = []
    for ent in book_entities:
        if ent in _COMMON_WORDS:
            continue
        if ent in original_entities:
            continue
        if ent in original_lower:
            continue
        truly_new.append(ent)

    total_book_entities = len(book_entities - _COMMON_WORDS)
    if total_book_entities == 0:
        hallucination_score = 0.0
    else:
        hallucination_score = len(truly_new) / total_book_entities

    hallucination_score = round(hallucination_score, 2)
    is_acceptable = hallucination_score <= acceptable_threshold

    return {
        "hallucination_score": hallucination_score,
        "new_entities": sorted(truly_new),
        "is_acceptable": is_acceptable,
    }

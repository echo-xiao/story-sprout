"""Coverage check: verify that key events from the original work appear in the picture book."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Try to import sentence-transformers; gracefully degrade if unavailable.
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np

    _SBERT_AVAILABLE = True
except ImportError:
    _SBERT_AVAILABLE = False
    logger.info(
        "sentence-transformers not installed; coverage check will use basic text overlap."
    )


def _cosine_similarity(a: Any, b: Any) -> float:
    """Compute cosine similarity between two vectors."""
    dot = float(np.dot(a, b))
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    return dot / norm if norm > 0 else 0.0


def _basic_overlap(text_a: str, text_b: str) -> float:
    """Compute Jaccard word-overlap as a cheap similarity proxy."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _get_event_text(event: dict) -> str:
    """Extract a text description from an event dict."""
    # Support various schema shapes
    for key in ("description", "text", "summary", "event", "title"):
        if key in event and event[key]:
            return str(event[key])
    return str(event)


def check_coverage(
    pages: list[dict],
    key_events: list[dict],
    original_segments: list[dict],
    similarity_threshold: float = 0.45,
) -> dict[str, Any]:
    """Check how well the picture book covers the key events from the original.

    Args:
        pages: Picture book pages, each with a ``text`` key.
        key_events: Key events extracted during analysis (each has a text description).
        original_segments: Original text segments (for additional context).
        similarity_threshold: Minimum similarity to consider an event "covered".

    Returns:
        {
            coverage_score: float,          # 0.0 - 1.0
            covered_events: [str, ...],
            missed_events: [str, ...],
        }
    """
    if not key_events:
        return {"coverage_score": 1.0, "covered_events": [], "missed_events": []}

    page_texts = [p.get("text", "") for p in pages]
    all_book_text = " ".join(page_texts)
    event_descriptions = [_get_event_text(e) for e in key_events]

    covered_events: list[str] = []
    missed_events: list[str] = []

    if _SBERT_AVAILABLE:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        page_embeddings = model.encode(page_texts, show_progress_bar=False)
        event_embeddings = model.encode(event_descriptions, show_progress_bar=False)

        for i, event_desc in enumerate(event_descriptions):
            # Best similarity between this event and any page
            best_sim = max(
                _cosine_similarity(event_embeddings[i], pe) for pe in page_embeddings
            )
            if best_sim >= similarity_threshold:
                covered_events.append(event_desc)
            else:
                missed_events.append(event_desc)
    else:
        # Fallback: basic word overlap
        for event_desc in event_descriptions:
            best_overlap = max(
                _basic_overlap(event_desc, pt) for pt in page_texts
            ) if page_texts else 0.0
            # Also check against the full concatenated text
            full_overlap = _basic_overlap(event_desc, all_book_text)
            best_score = max(best_overlap, full_overlap)

            if best_score >= 0.15:  # Lower threshold for Jaccard
                covered_events.append(event_desc)
            else:
                missed_events.append(event_desc)

    total = len(event_descriptions)
    coverage_score = len(covered_events) / total if total > 0 else 1.0

    return {
        "coverage_score": round(coverage_score, 2),
        "covered_events": covered_events,
        "missed_events": missed_events,
    }

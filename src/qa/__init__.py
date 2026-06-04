"""QA pipeline for picture book validation."""

import logging
from typing import Any

from src.qa.safety_check import check_safety
from src.qa.readability_check import check_readability
from src.qa.coverage_check import check_coverage
from src.qa.hallucination_check import check_hallucinations

logger = logging.getLogger(__name__)

__all__ = [
    "check_safety",
    "check_readability",
    "check_coverage",
    "check_hallucinations",
    "run_qa_pipeline",
]


def run_qa_pipeline(
    pages: list[dict],
    analysis: dict[str, Any],
    original_text: str,
    age_group: str,
) -> dict[str, Any]:
    """Run the full QA pipeline on a generated picture book.

    Args:
        pages: List of page dicts (each with at least a ``text`` key).
        analysis: The analysis dict from Layer 3 (should contain ``key_events``
                  and optionally ``segments``).
        original_text: The full original source text.
        age_group: Target age group key (e.g. ``"4-6"``).

    Returns:
        {
            passes: bool,
            safety: { ... },
            readability: { ... },
            coverage: { ... },
            hallucination: { ... },
            summary: str,
        }
    """
    logger.info("Running QA pipeline for %d pages, age group '%s'", len(pages), age_group)

    # --- Safety ---
    logger.info("  [1/4] Safety check...")
    safety_result = check_safety(pages, age_group)

    # --- Readability ---
    logger.info("  [2/4] Readability check...")
    readability_result = check_readability(pages, age_group)

    # --- Coverage ---
    logger.info("  [3/4] Coverage check...")
    key_events = analysis.get("key_events", [])
    original_segments = analysis.get("segments", [])
    coverage_result = check_coverage(pages, key_events, original_segments)

    # --- Hallucination ---
    logger.info("  [4/4] Hallucination check...")
    hallucination_result = check_hallucinations(pages, original_text)

    # --- Aggregate ---
    passes = all([
        safety_result["is_safe"],
        readability_result["passes"],
        coverage_result["coverage_score"] >= 0.5,
        hallucination_result["is_acceptable"],
    ])

    issues: list[str] = []
    if not safety_result["is_safe"]:
        issues.append(f"Safety: {len(safety_result['flagged_pages'])} page(s) flagged")
    if not readability_result["passes"]:
        failing = sum(1 for p in readability_result["per_page"] if p["issues"])
        issues.append(f"Readability: {failing} page(s) with issues")
    if coverage_result["coverage_score"] < 0.5:
        issues.append(
            f"Coverage: {len(coverage_result['missed_events'])} key event(s) missed "
            f"(score: {coverage_result['coverage_score']})"
        )
    if not hallucination_result["is_acceptable"]:
        issues.append(
            f"Hallucination: {len(hallucination_result['new_entities'])} new entity(ies) "
            f"(score: {hallucination_result['hallucination_score']})"
        )

    summary = "All checks passed." if passes else "Issues found: " + "; ".join(issues)
    logger.info("  QA result: %s", summary)

    return {
        "passes": passes,
        "safety": safety_result,
        "readability": readability_result,
        "coverage": coverage_result,
        "hallucination": hallucination_result,
        "summary": summary,
    }

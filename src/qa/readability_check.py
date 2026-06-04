"""Readability checks for generated picture book text."""

import logging
from typing import Any

import textstat

from src.config import AGE_PRESETS

logger = logging.getLogger(__name__)


def _analyze_page(text: str, age_group: str) -> dict[str, Any]:
    """Analyze a single page's readability against the age preset.

    Returns a per-page report dict.
    """
    preset = AGE_PRESETS.get(age_group, AGE_PRESETS["4-6"])

    words = text.split()
    word_count = len(words)

    # Flesch-Kincaid Grade Level (textstat returns a float)
    grade_level = textstat.flesch_kincaid_grade(text) if word_count > 0 else 0.0

    # Sentence-level analysis
    sentences = textstat.sentence_count(text) if word_count > 0 else 0
    avg_sentence_length = word_count / max(sentences, 1)

    issues: list[str] = []

    # Check grade level
    fk_max = preset["flesch_kincaid_max"]
    if grade_level > fk_max:
        issues.append(
            f"Grade level {grade_level:.1f} exceeds max {fk_max} for age group {age_group}"
        )

    # Check word count
    max_words = preset["max_words_per_page"]
    if word_count > max_words:
        issues.append(
            f"Word count {word_count} exceeds max {max_words} for age group {age_group}"
        )

    # Check sentence length
    max_sentence_len = preset["max_sentence_length"]
    if avg_sentence_length > max_sentence_len:
        issues.append(
            f"Avg sentence length {avg_sentence_length:.1f} words exceeds max {max_sentence_len}"
        )

    # Check individual sentences for outliers
    raw_sentences = text.replace("!", ".").replace("?", ".").split(".")
    for sent in raw_sentences:
        sent = sent.strip()
        sent_words = len(sent.split())
        if sent_words > max_sentence_len * 1.5 and sent_words > 3:
            issues.append(
                f"Long sentence ({sent_words} words): \"{sent[:60]}...\""
                if len(sent) > 60
                else f"Long sentence ({sent_words} words): \"{sent}\""
            )

    return {
        "grade_level": round(grade_level, 1),
        "word_count": word_count,
        "sentence_count": sentences,
        "avg_sentence_length": round(avg_sentence_length, 1),
        "issues": issues,
    }


def check_readability(pages: list[dict], age_group: str) -> dict[str, Any]:
    """Check readability of all pages against the target age group.

    Args:
        pages: List of page dicts, each with a ``text`` key.
        age_group: One of the keys in ``AGE_PRESETS`` (e.g. ``"4-6"``).

    Returns:
        {
            passes: bool,
            per_page: [{page: int, grade_level: float, word_count: int, issues: [str]}, ...]
        }
    """
    per_page: list[dict[str, Any]] = []
    all_pass = True

    for idx, page in enumerate(pages):
        text = page.get("text", "")
        if not text.strip():
            per_page.append({
                "page": idx + 1,
                "grade_level": 0.0,
                "word_count": 0,
                "issues": [],
            })
            continue

        analysis = _analyze_page(text, age_group)
        report = {
            "page": idx + 1,
            "grade_level": analysis["grade_level"],
            "word_count": analysis["word_count"],
            "issues": analysis["issues"],
        }
        per_page.append(report)

        if analysis["issues"]:
            all_pass = False

    return {
        "passes": all_pass,
        "per_page": per_page,
    }

"""_coerce_score / _normalize_page_quality (gemini_consistency_check.py).

These shape every quality payload the frontend renders; LLM output is dirty
by nature so the normalizer must absorb anything.
"""

from __future__ import annotations

from src.generation.gemini_consistency_check import (
    _PAGE_QUALITY_DIMENSIONS,
    _coerce_score,
    _normalize_page_quality,
)


def test_coerce_score_values():
    assert _coerce_score(85) == 85
    assert _coerce_score("85") == 85
    assert _coerce_score(85.6) == 86
    assert _coerce_score(150) == 100   # clamp high
    assert _coerce_score(-5) == 0      # clamp low
    assert _coerce_score(None) == 100  # missing -> benign default
    assert _coerce_score("n/a") == 100
    assert _coerce_score({}) == 100


def test_normalize_non_dict_is_qa_failed():
    result = _normalize_page_quality("totally not a dict")
    assert result["qa_failed"] is True
    for dim in _PAGE_QUALITY_DIMENSIONS:
        assert isinstance(result[dim], dict)


def test_normalize_fills_missing_dimensions_and_lists():
    result = _normalize_page_quality({})
    for dim in _PAGE_QUALITY_DIMENSIONS:
        assert result[dim]["score"] == 100
    assert result["character_consistency"]["characters"] == []
    assert result["spelling"]["errors"] == []
    assert result["duplicate_characters"]["duplicates"] == []
    assert result["name_face_mismatch"]["mismatches"] == []
    assert result["character_count"]["missing"] == []
    assert result["character_count"]["extra"] == []
    assert result["regeneration_feedback"] == ""
    assert result["overall_score"] == 100


def test_normalize_recomputes_overall_from_dimensions():
    raw = {
        "overall_score": 40,  # headline disagrees with dimensions — recompute
        "character_consistency": {"score": "90"},
        "spelling": {"score": 100},
        "duplicate_characters": {"score": 80},
        "name_face_mismatch": {"score": None},
        "character_count": {"score": 30},
    }
    result = _normalize_page_quality(raw)
    assert result["overall_score"] == round((90 + 100 + 80 + 100 + 30) / 5)


def test_normalize_preserves_existing_list_content():
    raw = {"spelling": {"score": 60, "errors": [{"word": "Gatsbyy"}]}}
    result = _normalize_page_quality(raw)
    assert result["spelling"]["errors"] == [{"word": "Gatsbyy"}]
    assert result["spelling"]["score"] == 60

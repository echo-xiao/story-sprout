"""Tests for tolerant QA JSON parsing (Task 6).

_parse_quality_json must handle:
- markdown fences (```json ... ```)
- trailing commas before } or ]
- prose wrapping around the JSON object
- fully unparseable input → returns {"overall_score": None, "parse_error": ...}
"""

import src.generation.gemini_consistency_check as qa


def test_parse_tolerates_fenced_and_trailing_comma():
    raw = '```json\n{"overall_score": 91, "character_consistency": {"score": 90, "issues": [],}}\n```'
    parsed = qa._parse_quality_json(raw)
    assert parsed["overall_score"] == 91


def test_parse_unparseable_returns_none_not_raise():
    parsed = qa._parse_quality_json("the character looks great, no json here")
    assert parsed.get("overall_score") is None
    assert "parse_error" in parsed

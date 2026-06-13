"""Root cause A: ONE normalization for quality; failure is None, not 100.

- Both page and sheet QA recompute overall_score from their dimensions, so
  neither trusts the LLM's headline (a sheet with appearance_match=20 must NOT
  pass on a headline of 85 → a broken sheet would degrade the whole book).
- A failed QA call reports overall_score None (UNKNOWN), distinct by type from a
  perfect 100, so no consumer can mistake "call failed" for "perfect".
"""

from __future__ import annotations

import json
import re
import pathlib

import src.generation.gemini_consistency_check as qc


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)


def _client_returning(payload):
    class _Models:
        def generate_content(self, **k):
            return _Resp(payload)

    class _Client:
        models = _Models()
    return _Client()


def test_sheet_overall_recomputed_not_headline(monkeypatch):
    monkeypatch.setattr(qc, "_load_image_part", lambda p: {"img": p})
    # LLM lies: headline 85 while every dimension is 20.
    monkeypatch.setattr(qc, "_get_client", lambda: _client_returning({
        "overall_score": 85,
        "appearance_match": {"score": 20}, "internal_consistency": {"score": 20},
        "multi_angle": {"score": 20}, "style_quality": {"score": 20},
        "text_labels": {"score": 20},
    }))
    result = qc.check_character_sheet_quality("sheet.png", "Alice", "blue dress")
    # Recomputed mean (20), NOT the headline 85 → self-correct can trigger.
    assert result["overall_score"] == 20


def test_page_qa_failure_is_none_not_100(monkeypatch):
    monkeypatch.setattr(qc, "_load_image_part", lambda p: {"img": p})

    class _Boom:
        class models:
            @staticmethod
            def generate_content(**k):
                raise RuntimeError("vision 429")
    monkeypatch.setattr(qc, "_get_client", lambda: _Boom())

    result = qc.check_page_quality("page.png", [], "text", ["Alice"])
    assert result["overall_score"] is None
    assert result.get("qa_failed") is True


def test_sentinel_constructors_have_no_100_overall():
    assert qc._empty_page_quality()["overall_score"] is None
    assert qc._empty_sheet_quality()["overall_score"] is None


def test_no_overall_score_100_sentinel_remains():
    """grep invariant: no code assigns overall_score = 100 (the old sentinel);
    a real verdict's overall is only ever the mean from _recompute_overall."""
    src = pathlib.Path(qc.__file__).read_text()
    assert not re.search(r'["\']overall_score["\']\s*:\s*100\b', src)
    # The only runtime writer of a real overall is _recompute_overall.
    writers = re.findall(r'result\[["\']overall_score["\']\]\s*=', src)
    assert len(writers) == 1, f"overall_score written in {len(writers)} places, expected 1"


def test_fatal_name_face_caps_page_overall():
    """A name-face mismatch (that dim 0) on an otherwise-perfect page must NOT
    average to 80 and skip self-correct — the fatal dim caps the overall."""
    result = qc._normalize_page_quality({
        "character_consistency": {"score": 100},
        "spelling": {"score": 100},
        "duplicate_characters": {"score": 100},
        "name_face_mismatch": {"score": 0},
        "character_count": {"score": 100},
    })
    assert result["overall_score"] == 0


def test_high_fatal_dim_does_not_cap():
    """A merely-imperfect fatal dim (>= 50) doesn't cap — overall is the mean."""
    result = qc._normalize_page_quality({
        "character_consistency": {"score": 80},
        "spelling": {"score": 80},
        "duplicate_characters": {"score": 80},
        "name_face_mismatch": {"score": 80},
        "character_count": {"score": 80},
    })
    assert result["overall_score"] == 80

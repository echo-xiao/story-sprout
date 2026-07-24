"""qa_and_self_correct file/state machine (src/generation/page_service.py).

This is the shared self-correction policy used by both the pipeline and the
single-page regen endpoint — exactly the code the upcoming bug fixes touch,
so every branch is pinned here. check_page_quality is stubbed; everything
else (file moves, restores, quality report writes) is real.
"""

from __future__ import annotations

import json

import pytest

import src.config as _cfg
import src.core.store as _store
import src.generation.gemini_consistency_check as gcc
from src.generation.page_service import qa_and_self_correct, sheet_qa_and_self_correct


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """One page image + dirs + a programmable QA stub.

    Returns a dict; set ``qa_results`` to a list of dicts that successive
    check_page_quality calls will return.
    """
    pages = tmp_path / "pages"
    pages.mkdir()
    img = pages / "page_001.png"
    img.write_bytes(b"OLD")

    state = {
        "img": img,
        "history": tmp_path / "history",
        "quality": tmp_path / "quality" / "page_001_quality.json",
        "qa_results": [],
        "qa_calls": 0,
        "regen_feedback": None,
    }

    def fake_qa(path, sheets, text, chars, page_num):
        result = state["qa_results"][state["qa_calls"]]
        state["qa_calls"] += 1
        return dict(result)

    monkeypatch.setattr(gcc, "check_page_quality", fake_qa)
    return state


def run(state, regenerate_fn=None, **kwargs):
    return qa_and_self_correct(
        image_path=str(state["img"]),
        character_sheets=[],
        expected_text="text",
        expected_characters=[],
        page_num=1,
        seg_id=7,
        history_dir=state["history"],
        quality_path=state["quality"],
        regenerate_fn=regenerate_fn or (lambda fb: ""),
        **kwargs,
    )


def written_quality(state):
    return json.loads(state["quality"].read_text())


def test_good_score_skips_regeneration(setup):
    setup["qa_results"] = [{"overall_score": 90, "regeneration_feedback": "fine"}]
    result = run(setup, regenerate_fn=lambda fb: pytest.fail("must not regenerate"))

    assert setup["img"].read_bytes() == b"OLD"
    assert result["page"] == 1 and result["segment_id"] == 7
    assert "self_correct_attempted" not in result
    assert written_quality(setup)["overall_score"] == 90


def test_low_score_without_feedback_skips_regeneration(setup):
    setup["qa_results"] = [{"overall_score": 10, "regeneration_feedback": ""}]
    run(setup, regenerate_fn=lambda fb: pytest.fail("must not regenerate"))
    assert setup["img"].read_bytes() == b"OLD"


def test_retry_better_keeps_new_image(setup):
    setup["qa_results"] = [
        {"overall_score": 30, "regeneration_feedback": "fix the face"},
        {"overall_score": 80},
    ]

    def regen(feedback):
        setup["regen_feedback"] = feedback
        setup["img"].write_bytes(b"NEW")
        return str(setup["img"])

    result = run(setup, regenerate_fn=regen)

    assert setup["regen_feedback"] == "fix the face"
    assert setup["img"].read_bytes() == b"NEW"
    # the original is preserved in history
    backup = setup["history"] / "page_001_selfcorrect_prev.png"
    assert backup.read_bytes() == b"OLD"
    assert result["self_correct"] == {"old_score": 30, "new_score": 80, "kept": "new"}
    assert written_quality(setup)["overall_score"] == 80


def test_retry_worse_restores_old_image_and_report(setup):
    setup["qa_results"] = [
        {"overall_score": 40, "regeneration_feedback": "fix"},
        {"overall_score": 20},
    ]

    def regen(feedback):
        setup["img"].write_bytes(b"NEW")
        return str(setup["img"])

    result = run(setup, regenerate_fn=regen)

    assert setup["img"].read_bytes() == b"OLD"
    assert result["overall_score"] == 40
    assert result["self_correct"]["kept"] == "old"
    assert written_quality(setup)["overall_score"] == 40


def test_regen_failure_restores_old_image(setup):
    setup["qa_results"] = [{"overall_score": 10, "regeneration_feedback": "fix"}]
    result = run(setup, regenerate_fn=lambda fb: "")

    assert setup["img"].read_bytes() == b"OLD"
    assert result["self_correct_attempted"] is True
    assert result["overall_score"] == 10


def test_qa_failed_on_retry_keeps_original(setup):
    """A failed QA run reports overall_score None (UNKNOWN, not a perfect 100) —
    it must NOT win the keep-the-better comparison."""
    setup["qa_results"] = [
        {"overall_score": 40, "regeneration_feedback": "fix"},
        {"overall_score": None, "qa_failed": True},
    ]

    def regen(feedback):
        setup["img"].write_bytes(b"NEW")
        return str(setup["img"])

    result = run(setup, regenerate_fn=regen)

    assert setup["img"].read_bytes() == b"OLD"
    assert result["overall_score"] == 40
    assert result["self_correct"]["new_score"] is None
    assert result["self_correct"]["kept"] == "old"


def test_self_correct_disabled_never_regenerates(setup):
    setup["qa_results"] = [{"overall_score": 0, "regeneration_feedback": "fix"}]
    run(setup, regenerate_fn=lambda fb: pytest.fail("must not regenerate"),
        self_correct=False)
    assert setup["img"].read_bytes() == b"OLD"


# ---------------------------------------------------------------------------
# GCS dual-write tests (Task 1)
# ---------------------------------------------------------------------------

def _make_fake_bucket():
    """Return a fresh in-memory bucket + the backing dict (so tests can inspect it)."""
    backing: dict = {}

    class _Blob:
        def __init__(self, key):
            self._key = key

        def exists(self):
            return self._key in backing

        def download_as_text(self):
            return backing[self._key]

        def upload_from_string(self, data, content_type="application/json"):
            backing[self._key] = data

    class _Bucket:
        def blob(self, key):
            return _Blob(key)

    return _Bucket(), backing


@pytest.fixture()
def gcs_setup(tmp_path, monkeypatch):
    """Like ``setup`` but quality_path is UNDER config.GENERATED_DIR (= tmp_path)
    so relative_to(GENERATED_DIR) succeeds and we can assert the store write."""
    import src.config as _cfg
    monkeypatch.setattr(_cfg, "GENERATED_DIR", tmp_path)

    bucket, store_backing = _make_fake_bucket()
    monkeypatch.setattr(_store, "_bucket", lambda: bucket)

    pages = tmp_path / "book1" / "chapters" / "ch00" / "pages"
    pages.mkdir(parents=True)
    img = pages / "page_001.png"
    img.write_bytes(b"PIXEL")

    quality_path = tmp_path / "book1" / "chapters" / "ch00" / "quality" / "page_001_quality.json"

    state = {
        "img": img,
        "history": tmp_path / "book1" / "chapters" / "ch00" / "history",
        "quality": quality_path,
        "qa_results": [],
        "qa_calls": 0,
        "store_backing": store_backing,
    }

    def fake_qa(path, sheets, text, chars, page_num):
        result = state["qa_results"][state["qa_calls"]]
        state["qa_calls"] += 1
        return dict(result)

    monkeypatch.setattr(gcc, "check_page_quality", fake_qa)
    return state


def run_gcs(state, regenerate_fn=None, **kwargs):
    return qa_and_self_correct(
        image_path=str(state["img"]),
        character_sheets=[],
        expected_text="text",
        expected_characters=[],
        page_num=1,
        seg_id=1,
        history_dir=state["history"],
        quality_path=state["quality"],
        regenerate_fn=regenerate_fn or (lambda fb: ""),
        **kwargs,
    )


def test_page_qa_writes_to_gcs(gcs_setup):
    """A passing QA result should be persisted to the store (backend-agnostic).
    Asserts via store.get_json so the test runs on both GCS and Firestore backends."""
    gcs_setup["qa_results"] = [{"overall_score": 85, "regeneration_feedback": "ok"}]
    run_gcs(gcs_setup)

    expected_key = "book1/chapters/ch00/quality/page_001_quality.json"
    stored = _store.get_json(expected_key)
    assert stored is not None, "QA JSON was not written to the store"
    assert stored["overall_score"] == 85


def test_page_qa_no_gcs_write_when_score_is_none(gcs_setup):
    """A failed QA call (overall_score None) must NOT write to the store (no stale data).
    Asserts via store.get_json — meaningful on both GCS and Firestore backends."""
    gcs_setup["qa_results"] = [{"overall_score": None, "qa_failed": True}]
    run_gcs(gcs_setup)

    expected_key = "book1/chapters/ch00/quality/page_001_quality.json"
    stored = _store.get_json(expected_key)
    assert stored is None, "Failed QA must not be persisted to the store"


def test_sheet_qa_writes_to_gcs(tmp_path, monkeypatch):
    """sheet_qa_and_self_correct persists to the store when overall_score is set.
    Asserts via store.get_json — works on both GCS and Firestore backends."""
    import src.config as _cfg
    import src.generation.gemini_consistency_check as qc

    monkeypatch.setattr(_cfg, "GENERATED_DIR", tmp_path)

    monkeypatch.setattr(qc, "check_character_sheet_quality",
                        lambda *a, **kw: {"overall_score": 72})

    sheet_path = tmp_path / "book1" / "characters" / "alice_sheet.png"
    sheet_path.parent.mkdir(parents=True)
    sheet_path.write_bytes(b"SHEET")

    quality_path = tmp_path / "book1" / "characters" / "quality" / "alice_quality.json"

    sheet_qa_and_self_correct(
        sheet_path=str(sheet_path),
        char_name="Alice",
        appearance="blue dress",
        visual_details={},
        gender="female",
        role="protagonist",
        history_dir=tmp_path / "book1" / "characters" / "history",
        quality_path=quality_path,
        regenerate_fn=None,
    )

    expected_key = "book1/characters/quality/alice_quality.json"
    stored = _store.get_json(expected_key)
    assert stored is not None, "Sheet QA JSON was not written to the store"
    assert stored["overall_score"] == 72


def test_gcs_persist_graceful_on_value_error(setup, monkeypatch):
    """quality_path outside GENERATED_DIR logs a warning but never raises."""
    # setup uses tmp_path (not under GENERATED_DIR) — relative_to raises ValueError.
    # The except clause should swallow it.
    setup["qa_results"] = [{"overall_score": 60, "regeneration_feedback": "ok"}]
    result = run(setup)
    # The local file must still be written even when GCS persist fails silently.
    assert written_quality(setup)["overall_score"] == 60
    assert result["overall_score"] == 60

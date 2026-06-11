"""regen-status marker robustness (routes/generation.py).

The frontend polls GET /segment/{id}/regen-status every few seconds while the
backend writes the marker non-atomically — a poll landing mid-write must not
turn into a 500 (review finding P1-10).
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_segment


@pytest.fixture()
def regen_env(monkeypatch, tmp_path):
    analysis = {"segments": [make_segment(0)]}
    monkeypatch.setattr(
        "src.routes.generation._load_json",
        lambda book_id, filename: analysis if filename == "analysis.json" else {},
    )
    monkeypatch.setattr("src.routes.generation.GENERATED_DIR", tmp_path)
    ch_base = tmp_path / "somebook" / "chapters" / "ch00"
    ch_base.mkdir(parents=True)
    return ch_base


def test_no_marker_means_generating(client, regen_env):
    resp = client.get("/api/book/somebook/segment/0/regen-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "generating"


def test_complete_marker_is_returned(client, regen_env):
    marker = regen_env / "regen_0.json"
    marker.write_text(json.dumps({"status": "complete", "segment_id": 0, "page_number": 1}))
    resp = client.get("/api/book/somebook/segment/0/regen-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"


def test_unknown_segment_is_unknown(client, regen_env):
    resp = client.get("/api/book/somebook/segment/42/regen-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"


@pytest.mark.xfail(
    strict=True,
    reason="BUG P1-10 (CODE_REVIEW_2026-06-11.md): marker is written "
    "non-atomically and read with a bare json.loads — a poll that races the "
    "write gets a 500 instead of 'generating'.",
)
def test_half_written_marker_does_not_500(client, regen_env):
    marker = regen_env / "regen_0.json"
    marker.write_text('{"status": "comp')  # torn write
    resp = client.get("/api/book/somebook/segment/0/regen-status")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("generating", "error")

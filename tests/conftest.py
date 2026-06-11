"""Shared fixtures.

Conventions used across this suite:

- Tests that lock in CURRENT correct behavior are plain tests (must stay green).
- Tests that document a KNOWN BUG from CODE_REVIEW_2026-06-11.md are marked
  @pytest.mark.xfail(strict=True, reason="BUG #N ..."). They fail today by
  design; once the bug is fixed they XPASS and strict=True turns that into a
  hard failure — forcing the fixer to delete the marker, which converts the
  test into a permanent regression test.
- No test may touch the network (MongoDB Atlas / Gemini). Anything that could
  is monkeypatched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """TestClient that returns 500s instead of raising server exceptions."""
    from src.app import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def require_user_key(monkeypatch):
    """Turn on the BYOK gate for the duration of a test.

    BYOKMiddleware re-imports REQUIRE_USER_KEY from src.config on every
    dispatch, so patching the module attribute is enough.
    """
    monkeypatch.setattr("src.config.REQUIRE_USER_KEY", True)


def make_segment(seg_id: int, ch_idx: int = 0, words: int = 20, **extra) -> dict:
    """A minimal analysis segment with enough words not to be skipped."""
    seg = {
        "id": seg_id,
        "chapter_idx": ch_idx,
        "text": " ".join(f"word{i}" for i in range(words)),
        "characters_in_scene": [],
        "character_actions": [],
        "scene_background": "",
        "scene_summary": f"summary of segment {seg_id}",
        "sentiment": "neutral",
    }
    seg.update(extra)
    return seg

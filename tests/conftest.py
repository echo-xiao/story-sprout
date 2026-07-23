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


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch):
    """Pin REQUIRE_USER_KEY OFF for every test unless it opts in via the
    `require_user_key` fixture. The production default is now ON (fail-safe), but
    most tests exercise route logic that the gate would 403 before reaching — so
    the suite's baseline is gate-off and gated behavior is tested explicitly. The
    middlewares re-import REQUIRE_USER_KEY from src.config per dispatch, so
    patching the module attribute is authoritative. (The default *value* itself
    is verified in test_require_user_key_default.py, which reloads the module.)"""
    monkeypatch.setattr("src.config.REQUIRE_USER_KEY", False)
    # Single shared-passcode gate off by default too — tests hitting generation
    # endpoints shouldn't 403; test_access_code_gate exercises the gate explicitly.
    monkeypatch.setattr("src.config.ACCESS_CODE", "", raising=False)


@pytest.fixture(autouse=True)
def _no_real_gcs(monkeypatch):
    """Force the image storage layer to its local-file fallback for every test —
    never reach the real GCS bucket (the production default). Tests that need a
    tmp dir also patch src.core.storage.GENERATED_DIR."""
    monkeypatch.setattr("src.core.storage.GCS_BUCKET", "", raising=False)


@pytest.fixture(autouse=True)
def _fake_store_bucket(monkeypatch):
    """Point the GCS-JSON store at an in-memory bucket for every test, so no test
    ever reaches real GCS (the store has no local fallback — GCS-only by design).
    Per-test fresh. Unit tests that inspect the bucket monkeypatch store._bucket
    themselves; that runs after this autouse fixture, so it wins."""
    class _Blob:
        def __init__(self, s, gen, k):
            self._s, self._gen, self._k = s, gen, k

        @property
        def name(self):
            return self._k

        @property
        def generation(self):
            return self._gen.get(self._k, 0)

        def exists(self):
            return self._k in self._s

        def download_as_text(self):
            return self._s[self._k]

        def upload_from_string(self, data, content_type="application/json",
                               if_generation_match=None):
            # Optimistic-concurrency guard (mirrors GCS): reject a write whose
            # expected generation no longer matches, so the store's retry loop
            # re-reads and re-applies instead of losing a concurrent update.
            if if_generation_match is not None:
                cur = self._gen.get(self._k, 0)
                if cur != if_generation_match:
                    from google.api_core.exceptions import PreconditionFailed
                    raise PreconditionFailed("generation mismatch")
            self._s[self._k] = data
            self._gen[self._k] = self._gen.get(self._k, 0) + 1

    class _Bucket:
        def __init__(self):
            self._s = {}
            self._gen = {}

        def blob(self, key):
            return _Blob(self._s, self._gen, key)

        def get_blob(self, key):
            return _Blob(self._s, self._gen, key) if key in self._s else None

        def list_blobs(self, prefix=""):
            return [_Blob(self._s, self._gen, k) for k in self._s if k.startswith(prefix)]

    bucket = _Bucket()
    monkeypatch.setattr("src.core.store._bucket", lambda: bucket, raising=False)


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """Safety net: clear email-sender credentials that .env's load_dotenv may
    have pulled into the environment, so no test ever sends a real email.
    Tests that exercise a sender set their own creds + patch the transport."""
    for var in ("RESEND_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "FEEDBACK_EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)


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

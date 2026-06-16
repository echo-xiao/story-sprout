"""Admin access: a valid X-Admin-Token bypasses the BYOK gate AND book-ownership,
running generation on the project Vertex backend (no user key) — so the operator
can regenerate the unowned public sample books WITHOUT flipping the global
REQUIRE_USER_KEY switch (which would open generation to everyone).

The admin predicate (is_admin_token) is the single source reused by both
middlewares and the route dependency.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_is_admin_token_predicate(monkeypatch):
    from src.routes.helpers import is_admin_token

    # Unset ADMIN_TOKEN → no backdoor, every check fails safe.
    monkeypatch.setattr("src.config.ADMIN_TOKEN", "")
    assert is_admin_token("anything") is False
    assert is_admin_token(None) is False

    monkeypatch.setattr("src.config.ADMIN_TOKEN", "s3cret")
    assert is_admin_token("s3cret") is True
    assert is_admin_token("wrong") is False
    assert is_admin_token(None) is False
    assert is_admin_token("") is False


def test_require_user_key_admin_bypass(monkeypatch):
    from src.routes.helpers import _require_user_key

    monkeypatch.setattr("src.config.ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr("src.config.REQUIRE_USER_KEY", True)
    # Admin token → None (run on project Vertex), no 403 even without a user key.
    assert _require_user_key(x_gemini_key=None, x_admin_token="s3cret") is None


def test_admin_token_bypasses_byok_and_ownership_middleware(monkeypatch):
    """End-to-end through the middleware stack: a gen endpoint on an unowned book.

    No token + REQUIRE_USER_KEY → BYOK middleware 403. With the admin token the
    request clears BOTH the BYOK gate and book-ownership, reaching the route
    (which 404s here only because the test book has no preprocess data) — proving
    auth passed, not generation.
    """
    monkeypatch.setattr("src.config.ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr("src.config.REQUIRE_USER_KEY", True)

    from src.app import app
    client = TestClient(app)
    path = "/api/book/no_such_book_xyz/chapter/0/generate"

    # No key, gate on → blocked at the BYOK middleware.
    assert client.post(path).status_code == 403

    # Admin token → past BYOK + ownership; route 404s (no preprocess data), not 403.
    r = client.post(path, headers={"X-Admin-Token": "s3cret"})
    assert r.status_code != 403
    assert r.status_code == 404

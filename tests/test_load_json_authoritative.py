"""Task 2B: _load_json must treat the durable store as the SINGLE source of truth.

When a durable backend is configured (STORE_BACKEND="firestore"), a successful
store read — even one that returns None — is AUTHORITATIVE and must never be
shadowed by a stale local /tmp copy.  The local-file fallback is ONLY for when
ALL store read attempts raise (store unconfigured / unreachable).
"""
from __future__ import annotations

import json
import pytest


# ── helpers ─────────────────────────────────────────────────────────────────

def _seed_firestore(fake_fs, book_id: str, filename: str, data) -> None:
    """Write data into the in-memory Firestore fake via the store's own key
    encoding so that store.load_preprocess_file will find it."""
    key = f"{book_id}/preprocess/{filename}"
    doc_id = key.replace("/", "|")
    fake_fs.document(doc_id).set({"key": key, "data": data})


# ── fixture: Firestore backend active ────────────────────────────────────────

@pytest.fixture
def fs_env(monkeypatch, tmp_path, fake_fs):
    """Wire helpers with STORE_BACKEND=firestore and a tmp GENERATED_DIR.

    Returns (helpers_module, fake_fs, tmp_path).
    """
    monkeypatch.setenv("STORE_BACKEND", "firestore")
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")

    # Redirect local preprocess writes to a tmp dir so we can plant stale files.
    import src.routes.helpers as helpers
    monkeypatch.setattr(helpers, "GENERATED_DIR", tmp_path)

    return helpers, fake_fs, tmp_path


def _plant_stale(tmp_path, book_id: str, filename: str, data) -> None:
    """Write a stale JSON file to the local preprocess directory."""
    p = tmp_path / book_id / "preprocess" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


# ── Test 1: store FRESH beats local STALE ────────────────────────────────────

def test_store_authoritative_over_stale_local(fs_env):
    """When the store holds FRESH data and a stale local file also exists,
    _load_json must return the store's value (FRESH), never the local copy (OLD).
    """
    helpers, fake_fs, tmp_path = fs_env

    fresh = {"segments": [{"id": 1, "scene_summary": "FRESH"}]}
    stale = {"segments": [{"id": 1, "scene_summary": "OLD"}]}

    _seed_firestore(fake_fs, "b", "analysis.json", fresh)
    _plant_stale(tmp_path, "b", "analysis.json", stale)

    result = helpers._load_json("b", "analysis.json")

    assert result is not None, "_load_json returned None instead of FRESH data"
    summary = result["segments"][0]["scene_summary"]
    assert summary == "FRESH", (
        f"Expected 'FRESH' from store, got '{summary}' — "
        "stale local copy shadowed the durable store"
    )


# ── Test 2: transient store error → retry → still returns FRESH ──────────────

def test_store_retry_still_returns_fresh(fs_env, monkeypatch):
    """When the store raises once then succeeds, the retry must return FRESH,
    not fall through to the stale local copy.
    """
    helpers, fake_fs, tmp_path = fs_env

    fresh = {"segments": [{"id": 1, "scene_summary": "FRESH"}]}
    stale = {"segments": [{"id": 1, "scene_summary": "OLD"}]}

    _seed_firestore(fake_fs, "b", "analysis.json", fresh)
    _plant_stale(tmp_path, "b", "analysis.json", stale)

    # Patch store.load_preprocess_file to raise on the first call, then
    # delegate to the real implementation on subsequent calls.
    import src.core.store as store_mod

    _real = store_mod.load_preprocess_file
    _call_count = [0]

    def _flaky(book_id, filename):
        _call_count[0] += 1
        if _call_count[0] == 1:
            raise RuntimeError("transient store error")
        return _real(book_id, filename)

    monkeypatch.setattr(store_mod, "load_preprocess_file", _flaky)
    # helpers imports store lazily via "from src.core import store" — patch the
    # attribute on that module object too so the helpers code sees the same mock.
    import src.core.store as _core_store
    monkeypatch.setattr(_core_store, "load_preprocess_file", _flaky)

    result = helpers._load_json("b", "analysis.json")

    assert result is not None, "_load_json returned None"
    summary = result["segments"][0]["scene_summary"]
    assert summary == "FRESH", (
        f"Expected 'FRESH' after retry, got '{summary}'"
    )
    assert _call_count[0] >= 2, "store was not retried"


# ── Test 3: all attempts raise → fall back to local file ─────────────────────

def test_store_all_fail_falls_back_to_local(fs_env, monkeypatch):
    """When ALL store read attempts raise (store unavailable), _load_json must
    fall back to the local file — local-dev behaviour preserved.
    """
    helpers, fake_fs, tmp_path = fs_env

    local_data = {"segments": [{"id": 1, "scene_summary": "LOCAL_FALLBACK"}]}
    _plant_stale(tmp_path, "b", "analysis.json", local_data)

    import src.core.store as store_mod

    def _always_raise(book_id, filename):
        raise RuntimeError("store completely unavailable")

    monkeypatch.setattr(store_mod, "load_preprocess_file", _always_raise)
    import src.core.store as _core_store
    monkeypatch.setattr(_core_store, "load_preprocess_file", _always_raise)

    result = helpers._load_json("b", "analysis.json")

    assert result is not None, "_load_json returned None instead of local fallback"
    summary = result["segments"][0]["scene_summary"]
    assert summary == "LOCAL_FALLBACK", (
        f"Expected local fallback 'LOCAL_FALLBACK', got '{summary}'"
    )


# ── Test 4: store returns None (genuine absent) → return None, not local ──────

def test_store_none_is_authoritative(fs_env, monkeypatch):
    """When the store read succeeds with None (document genuinely absent),
    that answer is authoritative — return None, NOT the stale local copy.
    """
    helpers, fake_fs, tmp_path = fs_env

    stale = {"segments": [{"id": 1, "scene_summary": "OLD"}]}
    _plant_stale(tmp_path, "b", "absent.json", stale)

    # The document is NOT seeded into fake_fs — so the store read returns None.
    # (No monkeypatching needed; empty fake_fs returns None for absent docs.)

    result = helpers._load_json("b", "absent.json")

    assert result is None, (
        f"Store said absent (None) but got local copy '{result}' — "
        "store None must be authoritative, not fall through to local"
    )

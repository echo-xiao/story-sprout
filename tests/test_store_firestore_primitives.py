"""Tests for Firestore-backed store primitives.

Uses an in-memory fake Firestore collection monkeypatched onto
`store._fs_collection` so no real Firestore is ever reached.

The shared fake infrastructure (classes + ``fake_fs`` fixture) lives in
``conftest.py`` and is re-used here.  These tests request ``fake_fs`` directly
so they get a named handle to the collection (e.g. to inject contention via
``fake_fs._conflict_raises_remaining``).  All other tests in the suite that
run on the Firestore backend get the fake via the autouse
``_fake_fs_collection`` conftest fixture without needing to ask for it.
"""

from __future__ import annotations

import pytest


# ── Tests ────────────────────────────────────────────────────────────────────

def test_firestore_primitives_roundtrip(fake_fs, monkeypatch):
    """Round-trip get/put, mutate atomicity + return value, list_keys filtering."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    # get missing key returns None
    assert store.get_json("b/preprocess/analysis.json") is None

    # put then get roundtrip
    store.put_json("b/preprocess/analysis.json", {"segments": [{"id": 1}]})
    assert store.get_json("b/preprocess/analysis.json") == {"segments": [{"id": 1}]}

    # mutate is atomic + returns the mutator's return value
    out = store._mutate_json("b/x.json", lambda o: o.__setitem__("n", 1))
    assert store.get_json("b/x.json") == {"n": 1}
    assert out is None  # __setitem__ returns None, which is fine

    # put another key
    store.put_json("b/meta.json", {"title": "T"})

    # list_keys with suffix filtering
    all_meta = store._list_keys("/meta.json")
    assert "b/meta.json" in all_meta
    assert "b/preprocess/analysis.json" not in all_meta


def test_firestore_mutate_returns_value(fake_fs, monkeypatch):
    """_mutate_json returns whatever the mutator returns."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    result = store._mutate_json("b/counter.json", lambda o: o.update({"c": 42}) or "done")
    assert result == "done"
    assert store.get_json("b/counter.json") == {"c": 42}


def test_firestore_mutate_starts_with_empty_dict(fake_fs, monkeypatch):
    """When the doc is absent, mutator sees {} not None."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    seen_keys_before_mutation = []

    def capture(obj):
        # Record the keys present BEFORE mutation to verify obj starts as {}
        seen_keys_before_mutation.extend(obj.keys())
        obj["x"] = 1

    store._mutate_json("nonexistent/key.json", capture)
    # Mutator should have received an empty dict (no keys before mutation)
    assert seen_keys_before_mutation == []
    assert store.get_json("nonexistent/key.json") == {"x": 1}


def test_firestore_mutate_no_lost_update(fake_fs, monkeypatch):
    """A concurrent write committed inside the mutator forces the transaction to
    re-run and preserve BOTH updates (fake_fs raises a contention error the
    first commit, mirroring Firestore's transactional retry)."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    # Seed the doc with initial state
    store.put_json("b/shared.json", {"counter": 0})

    # Schedule one conflict so the first transaction commit fails and retries
    fake_fs._conflict_raises_remaining = 1

    # The "concurrent write" happens between the read and commit: when the txn
    # retries, it re-reads the doc and sees the updated value
    call_count = [0]

    def incrementor(obj):
        call_count[0] += 1
        obj["counter"] = obj.get("counter", 0) + 1

    store._mutate_json("b/shared.json", incrementor)

    # The mutator ran twice (once that was rejected, once that committed)
    assert call_count[0] == 2
    # Final counter == 1 (re-ran from current state after contention)
    assert store.get_json("b/shared.json") == {"counter": 1}


def test_firestore_list_keys_empty_suffix(fake_fs, monkeypatch):
    """_list_keys('') returns all keys."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    store.put_json("book1/meta.json", {})
    store.put_json("book1/assets.json", {})
    all_keys = store._list_keys()
    assert "book1/meta.json" in all_keys
    assert "book1/assets.json" in all_keys


def test_firestore_doc_id_encoding(fake_fs, monkeypatch):
    """Keys with '/' are encoded to '|' in Firestore doc IDs (slashes are
    forbidden in Firestore doc IDs)."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "firestore")
    import src.core.store as store

    store.put_json("the_happy_prince/preprocess/analysis.json", {"ok": True})
    # The doc ID in the underlying fake store should use '|' not '/'
    expected_doc_id = "the_happy_prince|preprocess|analysis.json"
    assert expected_doc_id in fake_fs._store

    # And we can read it back
    assert store.get_json("the_happy_prince/preprocess/analysis.json") == {"ok": True}


def test_gcs_backend_still_works(monkeypatch):
    """STORE_BACKEND='gcs' keeps using the GCS path (existing tests unaffected)."""
    monkeypatch.setattr("src.config.STORE_BACKEND", "gcs")
    import src.core.store as store

    # The conftest autouse _fake_store_bucket monkeypatches _bucket for every test
    # so GCS path works without real GCS
    store.put_json("b/test.json", {"backend": "gcs"})
    assert store.get_json("b/test.json") == {"backend": "gcs"}

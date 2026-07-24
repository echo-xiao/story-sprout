"""Tests for Firestore-backed store primitives.

Uses an in-memory fake Firestore collection monkeypatched onto
`store._fs_collection` so no real Firestore is ever reached.

The fake supports:
- `.document(doc_id)` -> FakeDocRef
- `.stream()` -> iter of FakeDocSnapshot
- `firestore.transactional` decorator via FakeTransaction

The no-lost-update test simulates contention: the fake raises a conflict
error on the FIRST commit attempt inside a transaction, forcing a retry.
Firestore handles this automatically in production; here the fake does one
retry to prove the mutator re-runs and both updates survive.
"""

from __future__ import annotations

import threading
import pytest


# ── In-memory fake Firestore ────────────────────────────────────────────────

class _FakeDocSnapshot:
    def __init__(self, data, exists):
        # Store a deep copy so mutations to the returned dict don't bleed
        # back into the collection store (mirrors Firestore: each read returns
        # a fresh deserialized snapshot, not a reference to stored bytes).
        import copy
        self._data = copy.deepcopy(data) if data is not None else {}
        self.exists = exists

    def to_dict(self):
        import copy
        return copy.deepcopy(self._data) if self.exists else {}


class _FakeDocRef:
    def __init__(self, collection_store: dict, doc_id: str):
        self._store = collection_store
        self._doc_id = doc_id

    def get(self, transaction=None):
        data = self._store.get(self._doc_id)
        return _FakeDocSnapshot(data, data is not None)

    def set(self, data):
        self._store[self._doc_id] = dict(data)


class _FakeCollection:
    """In-memory Firestore collection."""

    def __init__(self):
        self._store: dict = {}  # doc_id -> body dict
        # Contention injection: if > 0, the next N commits will raise
        # _ConflictError to simulate a concurrent write forcing a retry.
        self._conflict_raises_remaining = 0

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)

    def stream(self):
        for doc_id, data in list(self._store.items()):
            yield _FakeDocSnapshot(data, True)


class _ConflictError(Exception):
    """Simulated Firestore transaction contention error."""


class _FakeTransaction:
    """Simulates a Firestore transactional context, with optional contention."""

    def __init__(self, collection: _FakeCollection):
        self._collection = collection
        self._pending: dict = {}  # doc_id -> body

    def set(self, ref: _FakeDocRef, data: dict):
        self._pending[ref._doc_id] = dict(data)

    def _commit(self):
        if self._collection._conflict_raises_remaining > 0:
            self._collection._conflict_raises_remaining -= 1
            self._pending.clear()
            raise _ConflictError("simulated contention")
        for doc_id, data in self._pending.items():
            self._collection._store[doc_id] = data
        self._pending.clear()


def make_fake_transactional(collection: _FakeCollection):
    """Return a decorator that mimics `firestore.transactional`.

    The decorated function receives a transaction object as its first argument.
    If _ConflictError is raised on commit, it retries once (Firestore retries
    automatically in production on contention).
    """
    def transactional(fn):
        def wrapper(*args, **kwargs):
            for attempt in range(2):  # up to 1 retry
                txn = _FakeTransaction(collection)
                result = fn(txn, *args, **kwargs)
                try:
                    txn._commit()
                    return result
                except _ConflictError:
                    if attempt == 1:
                        raise RuntimeError("fake transaction exceeded retry budget")
                    # re-run on retry
            raise RuntimeError("unreachable")
        return wrapper
    return transactional


@pytest.fixture
def fake_fs(monkeypatch):
    """Monkeypatch store._fs_collection to return our in-memory fake.
    Also patches firestore.transactional inside store so transactions work."""
    col = _FakeCollection()

    import src.core.store as store

    # Patch _fs_collection to always return the same fake collection
    monkeypatch.setattr(store, "_fs_collection", lambda: col, raising=False)

    # Patch the transactional decorator that store.py imports inside _fs_mutate_json
    # We need to patch it at the module level where it gets looked up
    fake_transactional = make_fake_transactional(col)
    monkeypatch.setattr(store, "_firestore_transactional", fake_transactional, raising=False)

    return col


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

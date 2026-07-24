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

import copy

import pytest
from fastapi.testclient import TestClient


# ── In-memory fake Firestore ────────────────────────────────────────────────
#
# Shared by all tests via the _fake_fs_collection autouse fixture below.
# Also re-exported so individual test files (e.g. test_store_firestore_primitives)
# can import and use the same classes instead of defining their own.
#
# Behaviour mirrors real Firestore:
# - Each document has an opaque version counter.
# - A transaction records the version of every document it reads; on commit it
#   verifies those versions haven't changed (optimistic concurrency).  If any
#   has changed it raises _ConflictError, the decorator retries.
# - Direct .set() (outside a transaction, e.g. from put_json) increments the
#   version immediately — so a nested write committed inside a mutator body
#   will be detected by the enclosing transaction on its next commit attempt.
# - _conflict_raises_remaining: manual injection for unit tests that want to
#   force a conflict on the next N commits regardless of actual version state.


class _FakeDocSnapshot:
    def __init__(self, data, exists, version: int = 0):
        # Deep-copy so mutations to the returned dict don't bleed back
        # into the collection store (mirrors Firestore snapshot semantics).
        self._data = copy.deepcopy(data) if data is not None else {}
        self.exists = exists
        self._version = version

    def to_dict(self):
        return copy.deepcopy(self._data) if self.exists else {}


class _FakeDocRef:
    def __init__(self, collection: "_FakeCollection", doc_id: str):
        self._collection = collection
        self._doc_id = doc_id

    # Back-compat: some code reads ref._store directly (test_store_firestore_primitives).
    @property
    def _store(self):
        return self._collection._store

    def get(self, transaction=None):
        data = self._collection._store.get(self._doc_id)
        ver = self._collection._versions.get(self._doc_id, 0)
        snap = _FakeDocSnapshot(data, data is not None, ver)
        if transaction is not None:
            transaction._read_versions[self._doc_id] = ver
        return snap

    def set(self, data):
        """Direct (non-transactional) write — increments the version counter."""
        self._collection._store[self._doc_id] = dict(data)
        self._collection._versions[self._doc_id] = (
            self._collection._versions.get(self._doc_id, 0) + 1
        )


class _FakeCollection:
    """In-memory Firestore collection with version-based conflict detection."""

    def __init__(self):
        self._store: dict = {}     # doc_id -> body dict
        self._versions: dict = {}  # doc_id -> int version counter
        # Manual contention injection: next N commits will raise _ConflictError.
        self._conflict_raises_remaining = 0

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self, doc_id)

    def stream(self):
        for doc_id, data in list(self._store.items()):
            yield _FakeDocSnapshot(data, True, self._versions.get(doc_id, 0))


class _ConflictError(Exception):
    """Simulated Firestore transaction contention error."""


class _FakeTransaction:
    """Simulates a Firestore transactional context.

    On commit:
    1. If _conflict_raises_remaining > 0, raises _ConflictError (manual injection).
    2. Checks that all documents read during this transaction still have the same
       version (optimistic concurrency, mirrors real Firestore).  If any changed,
       raises _ConflictError so the decorator retries.
    3. Commits all pending writes and increments their version counters.
    """

    def __init__(self, collection: _FakeCollection):
        self._collection = collection
        self._pending: dict = {}       # doc_id -> body
        self._read_versions: dict = {} # doc_id -> version at read time

    def set(self, ref: _FakeDocRef, data: dict):
        self._pending[ref._doc_id] = dict(data)

    def _commit(self):
        # 1. Manual injection first.
        if self._collection._conflict_raises_remaining > 0:
            self._collection._conflict_raises_remaining -= 1
            self._pending.clear()
            raise _ConflictError("simulated contention (manual injection)")

        # 2. Optimistic-concurrency check: detect concurrent writes between
        #    our read and now.
        for doc_id, read_ver in self._read_versions.items():
            if doc_id in self._pending:  # only check docs we're about to write
                current_ver = self._collection._versions.get(doc_id, 0)
                if current_ver != read_ver:
                    self._pending.clear()
                    raise _ConflictError(
                        f"version conflict on {doc_id}: "
                        f"read v{read_ver}, now v{current_ver}"
                    )

        # 3. Commit.
        for doc_id, data in self._pending.items():
            self._collection._store[doc_id] = data
            self._collection._versions[doc_id] = (
                self._collection._versions.get(doc_id, 0) + 1
            )
        self._pending.clear()


def make_fake_transactional(collection: _FakeCollection):
    """Return a decorator that mimics ``firestore.transactional``.

    The decorated function receives a transaction object as its first argument.
    On _ConflictError the wrapper retries once (Firestore auto-retries on
    contention in production; the fake mirrors that behaviour so tests that
    inject a conflict—either manually or via a concurrent write inside the
    mutator body—see the mutator re-run with the updated state).
    """
    def transactional(fn):
        # Mirror the REAL firestore.transactional call convention: the decorated
        # function is invoked as `_txn(transaction)` (the caller passes a
        # transaction from the client). The wrapper ignores the passed handle and
        # uses a FRESH _FakeTransaction per attempt so a retry re-reads state.
        def wrapper(transaction, *args, **kwargs):
            for attempt in range(2):  # up to 1 retry
                txn = _FakeTransaction(collection)
                result = fn(txn, *args, **kwargs)
                try:
                    txn._commit()
                    return result
                except _ConflictError:
                    if attempt == 1:
                        raise RuntimeError(
                            "fake transaction exceeded retry budget"
                        )
                    # loop → retry with fresh transaction
            raise RuntimeError("unreachable")
        return wrapper
    return transactional


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
def _fake_fs_collection(monkeypatch):
    """Point the Firestore-JSON store at an in-memory fake for every test.

    This fixture is autouse so no test ever reaches the real Firestore service,
    regardless of whether STORE_BACKEND is 'gcs' or 'firestore'.  On the GCS
    backend the Firestore paths are never called, so the patch is a harmless
    no-op.  On the Firestore backend (e.g. STORE_BACKEND=firestore in the env)
    all four store primitives route to this in-memory fake collection.

    The per-test collection is fresh (empty) for every test.  Tests that need
    a handle to the collection to inject contention should use the named
    ``fake_fs`` fixture below instead; both point at the same collection object.
    """
    import src.core.store as _store

    col = _FakeCollection()
    fake_transactional = make_fake_transactional(col)

    monkeypatch.setattr(_store, "_fs_collection", lambda: col, raising=False)
    monkeypatch.setattr(_store, "_firestore_transactional", fake_transactional, raising=False)
    # _fs_transaction() is the seam that returns a transaction handle for the
    # decorated function; the fake wrapper ignores the handle (fresh txn per
    # attempt) but store._fs_mutate_json still calls it, so provide a stub.
    monkeypatch.setattr(_store, "_fs_transaction", lambda: _FakeTransaction(col), raising=False)

    # Reset the real Firestore client singleton so a future test that re-enables
    # the real path (e.g. one that un-patches _fs_collection) starts fresh.
    monkeypatch.setattr(_store, "_fs_client", None, raising=False)


@pytest.fixture()
def fake_fs(monkeypatch):
    """Return the in-memory _FakeCollection wired into the Firestore store seam.

    This is a named (non-autouse) fixture for tests that need to inspect or
    mutate the collection directly — e.g. to inject contention via
    ``fake_fs._conflict_raises_remaining = 1``.

    The autouse ``_fake_fs_collection`` fixture already patches the seams, so
    this fixture just grabs the collection that the autouse fixture installed
    and returns it.  Both fixtures see the same object.
    """
    import src.core.store as _store
    # _fs_collection() is already patched by the autouse fixture; call it to
    # get the _FakeCollection instance that was installed for this test.
    return _store._fs_collection()


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

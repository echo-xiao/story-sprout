"""Regression tests for the asset-version framework (src.core.db).

Locks in the invariants the whole "pick a version -> it's the one used" feature
rests on:

  1. Selecting is a PURE pointer write — it never adds a version (clicking a
     thumbnail must not spawn a new version).
  2. get_selected_version returns the PICKED version, not just the newest.
  3. Regenerating a byte-identical image dedupes (no version bloat).
  4. The version list is capped.

No network: src.core.db._get_db is monkeypatched to an in-memory fake that
implements just the pymongo surface these functions use.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.core import db


# --------------------------------------------------------------------------
# Minimal in-memory stand-in for the pymongo collection these functions touch.
# --------------------------------------------------------------------------
class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc: dict, query: dict) -> bool:
        for k, v in query.items():
            if k == "versions.id":
                if not any(ver.get("id") == v for ver in doc.get("versions", [])):
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find_one(self, query, projection=None):
        for d in self.docs:
            if self._match(d, query):
                return copy.deepcopy(d)  # pymongo hands back a fresh dict
        return None

    def update_one(self, query, update, upsert=False):
        setv = update.get("$set", {})
        for d in self.docs:
            if self._match(d, query):
                d.update(copy.deepcopy(setv))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            newdoc = {k: v for k, v in query.items() if k != "versions.id"}
            newdoc.update(copy.deepcopy(setv))
            self.docs.append(newdoc)
            return SimpleNamespace(matched_count=0, modified_count=0)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class _FakeDB:
    def __init__(self):
        self.asset_versions = _FakeCollection()


@pytest.fixture()
def fake_db(monkeypatch):
    fdb = _FakeDB()
    monkeypatch.setattr(db, "_get_db", lambda: fdb)
    return fdb


B, T, K = "book1", "scene", "Gatsby's Mansion"


def _versions(fake_db):
    return db.list_asset_versions(B, T, K)["versions"]


def test_select_does_not_add_a_version(fake_db):
    """Clicking a thumbnail (select) must never create a new version."""
    v1 = db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="h1")
    v2 = db.add_asset_version(B, T, K, "http://x/v2.png", image_hash="h2")
    assert len(_versions(fake_db)) == 2

    # Select the older one repeatedly — count stays at 2.
    assert db.set_selected_version(B, T, K, v1) is True
    assert db.set_selected_version(B, T, K, v1) is True
    assert len(_versions(fake_db)) == 2
    assert db.get_selected_version(B, T, K)["id"] == v1  # picked, not newest (v2)


def test_get_selected_returns_picked_not_newest(fake_db):
    v1 = db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="h1")
    db.add_asset_version(B, T, K, "http://x/v2.png", image_hash="h2")
    # Newest (v2) is auto-selected on add; pick v1 back.
    db.set_selected_version(B, T, K, v1)
    assert db.get_selected_version(B, T, K)["id"] == v1


def test_dedupe_identical_hash(fake_db):
    """A regen that produced a byte-identical image adds no new version."""
    v1 = db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="hh")
    again = db.add_asset_version(B, T, K, "http://x/v1-copy.png", image_hash="hh")
    assert again == v1                      # re-selected the existing one
    assert len(_versions(fake_db)) == 1     # no duplicate stored


def test_version_list_is_capped(fake_db):
    for i in range(db._MAX_ASSET_VERSIONS + 5):
        db.add_asset_version(B, T, K, f"http://x/v{i}.png", image_hash=f"h{i}")
    assert len(_versions(fake_db)) == db._MAX_ASSET_VERSIONS


def test_select_unknown_version_is_rejected(fake_db):
    db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="h1")
    assert db.set_selected_version(B, T, K, "does-not-exist") is False


def test_delete_clears_versions(fake_db):
    db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="h1")
    db.delete_asset_versions(B)
    assert db.list_asset_versions(B, T, K) == {"versions": [], "selected_version_id": None}


def test_storage_key_is_stored(fake_db):
    """select must be able to fetch the picked version's bytes — so the storage
    key has to round-trip through the version record."""
    db.add_asset_version(B, T, K, "http://x/v1.png", image_hash="h1",
                         storage_key="bk/scenes/x_h1.png")
    assert db.get_selected_version(B, T, K)["storage_key"] == "bk/scenes/x_h1.png"


def test_mirror_removes_stale_other_extension(monkeypatch, tmp_path):
    """A new .jpg must drop the old .png (and vice-versa) so the serving layer
    can't return the stale image — the png/jpg collision bug."""
    from src.core import storage
    monkeypatch.setattr(storage, "GCS_BUCKET", "fakebucket")  # take the mirror path
    monkeypatch.setattr(storage, "_bucket", lambda: None)     # ...but no real GCS -> local
    monkeypatch.setattr(storage, "GENERATED_DIR", tmp_path)

    pages = tmp_path / "b" / "pages"
    pages.mkdir(parents=True)
    old_png = pages / "page_001.png"
    old_png.write_bytes(b"old")
    new_jpg = pages / "page_001.jpg"
    new_jpg.write_bytes(b"new")

    storage.mirror_to_gcs(new_jpg)

    assert new_jpg.exists()
    assert not old_png.exists()  # stale other-extension copy removed


def test_canonical_current_path_mapping():
    """Locks the asset_key -> live 'current' path mapping (the bug-prone keystone
    that makes a pick land where display/PDF read)."""
    from src.routes.editor import _canonical_current

    _, fbase, skey = _canonical_current("bk", "scene", "Gatsby's Mansion")
    assert fbase == "gatsbys_mansion_scene"
    assert skey == "bk/scenes/gatsbys_mansion_scene"

    _, fbase, skey = _canonical_current("bk", "page", "ch00:p003")
    assert fbase == "page_003"
    assert skey == "bk/chapters/ch00/pages/page_003"

    # An unparseable page key resolves to nothing rather than a wrong path.
    assert _canonical_current("bk", "page", "garbage") == (None, None, None)

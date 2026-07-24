"""Task 2 tests: chapter_data GCS-first reads and dual-write.

Exercises:
  - update_chapter_data_page dual-writes chapter_data.json to the GCS store.
  - download_book_pdf reads chapter_data from GCS on a cold instance (no local files).
  - download_book_pdf falls back to local glob when GCS has no chapter_data keys.
  - storage.localize is called for each page's image_path before export_pdf.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.config as _cfg
import src.core.store as _store
import src.core.storage as _storage
import src.routes.helpers as _helpers
import src.routes.books as _books

# GCS-specific tests seed data directly into a fake GCS bucket dict and then
# expect _store.get_json / _store._list_keys to read from the same dict.  On
# the Firestore backend those calls go to the Firestore fake, not the GCS
# bucket, so the seeded data is never visible.  These tests are marked to skip
# on the Firestore backend; the equivalent Firestore coverage lives in the
# public-API tests and in test_store_firestore_primitives.py.
_SKIP_ON_FIRESTORE = pytest.mark.skipif(
    _cfg.STORE_BACKEND == "firestore",
    reason=(
        "GCS-specific: seeds/reads data via fake GCS bucket internals. "
        "On Firestore backend store.get_json and _list_keys route to the "
        "Firestore fake, making GCS-seeded data invisible."
    ),
)


# ---------------------------------------------------------------------------
# Helper: fake bucket for _store
# ---------------------------------------------------------------------------

class _FakeBucket:
    def __init__(self):
        self._s: dict[str, str] = {}

    def blob(self, key):
        return _FakeBlob(self._s, key)

    def list_blobs(self, prefix=""):
        return [_FakeBlob(self._s, k) for k in self._s if k.startswith(prefix)]


class _FakeBlob:
    def __init__(self, s, k):
        self._s, self._k = s, k

    @property
    def name(self):
        return self._k

    def exists(self):
        return self._k in self._s

    def download_as_text(self):
        return self._s[self._k]

    def upload_from_string(self, data, content_type="application/json"):
        self._s[self._k] = data


# ---------------------------------------------------------------------------
# Step 2: dual-write tests
# ---------------------------------------------------------------------------

class TestUpdateChapterDataPageDualWrite:
    """update_chapter_data_page must persist chapter_data to the GCS store
    (in addition to the local file) so a subsequent cold-start PDF build can
    read it without the local file."""

    @pytest.fixture()
    def wired(self, monkeypatch, tmp_path):
        bucket = _FakeBucket()
        monkeypatch.setattr(_store, "_bucket", lambda: bucket)
        monkeypatch.setattr(_helpers, "GENERATED_DIR", tmp_path)
        ch = tmp_path / "b1" / "chapters" / "ch00"
        ch.mkdir(parents=True)
        (ch / "chapter_data.json").write_text(json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "/x/page_001.png", "text": "hello"}],
        }))
        return bucket, tmp_path

    def test_dual_write_persists_to_store(self, wired):
        bucket, _ = wired
        _helpers.update_chapter_data_page("b1", 0, 1, text="updated")
        stored = _store.get_json("b1/chapters/ch00/chapter_data.json")
        assert stored is not None, "chapter_data must be persisted to GCS store"
        pages = stored["pages"]
        assert pages[0]["text"] == "updated"

    @_SKIP_ON_FIRESTORE
    def test_dual_write_key_matches_local_path(self, wired):
        """GCS key must be '<book_id>/chapters/chXX/chapter_data.json'."""
        bucket, _ = wired
        _helpers.update_chapter_data_page("b1", 0, 1, image_path="/y/page_001.jpg")
        raw = bucket._s.get("b1/chapters/ch00/chapter_data.json")
        assert raw is not None
        data = json.loads(raw)
        assert data["pages"][0]["image_path"] == "/y/page_001.jpg"

    def test_dual_write_contains_all_pages(self, wired):
        """All pages (not just the updated one) must be in the stored JSON."""
        ch_dir = wired[1] / "b1" / "chapters" / "ch00"
        (ch_dir / "chapter_data.json").write_text(json.dumps({
            "chapter_idx": 0,
            "pages": [
                {"page_number": 1, "image_path": "/x/p1.png", "text": "p1"},
                {"page_number": 2, "image_path": "/x/p2.png", "text": "p2"},
            ],
        }))
        _helpers.update_chapter_data_page("b1", 0, 1, text="edited p1")
        stored = _store.get_json("b1/chapters/ch00/chapter_data.json")
        assert len(stored["pages"]) == 2
        assert stored["pages"][1]["text"] == "p2", "other pages preserved"

    @_SKIP_ON_FIRESTORE
    def test_dual_write_silent_on_store_failure(self, monkeypatch, tmp_path):
        """A GCS failure must NOT raise — it only logs a warning."""
        monkeypatch.setattr(_store, "_bucket",
                            lambda: (_ for _ in ()).throw(RuntimeError("GCS down")))
        monkeypatch.setattr(_helpers, "GENERATED_DIR", tmp_path)
        ch = tmp_path / "b2" / "chapters" / "ch01"
        ch.mkdir(parents=True)
        (ch / "chapter_data.json").write_text(json.dumps({
            "chapter_idx": 1, "pages": [{"page_number": 1, "image_path": "", "text": "x"}],
        }))
        # Must not raise
        _helpers.update_chapter_data_page("b2", 1, 1, text="y")
        # Local file still updated correctly
        local = json.loads((ch / "chapter_data.json").read_text())
        assert local["pages"][0]["text"] == "y"


# ---------------------------------------------------------------------------
# Step 3: GCS-first PDF chapter enumeration
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _cfg.STORE_BACKEND == "firestore",
    reason=(
        "GCS-specific: seeds chapter_data into bucket._s and uses a list_keys "
        "lambda that reads from the same dict.  On Firestore backend "
        "store.get_json reads from the Firestore fake, making the seeded data "
        "invisible."
    ),
)
class TestPdfGcsFirstEnumeration:
    """download_book_pdf must read chapter_data from the GCS store on a cold
    serverless instance (no local /tmp files)."""

    @pytest.fixture()
    def cold_instance(self, monkeypatch, tmp_path):
        """A cold instance: no local chapter files, but chapter_data in GCS."""
        bucket = _FakeBucket()
        monkeypatch.setattr(_store, "_bucket", lambda: bucket)
        # storage (image layer) in local fallback mode, empty tmp
        monkeypatch.setattr(_storage, "GCS_BUCKET", "fake-bucket", raising=False)
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "Cold Book"})
        return bucket, tmp_path

    def _seed_chapter_data(self, bucket, book_id, ch_idx, pages):
        key = f"{book_id}/chapters/ch{ch_idx:02d}/chapter_data.json"
        bucket._s[key] = json.dumps({"chapter_idx": ch_idx, "pages": pages})

    def _make_list_keys(self, bucket):
        """Return a list_keys function backed by the fake bucket."""
        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]
        return _list_keys

    def test_pdf_reads_chapter_data_from_gcs_on_cold_instance(
        self, cold_instance, monkeypatch, client
    ):
        """No local files; chapter_data in GCS → PDF must be built (200)."""
        bucket, tmp_path = cold_instance
        self._seed_chapter_data(bucket, "coldbook", 0, [
            {"page_number": 1, "image_path": "", "text": "cold page 1"},
        ])

        monkeypatch.setattr(_storage, "list_keys", self._make_list_keys(bucket))
        # localize: no image to download; just return None (export_pdf handles blank)
        monkeypatch.setattr(_storage, "localize", lambda key: None)

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/coldbook/pdf")
        assert resp.status_code == 200, resp.text
        assert captured.get("pages"), "export_pdf must be called with pages from GCS"
        assert captured["pages"][0]["text"] == "cold page 1"

    def test_pdf_chapters_sorted_in_chapter_order(
        self, cold_instance, monkeypatch, client
    ):
        """Multiple chapters from GCS must be assembled in chapter_idx order."""
        bucket, _ = cold_instance
        # seed out-of-order
        self._seed_chapter_data(bucket, "sortbook", 1, [
            {"page_number": 1, "image_path": "", "text": "ch1 p1"},
        ])
        self._seed_chapter_data(bucket, "sortbook", 0, [
            {"page_number": 1, "image_path": "", "text": "ch0 p1"},
        ])

        monkeypatch.setattr(_storage, "list_keys", self._make_list_keys(bucket))
        monkeypatch.setattr(_storage, "localize", lambda key: None)

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/sortbook/pdf")
        assert resp.status_code == 200
        assert [p["_chapter_num"] for p in captured["pages"]] == [1, 2]

    def test_pdf_404_when_gcs_empty_and_no_local(self, cold_instance, monkeypatch, client):
        """No GCS chapter_data AND no local files → 404."""
        bucket, _ = cold_instance
        monkeypatch.setattr(_storage, "list_keys", self._make_list_keys(bucket))
        resp = client.get("/api/book/emptybook/pdf")
        assert resp.status_code == 404

    def test_pdf_local_fallback_when_gcs_has_no_chapter_data(
        self, monkeypatch, tmp_path, client
    ):
        """GCS has no chapter_data keys (e.g. dev mode) → fall back to local glob."""
        bucket = _FakeBucket()
        monkeypatch.setattr(_store, "_bucket", lambda: bucket)
        monkeypatch.setattr(_storage, "GCS_BUCKET", "", raising=False)
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "Local Book"})
        monkeypatch.setattr(_storage, "localize", lambda key: None)

        # Only local chapter_data — nothing in GCS
        ch_dir = tmp_path / "localbook" / "chapters" / "ch00"
        ch_dir.mkdir(parents=True)
        (ch_dir / "chapter_data.json").write_text(json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "local page"}],
        }))

        captured = {}

        def _fake_export(pages, title, out_path, special_dir=""):
            captured["pages"] = pages
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/localbook/pdf")
        assert resp.status_code == 200
        assert captured["pages"][0]["text"] == "local page"


# ---------------------------------------------------------------------------
# Step 4: localize page images before export_pdf
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _cfg.STORE_BACKEND == "firestore",
    reason=(
        "GCS-specific: seeds chapter_data into bucket._s and uses a list_keys "
        "lambda that reads from the same dict.  On Firestore backend "
        "store.get_json reads from the Firestore fake, making the seeded data "
        "invisible."
    ),
)
class TestLocalizePageImagesBeforePdf:
    """Page images referenced in chapter_data must be localized from GCS
    before export_pdf so they exist as local files for reportlab."""

    @pytest.fixture()
    def setup(self, monkeypatch, tmp_path):
        bucket = _FakeBucket()
        monkeypatch.setattr(_store, "_bucket", lambda: bucket)
        monkeypatch.setattr(_storage, "GCS_BUCKET", "fake-bucket", raising=False)
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "T"})
        return bucket, tmp_path

    def test_localize_called_for_each_page_image(self, setup, monkeypatch, client):
        bucket, tmp_path = setup
        image_key = "imgbook/chapters/ch00/pages/page_001.png"
        bucket._s["imgbook/chapters/ch00/chapter_data.json"] = json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1,
                        "image_path": str(tmp_path / image_key),
                        "text": "img page"}],
        })

        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]

        monkeypatch.setattr(_storage, "list_keys", _list_keys)

        localized_keys = []

        def _fake_localize(key):
            localized_keys.append(key)
            return None

        monkeypatch.setattr(_storage, "localize", _fake_localize)

        def _fake_export(pages, title, out_path, special_dir=""):
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/imgbook/pdf")
        assert resp.status_code == 200
        assert image_key in localized_keys, (
            f"localize must be called with the GCS key; got {localized_keys}"
        )

    def test_localize_skipped_for_empty_image_path(self, setup, monkeypatch, client):
        """Pages with no image_path must not trigger localize (no crash)."""
        bucket, tmp_path = setup
        bucket._s["noimg/chapters/ch00/chapter_data.json"] = json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "no image"}],
        })

        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]

        monkeypatch.setattr(_storage, "list_keys", _list_keys)

        localized_keys = []
        monkeypatch.setattr(_storage, "localize", lambda k: localized_keys.append(k) or None)

        def _fake_export(pages, title, out_path, special_dir=""):
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/noimg/pdf")
        assert resp.status_code == 200
        assert localized_keys == [], "empty image_path must not trigger localize"

    def test_localize_failure_does_not_crash_pdf_build(self, setup, monkeypatch, client):
        """A localize failure (GCS miss) must NOT raise — export_pdf renders
        a text-only page when the local file is missing."""
        bucket, tmp_path = setup
        image_key = "failbook/chapters/ch00/pages/page_001.png"
        bucket._s["failbook/chapters/ch00/chapter_data.json"] = json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1,
                        "image_path": str(tmp_path / image_key),
                        "text": "fallback text"}],
        })

        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]

        monkeypatch.setattr(_storage, "list_keys", _list_keys)
        monkeypatch.setattr(_storage, "localize", lambda key: (_ for _ in ()).throw(RuntimeError("GCS miss")))

        def _fake_export(pages, title, out_path, special_dir=""):
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/failbook/pdf")
        assert resp.status_code == 200, "localize failure must not crash the PDF build"


# ---------------------------------------------------------------------------
# Fix 2: localize special/cover images before building the PDF
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    _cfg.STORE_BACKEND == "firestore",
    reason=(
        "GCS-specific: seeds special image keys into bucket._s and uses a "
        "list_keys lambda that reads from the same dict.  On Firestore backend "
        "those keys are invisible to the store layer."
    ),
)
class TestLocalizeSpecialImagesBeforePdf:
    """Special images (book_cover, chapter dividers, back_cover) in GCS must be
    localized before export_pdf so export_pdf's os.path.exists() finds them."""

    @pytest.fixture()
    def setup(self, monkeypatch, tmp_path):
        bucket = _FakeBucket()
        monkeypatch.setattr(_store, "_bucket", lambda: bucket)
        monkeypatch.setattr(_storage, "GCS_BUCKET", "fake-bucket", raising=False)
        monkeypatch.setattr(_storage, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "GENERATED_DIR", tmp_path)
        monkeypatch.setattr(_books, "_load_json", lambda bid, fn: {"title": "CoverBook"})
        return bucket, tmp_path

    def test_localize_called_for_special_image(self, setup, monkeypatch, client):
        """storage.localize must be invoked for a special image key seeded in GCS."""
        bucket, tmp_path = setup

        # Seed one chapter_data page (required for PDF build to proceed)
        bucket._s["coverbook/chapters/ch00/chapter_data.json"] = json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "page"}],
        })
        # Seed a special cover image key in GCS
        special_image_key = "coverbook/special/book_cover.png"
        bucket._s[special_image_key] = b"COVER_BYTES"

        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]

        monkeypatch.setattr(_storage, "list_keys", _list_keys)

        localized_keys = []

        def _fake_localize(key):
            localized_keys.append(key)
            return None

        monkeypatch.setattr(_storage, "localize", _fake_localize)

        def _fake_export(pages, title, out_path, special_dir=""):
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/coverbook/pdf")
        assert resp.status_code == 200
        assert special_image_key in localized_keys, (
            f"localize must be called for special image '{special_image_key}'; got {localized_keys}"
        )

    def test_special_image_localize_failure_does_not_crash_pdf(self, setup, monkeypatch, client):
        """A localize failure for a special image must NOT raise — PDF still builds."""
        bucket, tmp_path = setup

        bucket._s["coverbook2/chapters/ch00/chapter_data.json"] = json.dumps({
            "chapter_idx": 0,
            "pages": [{"page_number": 1, "image_path": "", "text": "page"}],
        })
        bucket._s["coverbook2/special/book_cover.png"] = b"COVER"

        def _list_keys(prefix):
            return [k for k in bucket._s if k.startswith(prefix)]

        monkeypatch.setattr(_storage, "list_keys", _list_keys)
        monkeypatch.setattr(_storage, "localize",
                            lambda key: (_ for _ in ()).throw(RuntimeError("GCS miss")))

        def _fake_export(pages, title, out_path, special_dir=""):
            Path(out_path).write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr("src.renderer.pdf_export.export_pdf", _fake_export)

        resp = client.get("/api/book/coverbook2/pdf")
        assert resp.status_code == 200, "special image localize failure must not crash PDF build"

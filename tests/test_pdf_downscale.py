"""Framework fix: the on-demand book PDF (/api/book/{id}/pdf) timed out — Next's
reverse proxy cut it at 30s because reportlab embedded the raw 2-3MB page images.
Downscale + JPEG-compress in the one image-drawing exit (_draw_full_image_page)
so every page/cover is small enough to build well under the timeout.
"""

from __future__ import annotations

from PIL import Image

from src.renderer.pdf_export import _PDF_MAX_PX, _downscaled_reader, export_pdf


def test_downscales_large_image(tmp_path):
    big = tmp_path / "page_001.png"
    Image.new("RGB", (2400, 2400), "blue").save(big)
    w, h = _downscaled_reader(str(big)).getSize()
    assert max(w, h) <= _PDF_MAX_PX, "large image must be downscaled to the page size"


def test_small_image_not_upscaled(tmp_path):
    small = tmp_path / "page_002.png"
    Image.new("RGB", (600, 600), "red").save(small)
    assert _downscaled_reader(str(small)).getSize() == (600, 600)


def test_flattens_alpha_without_error(tmp_path):
    # RGBA → JPEG has no alpha; must flatten onto white, not crash or go black.
    p = tmp_path / "alpha.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 128)).save(p)
    assert _downscaled_reader(str(p)).getSize() == (100, 100)


def test_export_pdf_builds_from_large_images(tmp_path):
    pages = []
    for i in range(1, 4):
        img = tmp_path / f"page_{i:03d}.png"
        Image.new("RGB", (2000, 2000), "green").save(img)
        pages.append({"image_path": str(img), "text": f"page {i}", "page_number": i})
    out = tmp_path / "book.pdf"
    export_pdf(pages, "Test Book", str(out), special_dir=str(tmp_path / "special"))
    assert out.exists() and out.stat().st_size > 0

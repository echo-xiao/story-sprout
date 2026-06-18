"""PDF export for picture books using reportlab."""

import logging
import os
from pathlib import Path

from reportlab.lib.pagesizes import inch
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# 8.5" x 8.5" square picture book
PAGE_SIZE = (8.5 * inch, 8.5 * inch)


def _register_fonts() -> str:
    try:
        pdfmetrics.registerFont(TTFont("ComicSans", "Comic Sans MS.ttf"))
        return "ComicSans"
    except Exception:
        pass
    return "Helvetica"


# The PDF page is 8.5" square; at ~130 DPI that's ~1100px — past that the extra
# pixels don't show. Embedding the raw 2-3MB 1024²+ PNGs made reportlab take
# >30s for a 39-page book, and Next's reverse proxy cut the request off at 30s
# (→ 500, never a usable PDF). Downscale + JPEG-compress to the display size in
# the ONE image-drawing exit so every page/cover benefits: ~10x faster build,
# ~15x smaller file.
_PDF_MAX_PX = 1100


def _downscaled_reader(image_path: str):
    """A reportlab ImageReader over a downscaled, white-flattened JPEG of the
    source image. Keeps the on-demand PDF build well under the proxy timeout and
    the file small, with no visible quality loss at the page's print size."""
    from io import BytesIO

    from PIL import Image
    from reportlab.lib.utils import ImageReader

    im = Image.open(image_path)
    # JPEG has no alpha channel — flatten any transparency onto white so PNGs
    # with alpha don't come out black.
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if max(im.size) > _PDF_MAX_PX:
        im.thumbnail((_PDF_MAX_PX, _PDF_MAX_PX), Image.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=85, optimize=True)
    buf.seek(0)
    return ImageReader(buf)


def _draw_full_image_page(c: canvas.Canvas, image_path: str, width: float, height: float) -> bool:
    """Draw a full-page image (downscaled for speed). Returns True if successful."""
    if not image_path or not os.path.exists(image_path):
        return False
    try:
        c.drawImage(
            _downscaled_reader(image_path), 0, 0,
            width=width, height=height,
            preserveAspectRatio=True, anchor="c",
        )
        c.showPage()
        return True
    except Exception as e:
        logger.warning("Could not draw image %s: %s", image_path, e)
        return False


def _find_image(directory: str | Path, name: str) -> str:
    """Find an image file by name (trying .png and .jpg)."""
    d = Path(directory)
    for ext in (".png", ".jpg"):
        candidate = d / f"{name}{ext}"
        if candidate.exists():
            return str(candidate)
    return ""


def export_pdf(
    pages: list[dict],
    book_title: str,
    output_path: str,
    special_dir: str = "",
) -> str:
    """Export the picture book as a PDF.

    Pages with images use the image as full-page (text is already embedded
    by Gemini in the illustration). No additional text overlay. Chapter
    cover insertion is driven entirely by each page's `_chapter_num`
    tag (set by build_combined_pdf).

    Args:
        pages: List of page dicts with 'image_path' and 'text'.
        book_title: Title for the PDF.
        output_path: Where to save the PDF.
        special_dir: Directory containing special page images.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not special_dir:
        special_dir = str(output.parent / "special")

    width, height = PAGE_SIZE
    font = _register_fonts()

    c = canvas.Canvas(str(output), pagesize=PAGE_SIZE)
    c.setTitle(book_title)
    c.setAuthor("Picture Book Generator")

    special = Path(special_dir)

    # 1. Book cover
    book_cover = _find_image(special, "book_cover")
    if book_cover:
        _draw_full_image_page(c, book_cover, width, height)

    # 2. Content pages — each page group is tagged with _chapter_num if from combined PDF
    # Pages should have _chapter_num set by build_combined_pdf
    current_chapter = None
    for page in pages:
        ch_num = page.get("_chapter_num")

        # Insert chapter cover when chapter changes (per-chapter ending pages
        # were cut by design: one front cover, chapter covers, one back cover)
        if ch_num is not None and ch_num != current_chapter:
            current_chapter = ch_num
            ch_cover = _find_image(special, f"chapter_{ch_num:02d}_cover")
            if ch_cover:
                _draw_full_image_page(c, ch_cover, width, height)

        # Render the page
        image_path = page.get("image_path", "")
        if image_path and os.path.exists(image_path):
            _draw_full_image_page(c, image_path, width, height)
        else:
            if image_path:
                # A recorded path that no longer exists means chapter_data has
                # gone stale (e.g. a regen changed the extension) — say so
                # instead of silently emitting a text-only page.
                logger.warning("PDF page %s: image missing at %s — rendering text-only",
                               page.get("page_number", "?"), image_path)
            text = page.get("text", "")
            if text:
                bg = HexColor("#fffaf4")
                c.setFillColor(bg)
                c.rect(0, 0, width, height, fill=1, stroke=0)
                c.setFillColor(HexColor("#3d3229"))
                c.setFont(font, 18)
                import textwrap
                max_chars = int((width - 1.2 * inch) / (18 * 0.5))
                lines = textwrap.wrap(text, width=max_chars)
                y = height * 0.7
                for line in lines:
                    if y < inch:
                        break
                    c.drawString(0.6 * inch, y, line)
                    y -= 18 * 1.6
                c.showPage()

    # 3. Back cover
    back_cover = _find_image(special, "back_cover")
    if back_cover:
        _draw_full_image_page(c, back_cover, width, height)

    c.save()
    abs_path = str(output.resolve())
    logger.info("PDF exported to %s", abs_path)
    return abs_path

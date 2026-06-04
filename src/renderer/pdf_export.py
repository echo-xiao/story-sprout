"""PDF export for picture books using reportlab."""

import logging
import os
import textwrap
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import inch
from reportlab.lib.colors import Color, HexColor, white, black
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

# 8.5" x 8.5" square picture book
PAGE_SIZE = (8.5 * inch, 8.5 * inch)
MARGIN = 0.6 * inch

# Color palette (matches the HTML viewer)
_ACCENT = HexColor("#e07a5f")
_ACCENT_DARK = HexColor("#c1574a")
_BLUE = HexColor("#81b2d2")
_BG_WARM = HexColor("#fffaf4")
_TEXT_COLOR = HexColor("#3d3229")

# Pastel page backgrounds
_PAGE_COLORS: list[Color] = [
    HexColor("#ffecd2"),
    HexColor("#c2e9fb"),
    HexColor("#d4fc79"),
    HexColor("#fbc2eb"),
    HexColor("#ffeaa7"),
    HexColor("#e0c3fc"),
    HexColor("#f6d365"),
]


def _register_fonts() -> str:
    """Register a readable font. Returns the font family name to use."""
    try:
        pdfmetrics.registerFont(TTFont("ComicSans", "Comic Sans MS.ttf"))
        return "ComicSans"
    except Exception:
        pass
    return "Helvetica"


def _draw_cover(
    c: canvas.Canvas, title: str, width: float, height: float, font: str,
    cover_image: str = "",
) -> None:
    """Draw the front cover page with optional background image."""
    if cover_image and os.path.exists(cover_image):
        # Draw cover image as background
        try:
            c.drawImage(
                cover_image,
                0, 0,
                width=width,
                height=height,
                preserveAspectRatio=True,
                anchor="c",
            )
            # Semi-transparent overlay for title readability
            c.setFillColor(Color(0, 0, 0, alpha=0.4))
            c.rect(0, height * 0.3, width, height * 0.4, fill=1, stroke=0)
        except Exception as e:
            logger.warning("Cover image failed: %s", e)
            c.setFillColor(_ACCENT)
            c.rect(0, 0, width, height, fill=1, stroke=0)
    else:
        c.setFillColor(_ACCENT)
        c.rect(0, 0, width, height, fill=1, stroke=0)

    # Title
    c.setFillColor(white)
    c.setFont(font, 36)
    lines = textwrap.wrap(title, width=22)
    y_start = height / 2 + (len(lines) - 1) * 22
    for i, line in enumerate(lines):
        text_width = c.stringWidth(line, font, 36)
        c.drawString((width - text_width) / 2, y_start - i * 44, line)

    # Subtitle
    c.setFont(font, 16)
    sub = "A Picture Book"
    sw = c.stringWidth(sub, font, 16)
    c.drawString((width - sw) / 2, y_start - len(lines) * 44 - 20, sub)

    c.showPage()


def _draw_title_page(
    c: canvas.Canvas, title: str, width: float, height: float, font: str,
) -> None:
    """Draw a title/half-title page (page 2, before content starts)."""
    c.setFillColor(_BG_WARM)
    c.rect(0, 0, width, height, fill=1, stroke=0)

    # Decorative line
    c.setStrokeColor(_ACCENT)
    c.setLineWidth(2)
    line_y = height * 0.55
    c.line(width * 0.2, line_y, width * 0.8, line_y)

    # Title
    c.setFillColor(_TEXT_COLOR)
    c.setFont(font, 28)
    lines = textwrap.wrap(title, width=26)
    y_start = height * 0.55 + 40
    for i, line in enumerate(lines):
        text_width = c.stringWidth(line, font, 28)
        c.drawString((width - text_width) / 2, y_start + (len(lines) - 1 - i) * 36, line)

    # Credit
    c.setFont(font, 12)
    credit = "Illustrated by AI"
    cw = c.stringWidth(credit, font, 12)
    c.drawString((width - cw) / 2, height * 0.55 - 40, credit)

    c.showPage()


def _draw_chapter_title(
    c: canvas.Canvas, chapter_title: str, chapter_num: int,
    width: float, height: float, font: str,
) -> None:
    """Draw a chapter title divider page."""
    bg = _PAGE_COLORS[(chapter_num - 1) % len(_PAGE_COLORS)]
    c.setFillColor(bg)
    c.rect(0, 0, width, height, fill=1, stroke=0)

    # Chapter number
    c.setFillColor(_TEXT_COLOR)
    c.setFont(font, 14)
    ch_text = f"Chapter {chapter_num}"
    tw = c.stringWidth(ch_text, font, 14)
    c.drawString((width - tw) / 2, height * 0.6, ch_text)

    # Chapter title
    c.setFont(font, 24)
    lines = textwrap.wrap(chapter_title, width=28)
    y_start = height * 0.5
    for i, line in enumerate(lines):
        text_width = c.stringWidth(line, font, 24)
        c.drawString((width - text_width) / 2, y_start - i * 32, line)

    c.showPage()


def _draw_back_cover(c: canvas.Canvas, width: float, height: float, font: str) -> None:
    """Draw the back cover page."""
    c.setFillColor(_BLUE)
    c.rect(0, 0, width, height, fill=1, stroke=0)

    c.setFillColor(white)
    c.setFont(font, 32)
    text = "The End"
    tw = c.stringWidth(text, font, 32)
    c.drawString((width - tw) / 2, height / 2 + 20, text)

    c.setFont(font, 14)
    sub = "Thank you for reading!"
    sw = c.stringWidth(sub, font, 14)
    c.drawString((width - sw) / 2, height / 2 - 20, sub)

    c.showPage()


def _draw_content_page(
    c: canvas.Canvas,
    page: dict,
    page_num: int,
    total: int,
    width: float,
    height: float,
    font: str,
) -> None:
    """Draw a single content page with image filling entire page."""
    text = page.get("text", "")
    image_path = page.get("image_path", "")

    # Background color
    bg = _PAGE_COLORS[(page_num - 1) % len(_PAGE_COLORS)]

    has_image = bool(image_path and os.path.exists(image_path))

    if has_image:
        # Image fills ENTIRE page
        try:
            c.drawImage(
                image_path,
                0, 0,
                width=width,
                height=height,
                preserveAspectRatio=True,
                anchor="c",
            )
        except Exception as e:
            logger.warning("Could not draw image %s: %s", image_path, e)
            c.setFillColor(bg)
            c.rect(0, 0, width, height, fill=1, stroke=0)
    else:
        c.setFillColor(bg)
        c.rect(0, 0, width, height, fill=1, stroke=0)

    # Only render text if no image (fallback)
    if not has_image and text:
        c.setFillColor(_BG_WARM)
        c.rect(0, 0, width, height * 0.40, fill=1, stroke=0)
        c.setFillColor(_TEXT_COLOR)
        font_size = 18
        c.setFont(font, font_size)
        max_chars = int((width - 2 * MARGIN) / (font_size * 0.5))
        lines = textwrap.wrap(text, width=max_chars)
        line_height = font_size * 1.6
        text_block_height = len(lines) * line_height
        y_start = (height * 0.38 + text_block_height) / 2
        for i, line in enumerate(lines):
            y = y_start - i * line_height
            if y < MARGIN:
                break
            c.drawString(MARGIN, y, line)

    c.showPage()


def export_pdf(
    pages: list[dict],
    book_title: str,
    output_path: str,
    cover_image: str = "",
) -> str:
    """Export the picture book as a PDF.

    Args:
        pages: List of page dicts (``text``, ``image_path``, optionally ``template_beat``).
        book_title: Title for the cover.
        output_path: Destination file path for the PDF.
        cover_image: Optional path to an image for the cover background.

    Returns:
        The absolute path to the written PDF file.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    width, height = PAGE_SIZE
    font = _register_fonts()

    c = canvas.Canvas(str(output), pagesize=PAGE_SIZE)
    c.setTitle(book_title)
    c.setAuthor("Picture Book Generator")

    # Front cover (with optional image)
    _draw_cover(c, book_title, width, height, font, cover_image=cover_image)

    # Title page
    _draw_title_page(c, book_title, width, height, font)

    # Content pages
    total = len(pages)
    for idx, page in enumerate(pages):
        _draw_content_page(c, page, idx + 1, total, width, height, font)

    # Back cover ("The End")
    _draw_back_cover(c, width, height, font)

    c.save()
    abs_path = str(output.resolve())
    logger.info("PDF exported to %s", abs_path)
    return abs_path

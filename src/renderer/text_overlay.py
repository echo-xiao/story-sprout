"""Embed text into illustration images with style-matched hand-drawn fonts.

Text placement:
- Scans the image to find the lightest/emptiest region
- Places text there with a subtle background fade
- Uses hand-drawn style fonts to match illustration aesthetic
"""

import logging
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# Hand-drawn style fonts (prioritized)
_FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/ChalkboardSE.ttc",
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
    "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    # CJK fallbacks
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

# Try to find a CJK-capable hand-drawn font
_CJK_FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def _get_font(size: int, text: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font that supports the text content."""
    # Check if text has CJK characters
    has_cjk = any('\u4e00' <= c <= '\u9fff' for c in text)

    paths = _CJK_FONT_PATHS + _FONT_PATHS if has_cjk else _FONT_PATHS
    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _text_bbox(text: str, font) -> tuple[int, int]:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        return int(len(text) * 12), 20


def _find_best_text_region(img: Image.Image, text_h_ratio: float = 0.25) -> str:
    """Find the lightest/emptiest region of the image for text placement.

    Returns: 'top', 'bottom', or 'middle'
    """
    w, h = img.size
    arr = np.array(img.convert("L"))  # grayscale

    strip_h = int(h * text_h_ratio)

    # Calculate average brightness for top, middle, bottom strips
    top_brightness = arr[:strip_h, :].mean()
    mid_start = (h - strip_h) // 2
    mid_brightness = arr[mid_start:mid_start + strip_h, :].mean()
    bottom_brightness = arr[h - strip_h:, :].mean()

    # Also calculate variance (low variance = more uniform = better for text)
    top_var = arr[:strip_h, :].var()
    mid_var = arr[mid_start:mid_start + strip_h, :].var()
    bottom_var = arr[h - strip_h:, :].var()

    # Score: higher brightness + lower variance = better for text
    top_score = top_brightness - top_var * 0.01
    mid_score = mid_brightness - mid_var * 0.01
    bottom_score = bottom_brightness - bottom_var * 0.01

    scores = {"top": top_score, "bottom": bottom_score, "middle": mid_score}
    best = max(scores, key=scores.get)

    logger.debug("Text region scores: top=%.0f mid=%.0f bottom=%.0f -> %s",
                top_score, mid_score, bottom_score, best)
    return best


def overlay_text_on_image(
    image_path: str,
    text: str,
    output_path: str | None = None,
    font_size: int = 24,
    text_area_ratio: float = 0.25,
    auto_position: bool = True,
) -> str:
    """Embed text into illustration with hand-drawn font, auto-positioned."""
    if not output_path:
        output_path = image_path

    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as e:
        logger.error("Cannot open image %s: %s", image_path, e)
        return image_path

    w, h = img.size
    text_h = int(h * text_area_ratio)

    # Find best position
    if auto_position:
        position = _find_best_text_region(img, text_area_ratio)
    else:
        position = "bottom"

    if position == "top":
        text_y = 0
    elif position == "middle":
        text_y = (h - text_h) // 2
    else:
        text_y = h - text_h

    # Create gradient overlay
    overlay = Image.new("RGBA", (w, text_h), (0, 0, 0, 0))

    # Sample the image color in the text region for a matching tint
    region = img.crop((0, text_y, w, text_y + text_h))
    region_arr = np.array(region.convert("RGB"))
    avg_color = region_arr.mean(axis=(0, 1)).astype(int)
    # Make it lighter for readability
    bg_r = min(255, avg_color[0] + 80)
    bg_g = min(255, avg_color[1] + 80)
    bg_b = min(255, avg_color[2] + 80)

    for y in range(text_h):
        if position == "top":
            # Fade: solid at top → transparent at bottom
            progress = 1.0 - (y / text_h)
        elif position == "bottom":
            # Fade: transparent at top → solid at bottom
            progress = y / text_h
        else:
            # Fade from edges to center (solid in middle)
            progress = 1.0 - abs(2.0 * y / text_h - 1.0)

        alpha = int(200 * (progress ** 1.3))
        for x in range(w):
            overlay.putpixel((x, y), (bg_r, bg_g, bg_b, alpha))

    img.paste(overlay, (0, text_y), overlay)

    # Render text with hand-drawn font
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size, text)

    padding = 25
    max_chars = max(10, int((w - padding * 2) / (_text_bbox("M", font)[0] or 12)))
    lines = textwrap.wrap(text, width=max_chars)

    line_h = _text_bbox("Ag", font)[1] + 8

    # Adjust font size if needed
    max_lines = int((text_h * 0.75) / line_h)
    while len(lines) > max_lines and font_size > 14:
        font_size -= 2
        font = _get_font(font_size, text)
        max_chars = max(10, int((w - padding * 2) / (_text_bbox("M", font)[0] or 12)))
        lines = textwrap.wrap(text, width=max_chars)
        line_h = _text_bbox("Ag", font)[1] + 8
        max_lines = int((text_h * 0.75) / line_h)

    lines = lines[:max_lines]
    total_text_h = len(lines) * line_h

    if position == "top":
        y_start = text_y + padding
    elif position == "bottom":
        y_start = text_y + (text_h - total_text_h) // 2 + int(text_h * 0.1)
    else:
        y_start = text_y + (text_h - total_text_h) // 2

    # Text color — dark brown for warm feel
    text_color = (55, 40, 30)

    for i, line in enumerate(lines):
        lw = _text_bbox(line, font)[0]
        x = (w - lw) // 2
        y = y_start + i * line_h
        # Subtle shadow for readability
        draw.text((x + 1, y + 1), line, fill=(255, 255, 250, 100), font=font)
        draw.text((x, y), line, fill=text_color, font=font)

    final = img.convert("RGB")
    final.save(output_path, quality=95)
    return output_path


def create_composite_pages(
    pages: list[dict],
    output_dir: str | Path,
) -> list[dict]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(pages):
        image_path = page.get("image_path", "")
        text = page.get("text", "")

        if not image_path or not Path(image_path).exists():
            continue

        composite_path = str(output_dir / f"composite_{i + 1:03d}.png")
        overlay_text_on_image(image_path, text, composite_path)
        page["composite_path"] = composite_path

    return pages

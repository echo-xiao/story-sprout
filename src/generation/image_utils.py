"""Shared image utilities for Gemini-based generation modules."""

import base64
import logging
from pathlib import Path

from google import genai

from src.gemini_backend import make_genai_client

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    # BYOK: when a per-request user key is active, build a fresh client bound to
    # that key rather than reusing the cached project client.
    from src.gemini_backend import get_user_api_key
    if get_user_api_key():
        return make_genai_client()
    if _client is None:
        _client = make_genai_client()
    return _client


def save_inline_image(response: object, save_path: Path) -> str:
    """Save the first inline image from a Gemini response.

    The extension is chosen from the returned mime type, replacing
    `save_path`'s suffix. Returns the final path as a string, or "" when the
    response carried no image.
    """
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return ""
    for part in candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            mime = part.inline_data.mime_type or "image/png"
            ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
            final = save_path.with_suffix(ext)
            final.write_bytes(part.inline_data.data)
            return str(final)
    return ""


def _load_image_part(image_path: str) -> dict | None:
    """Load an image file and return a Gemini-compatible Part dict."""
    path = Path(image_path)
    if not path.exists():
        return None
    try:
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        return {
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(data).decode("utf-8"),
            }
        }
    except Exception as e:
        logger.warning("Failed to load reference image %s: %s", image_path, e)
        return None

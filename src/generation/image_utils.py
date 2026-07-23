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


def _diagnose_empty_image(response: object, save_path: Path) -> str:
    """Best-effort one-line reason a Gemini response carried no image, so a
    silent no-image 200 (the model returned text or was blocked) leaves a trace
    instead of vanishing. Never raises."""
    try:
        bits: list[str] = []
        pf = getattr(response, "prompt_feedback", None)
        if pf is not None and getattr(pf, "block_reason", None):
            bits.append(f"prompt_block={pf.block_reason}")
        cands = getattr(response, "candidates", None) or []
        if not cands:
            bits.append("no_candidates")
        else:
            c0 = cands[0]
            fr = getattr(c0, "finish_reason", None)
            if fr:
                bits.append(f"finish_reason={fr}")
            content = getattr(c0, "content", None)
            parts = getattr(content, "parts", None) or [] if content else []
            kinds = []
            for p in parts:
                if getattr(p, "inline_data", None) is not None:
                    kinds.append("image")
                elif getattr(p, "text", None):
                    kinds.append("text")
                else:
                    kinds.append("other")
            bits.append(f"parts={kinds or 'none'}")
            txt = next((getattr(p, "text", "") for p in parts if getattr(p, "text", None)), "")
            if txt:
                bits.append(f"text[:120]={txt[:120]!r}")
        return ", ".join(bits) or "unknown"
    except Exception as e:  # diagnostics must never mask the real failure
        return f"diagnose-failed: {e}"


def save_inline_image(response: object, save_path: Path) -> str:
    """Save the first inline image from a Gemini response.

    The extension is chosen from the returned mime type, replacing
    `save_path`'s suffix. Returns the final path as a string, or "" when the
    response carried no image.
    """
    candidates = getattr(response, "candidates", None)
    if not candidates:
        logger.warning("save_inline_image: no image for %s — %s",
                       save_path.name, _diagnose_empty_image(response, save_path))
        return ""
    for part in candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            mime = part.inline_data.mime_type or "image/png"
            ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
            final = save_path.with_suffix(ext)
            final.write_bytes(part.inline_data.data)
            # One stem = one current file. If a previous render saved the OTHER
            # extension (.png<->.jpg), drop it here — checkpoint/stale/PDF probes
            # loop over both extensions, so a leftover twin can win the probe and
            # be served as the "current" image (a page reported drawn/up-to-date
            # while showing the stale art). Cleaned in the one image-saving exit
            # so EVERY path benefits, and BEFORE the mirror so it also covers the
            # local / no-GCS case (mirror_to_gcs early-returns without GCS and so
            # never cleaned the local twin).
            other = save_path.with_suffix(".jpg" if ext == ".png" else ".png")
            if other != final:
                try:
                    other.unlink(missing_ok=True)
                except OSError:
                    pass
            # Mirror to durable storage (GCS) so chapter-generated pages/sheets/
            # covers survive a Cloud Run redeploy instead of being local-only.
            # mirror_to_gcs also drops the other-extension OBJECT in GCS.
            try:
                from src.core import storage
                storage.mirror_to_gcs(final)
            except Exception:
                pass
            return str(final)
    logger.warning("save_inline_image: no image for %s — %s",
                   save_path.name, _diagnose_empty_image(response, save_path))
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

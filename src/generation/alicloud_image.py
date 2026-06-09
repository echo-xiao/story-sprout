"""Alibaba Cloud DashScope image generation using wan2.7-image-pro.

Supports text-to-image with reference images (up to 4).
Used as a cheaper alternative to Gemini for development/debugging.
"""

import base64
import logging
import time
from pathlib import Path

from dashscope.aigc.image_generation import ImageGeneration
from dashscope.api_entities.dashscope_response import Message

from src.config import ALICLOUD_API_KEY, ALICLOUD_IMAGE_MODEL

logger = logging.getLogger(__name__)


def _build_content(prompt: str, reference_images: list[str] | None = None) -> list[dict]:
    """Build message content with optional reference images."""
    content = []

    # Add reference images (up to 4)
    if reference_images:
        for img_path in reference_images[:4]:
            path = Path(img_path)
            if not path.exists():
                continue
            data = base64.b64encode(path.read_bytes()).decode("utf-8")
            suffix = path.suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/jpeg"
            content.append({
                "image": f"data:{mime};base64,{data}",
                "type": "image",
            })

    content.append({"text": prompt, "type": "text"})
    return content


def generate_image_alicloud(
    prompt: str,
    save_path: Path,
    reference_images: list[str] | None = None,
    size: str = "1024*1024",
    max_retries: int = 2,
) -> str:
    """Generate a single image using DashScope. Returns saved path or empty string."""
    save_path.parent.mkdir(parents=True, exist_ok=True)

    content = _build_content(prompt, reference_images)
    message = Message(role="user", content=content)

    for attempt in range(max_retries):
        try:
            rsp = ImageGeneration.call(
                model=ALICLOUD_IMAGE_MODEL,
                api_key=ALICLOUD_API_KEY,
                messages=[message],
                n=1,
                size=size,
            )

            if rsp.status_code != 200:
                logger.warning("DashScope error (attempt %d): %s", attempt + 1, rsp.message)
                if attempt < max_retries - 1:
                    time.sleep(2)
                continue

            # Extract image URL from response
            choices = rsp.output.get("choices", [])
            if not choices:
                logger.warning("DashScope returned no choices")
                continue

            image_content = choices[0].get("message", {}).get("content", [])
            for item in image_content:
                if isinstance(item, dict) and item.get("type") == "image" and item.get("image"):
                    image_url = item["image"]

                    # Download image
                    import httpx
                    resp = httpx.get(image_url, timeout=60)
                    resp.raise_for_status()

                    ext = ".png" if "png" in resp.headers.get("content-type", "") else ".jpg"
                    final_path = save_path.with_suffix(ext)
                    final_path.write_bytes(resp.content)
                    logger.info("Saved DashScope image to %s", final_path)
                    return str(final_path)

        except Exception as e:
            logger.warning("DashScope attempt %d failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2)

    return ""

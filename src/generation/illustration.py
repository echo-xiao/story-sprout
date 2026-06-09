"""Generate page illustrations using Gemini image generation with reference images.

Uses character sheet images and optional style reference images as visual
references for consistency — not just text prompts.
"""

import logging
import random
import time
from pathlib import Path

from google import genai

from src.config import (
    DEFAULT_STYLE,
    GEMINI_IMAGE_MODEL,
    GENERATED_DIR,
    NEGATIVE_PROMPT,
)
from src.generation.image_utils import _get_client, _load_image_part

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def _build_reference_content(
    prompt_text: str,
    character_sheets: list[dict],
    style_ref_path: str | None = None,
    in_scene_names: list[str] | None = None,
    scene_sheet_path: str | None = None,
) -> list[dict]:
    """Build multi-part content: reference images FIRST, then prompt.

    Character sheets go first so Gemini treats them as primary visual anchors.
    Only includes sheets for characters actually in the scene.
    """
    parts = []

    # Style reference first (sets overall look)
    if style_ref_path:
        img_part = _load_image_part(style_ref_path)
        if img_part:
            parts.append({"text": "[STYLE REFERENCE — match this art style]"})
            parts.append(img_part)

    # Character sheets — prioritize in-scene characters, then add others for style consistency
    sheet_images_added = 0
    matched_sheets = []
    unmatched_sheets = []

    for sheet in character_sheets:
        char_name = sheet.get("character_name", "character")
        sheet_path = sheet.get("sheet_path", "")
        if not sheet_path:
            continue
        if in_scene_names:
            name_lower = char_name.lower()
            name_parts = name_lower.split()
            first_name = name_parts[0]
            last_name = name_parts[-1] if len(name_parts) > 1 else ""
            is_match = any(
                name_lower == n.lower()
                or first_name == n.lower().split()[0]
                or (last_name and last_name == n.lower().split()[-1])
                or any(p in n.lower() for p in name_parts if len(p) > 3)
                or any(p in name_lower for p in n.lower().split() if len(p) > 3)
                for n in in_scene_names
            )
            if is_match:
                matched_sheets.append(sheet)
            else:
                unmatched_sheets.append(sheet)
        else:
            matched_sheets.append(sheet)

    # Add matched sheets first (in-scene characters)
    for sheet in matched_sheets:
        char_name = sheet.get("character_name", "character")
        img_part = _load_image_part(sheet["sheet_path"])
        if img_part:
            parts.append({"text": f"[CHARACTER SHEET: {char_name}] — COPY this character's hair, face, outfit, and colors EXACTLY as shown. Do NOT change any detail."})
            parts.append(img_part)
            sheet_images_added += 1
            if sheet_images_added >= 5:
                break

    # If fewer than 2 matched, add unmatched sheets as style references to keep consistency
    if sheet_images_added < 2:
        for sheet in unmatched_sheets:
            if sheet_images_added >= 3:
                break
            char_name = sheet.get("character_name", "character")
            img_part = _load_image_part(sheet["sheet_path"])
            if img_part:
                parts.append({"text": f"[STYLE REFERENCE from {char_name}] — Match this art style, colors, and line quality."})
                parts.append(img_part)
                sheet_images_added += 1

    # Scene background reference (if available)
    if scene_sheet_path:
        img_part = _load_image_part(scene_sheet_path)
        if img_part:
            parts.append({"text": "[SCENE BACKGROUND REFERENCE — Use this image as the background/setting for this scene. Match the architecture, lighting, colors, and atmosphere EXACTLY.]"})
            parts.append(img_part)

    # Prompt text last
    parts.append({"text": prompt_text})

    return parts


def _build_page_prompt(page: dict, character_sheets: list[dict]) -> tuple[str, list[str]]:
    """Build a concise, prioritized prompt for page illustration.

    Uses key_characters from LLM annotation (only physically present characters).
    """
    scene = page.get("scene_description", page.get("prompt", ""))
    text = page.get("text", "")
    scene_direction = page.get("scene_direction", "")
    page_num = page.get("page_number", "?")
    key_characters = page.get("key_characters", [])

    # Use LLM-annotated characters directly (only physically present + their actions)
    in_scene_names = list(key_characters)
    character_actions = page.get("character_actions", [])

    # Build character + action description
    if character_actions:
        char_lines = []
        for ca in character_actions:
            name = ca.get("name", "") if isinstance(ca, dict) else ca
            action = ca.get("action", "") if isinstance(ca, dict) else ""
            char_lines.append(f"- {name}: {action}" if action else f"- {name}")
            if name not in in_scene_names:
                in_scene_names.append(name)
        char_block = "\n".join(char_lines)
    else:
        char_block = "\n".join(f"- {n}" for n in in_scene_names) if in_scene_names else "no specific characters"

    background = page.get("scene_background", "")

    summary = page.get("scene_summary", "")

    prompt = f"""Children's picture book illustration, page {page_num}.

IMPORTANT: Draw ONE single scene, ONE single moment in time. Do NOT split the image into multiple panels or scenes.

SCENE:
{summary}

BACKGROUND/SETTING:
{background or scene_direction or scene}
Draw a rich, detailed environment. Fill the ENTIRE image. Historically accurate, no modern objects.

CHARACTERS AND ACTIONS:
{char_block}
- ONLY draw these characters. No one else.
- EACH CHARACTER APPEARS EXACTLY ONCE. NEVER draw the same person twice. Count the characters listed above — that is the exact number of people in the image.
- Each character MUST be performing their described action — show movement, expression, body language.

CHARACTER APPEARANCE (match reference sheets EXACTLY):
- COPY each character's hair color, hairstyle, outfit, accessories from their reference sheet above.
- Do NOT change any visual detail. The reference sheets are the ground truth.

NAME LABELS:
- Small wooden sign or ribbon on the ground directly below each character's feet.
- Every character MUST have a name label.

STORY TEXT:
"{text}"
Embed naturally: speech bubbles for dialogue, scrolls/banners for narration. Spell every word correctly.

Style: {DEFAULT_STYLE}
Do NOT include: {NEGATIVE_PROMPT}"""

    return prompt, in_scene_names


def _extract_image(response: object, save_path: Path) -> bool:
    """Save the first image from a Gemini response to disk."""
    if not response.candidates:
        return False
    for part in response.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            mime_type = part.inline_data.mime_type or "image/png"
            ext = ".jpg" if "jpeg" in mime_type or "jpg" in mime_type else ".png"
            final_path = save_path.with_suffix(ext)
            final_path.write_bytes(part.inline_data.data)
            logger.info("Saved illustration to %s", final_path)
            return True
    return False


def _generate_single_page(
    client,
    page: dict,
    valid_sheets: list[dict],
    save_path: Path,
    style_ref_path: str | None = None,
    scene_sheet_path: str | None = None,
) -> tuple[bool, str, str]:
    """Generate a single page illustration. Returns (success, image_path, prompt_used)."""
    prompt_text, in_scene_names = _build_page_prompt(page, valid_sheets)

    contents = _build_reference_content(prompt_text, valid_sheets, style_ref_path, in_scene_names, scene_sheet_path)

    try:
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=genai.types.ImageConfig(
                    aspect_ratio="1:1",
                ),
            ),
        )

        if _extract_image(response, save_path):
            for ext in (".png", ".jpg"):
                candidate = save_path.with_suffix(ext)
                if candidate.exists():
                    return True, str(candidate), prompt_text
        return False, "", prompt_text

    except Exception as e:
        error_str = str(e).lower()
        if any(kw in error_str for kw in ["rate limit", "429", "resource exhausted"]):
            time.sleep(3 + random.uniform(0, 2))
        logger.warning("Generation failed for %s: %s", save_path.name, e)
        return False, "", prompt_text


def _find_scene_sheet(book_id: str, scene_background: str) -> str | None:
    """Find the best matching scene sheet for a given scene_background description."""
    import json
    import re

    scenes_dir = GENERATED_DIR / book_id / "scenes"
    if not scenes_dir.exists():
        return None

    # Load locations
    locs_path = GENERATED_DIR / book_id / "preprocess" / "llm_locations.json"
    if not locs_path.exists():
        return None

    try:
        locations = json.loads(locs_path.read_text(encoding="utf-8")).get("locations", [])
    except Exception:
        return None

    bg_lower = scene_background.lower()

    # Match location by name or aliases appearing in the scene_background
    for loc in locations:
        name = loc.get("name", "")
        aliases = loc.get("aliases", [])
        all_names = [name] + aliases

        for n in all_names:
            if n.lower() in bg_lower:
                safe = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name)
                safe = re.sub(r'\s+', '_', safe.strip()).lower()[:50]
                for ext in (".png", ".jpg"):
                    path = scenes_dir / f"{safe}_scene{ext}"
                    if path.exists():
                        return str(path)
                break

    return None


def generate_illustrations(
    page_prompts: list[dict],
    character_sheets: list[dict],
    book_id: str,
    style_ref_path: str | None = None,
    pages_dir: str | Path | None = None,
) -> list[dict]:
    """Generate illustrations — one shot per page, no retries.

    Args:
        page_prompts: List of page dicts.
        character_sheets: Character sheet dicts with 'sheet_path'.
        book_id: Unique book identifier.
        style_ref_path: Optional style reference image.
        pages_dir: Override output directory for pages.
    """
    client = _get_client()
    output_dir = Path(pages_dir) if pages_dir else GENERATED_DIR / book_id / "pages"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-use book cover as style reference if not provided
    if not style_ref_path:
        from src.generation.special_pages import _find_book_cover
        style_ref_path = _find_book_cover(book_id)
        if style_ref_path:
            logger.info("Using book cover as style reference: %s", style_ref_path)

    valid_sheets = [s for s in character_sheets if s.get("sheet_path") and Path(s["sheet_path"]).exists()]
    logger.info("Using %d character sheet references", len(valid_sheets))

    results: list[dict] = []

    for page in page_prompts:
        page_num = page.get("page_number", len(results) + 1)
        save_path = output_dir / f"page_{page_num:03d}"

        # Checkpoint: skip if image already exists
        existing = None
        for ext in (".png", ".jpg"):
            candidate = save_path.with_suffix(ext)
            if candidate.exists():
                existing = str(candidate)
                break

        if existing:
            logger.info("Page %d: already exists, skipping (%s)", page_num, existing)
            results.append({
                "page_number": page_num,
                "image_path": existing,
                "prompt_used": "(cached)",
            })
            continue

        # Find matching scene background sheet
        scene_bg = page.get("scene_background", "")
        scene_sheet = _find_scene_sheet(book_id, scene_bg) if scene_bg else None
        if scene_sheet:
            logger.info("Page %d: using scene sheet %s", page_num, Path(scene_sheet).name)

        success, image_path, prompt = _generate_single_page(
            client, page, valid_sheets, save_path, style_ref_path, scene_sheet
        )

        results.append({
            "page_number": page_num,
            "image_path": image_path if success else "",
            "prompt_used": prompt,
        })

        if success:
            logger.info("Page %d: saved to %s", page_num, image_path)
        else:
            logger.warning("Page %d: generation failed", page_num)

    logger.info("Generated %d/%d illustrations", sum(1 for r in results if r["image_path"]), len(results))
    return results

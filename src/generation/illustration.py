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
from src.generation.image_utils import _get_client, _load_image_part, save_inline_image

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
            # Exact full-name match only. The old first-name/last-name/substring
            # fallbacks cross-matched e.g. "Madame Defarge" into a scene with only
            # "Monsieur Defarge", injecting the wrong reference sheet as COPY EXACTLY.
            is_match = any(name_lower == n.lower() for n in in_scene_names)
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

    # Build name label spelling instructions
    name_labels_block = ""
    if in_scene_names:
        label_lines = []
        for n in in_scene_names:
            spelled = "-".join(n.upper())
            label_lines.append(f'- "{n}" (spell exactly: {spelled})')
        name_labels_block = "\n".join(label_lines)

    prompt = f"""Children's picture book illustration.

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
- Small wooden sign or ribbon below each character's feet.
- Every character MUST have a name label.
- Spell each name EXACTLY as shown below. Do NOT add numbers, suffixes, or modify names in any way.
{name_labels_block}

STORY TEXT:
"{text}"
Embed naturally: speech bubbles for dialogue, scrolls/banners for narration.

STRICT TEXT RULES:
- Spell every word EXACTLY as provided above. Do NOT rephrase, abbreviate, or improvise any text.
- Do NOT add any text that is not in the STORY TEXT or NAME LABELS above.
- Do NOT write page numbers, "Page X", or any metadata in the image.
- Do NOT add numbers after character names (no "Jordan Baker 2", just "Jordan Baker").
- If you are unsure how to spell a word, copy it letter by letter from above.

Style: {DEFAULT_STYLE}
Do NOT include: {NEGATIVE_PROMPT}"""

    return prompt, in_scene_names


def _extract_image(response: object, save_path: Path) -> bool:
    """Save the first image from a Gemini response to disk."""
    final_path = save_inline_image(response, save_path)
    if final_path:
        logger.info("Saved illustration to %s", final_path)
        return True
    return False


def _collect_reference_paths(
    valid_sheets: list[dict],
    in_scene_names: list[str] | None,
    style_ref_path: str | None,
    scene_sheet_path: str | None,
) -> list[str]:
    """Collect all reference image paths for alicloud provider."""
    paths = []
    if style_ref_path and Path(style_ref_path).exists():
        paths.append(style_ref_path)
    # Matched character sheets
    for sheet in valid_sheets:
        if len(paths) >= 4:
            break
        sp = sheet.get("sheet_path", "")
        if not sp or not Path(sp).exists():
            continue
        if in_scene_names:
            name_lower = sheet.get("character_name", "").lower()
            if any(n.lower() in name_lower or name_lower in n.lower() for n in in_scene_names):
                paths.append(sp)
        else:
            paths.append(sp)
    if scene_sheet_path and Path(scene_sheet_path).exists() and len(paths) < 4:
        paths.append(scene_sheet_path)
    return paths[:4]


def _generate_single_page(
    client,
    page: dict,
    valid_sheets: list[dict],
    save_path: Path,
    style_ref_path: str | None = None,
    scene_sheet_path: str | None = None,
    correction_feedback: str | None = None,
) -> tuple[bool, str, str]:
    """Generate a single page illustration. Returns (success, image_path, prompt_used)."""
    from src.config import IMAGE_LLM

    prompt_text, in_scene_names = _build_page_prompt(page, valid_sheets)
    if correction_feedback:
        prompt_text += (
            "\n\nQUALITY REVIEW FEEDBACK — a previous version of this page failed review. "
            "Fix the issues below. If the feedback quotes story text that differs from the "
            "story text above, the story text above is authoritative.\n"
            + correction_feedback
        )

    if IMAGE_LLM == "alicloud":
        from src.generation.alicloud_image import generate_image_alicloud
        ref_paths = _collect_reference_paths(valid_sheets, in_scene_names, style_ref_path, scene_sheet_path)
        result = generate_image_alicloud(prompt_text, save_path, reference_images=ref_paths)
        if result:
            return True, result, prompt_text
        return False, "", prompt_text

    # Default: Gemini
    contents = _build_reference_content(prompt_text, valid_sheets, style_ref_path, in_scene_names, scene_sheet_path)

    for attempt in range(MAX_RETRIES):
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
            is_rate_limit = any(kw in error_str for kw in ["rate limit", "429", "resource exhausted"])
            if is_rate_limit and attempt < MAX_RETRIES - 1:
                wait = (2 ** attempt) * 5 + random.uniform(0, 3)
                logger.warning("Rate limited on %s, retry %d/%d in %.1fs",
                               save_path.name, attempt + 1, MAX_RETRIES, wait)
                time.sleep(wait)
                continue
            logger.warning("Generation failed for %s: %s", save_path.name, e)
            return False, "", prompt_text

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
    correction_feedback: str | None = None,
) -> list[dict]:
    """Generate illustrations — one shot per page, no retries.

    Args:
        page_prompts: List of page dicts.
        character_sheets: Character sheet dicts with 'sheet_path'.
        book_id: Unique book identifier.
        style_ref_path: Optional style reference image.
        pages_dir: Override output directory for pages.
        correction_feedback: QA feedback injected into the prompt when
            regenerating a page that failed quality review.
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
            client, page, valid_sheets, save_path, style_ref_path, scene_sheet,
            correction_feedback=correction_feedback,
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

        # Throttle between real API calls to avoid free-tier rate limits.
        time.sleep(2)

    logger.info("Generated %d/%d illustrations", sum(1 for r in results if r["image_path"]), len(results))
    return results

"""Generate page illustrations using Gemini image generation with reference images.

Uses character sheet images and optional style reference images as visual
references for consistency — not just text prompts.
"""

import base64
import logging
import random
import time
from pathlib import Path

from google import genai

from src.config import (
    DEFAULT_STYLE,
    GEMINI_API_KEY,
    GEMINI_IMAGE_MODEL,
    GENERATED_DIR,
    NEGATIVE_PROMPT,
)

logger = logging.getLogger(__name__)

_client: genai.Client | None = None
MAX_RETRIES = 3


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


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


def _build_reference_content(
    prompt_text: str,
    character_sheets: list[dict],
    style_ref_path: str | None = None,
) -> list[dict]:
    """Build multi-part content with text prompt + reference images.

    This passes character sheet images and style reference directly to
    Gemini so it can visually reference them, not just read text descriptions.
    """
    parts = []

    # Add character sheet images as references
    sheet_images_added = 0
    for sheet in character_sheets:
        sheet_path = sheet.get("sheet_path", "")
        if not sheet_path:
            continue
        img_part = _load_image_part(sheet_path)
        if img_part:
            char_name = sheet.get("character_name", "character")
            parts.append({"text": f"[Reference: character sheet for {char_name}]"})
            parts.append(img_part)
            sheet_images_added += 1
            if sheet_images_added >= 5:
                break

    # Add style reference image if provided
    if style_ref_path:
        img_part = _load_image_part(style_ref_path)
        if img_part:
            parts.append({"text": "[Reference: target art style - match this visual style exactly]"})
            parts.append(img_part)

    # Add the actual generation prompt
    parts.append({"text": prompt_text})

    return parts


def _build_page_prompt(page: dict, character_sheets: list[dict]) -> str:
    """Build the text prompt with detailed scene + character identity."""
    scene = page.get("scene_description", page.get("prompt", ""))
    text = page.get("text", "")
    scene_direction = page.get("scene_direction", "")
    page_num = page.get("page_number", "?")
    key_characters = page.get("key_characters", [])

    # Build character identity descriptions from sheets, prioritizing characters in this scene
    char_identities = []
    scene_text_lower = (scene_direction + " " + text + " " + scene).lower()
    for sheet in character_sheets:
        name = sheet.get("character_name", "")
        vi = sheet.get("visual_identity", "")
        if not name or not vi:
            continue
        # Check if this character appears in the scene
        name_lower = name.lower()
        first_name = name_lower.split()[0]
        in_scene = (
            name_lower in scene_text_lower
            or first_name in scene_text_lower
            or name in key_characters
            or any(first_name == kc.lower().split()[0] for kc in key_characters if kc)
        )
        if in_scene:
            char_identities.insert(0, f"- {name} (IN THIS SCENE): {vi}")
        else:
            char_identities.append(f"- {name}: {vi}")

    char_block = "\n".join(char_identities) if char_identities else "See reference images above."

    prompt = f"""Generate a children's picture book illustration for page {page_num}.

SCENE: {scene_direction or scene}
STORY TEXT (embed this naturally into the illustration): "{text}"

CHARACTER VISUAL IDENTITIES — MANDATORY, MUST match EXACTLY in every detail:
{char_block}

CONSISTENCY IS THE #1 PRIORITY:
- Each character MUST have the EXACT same hair color, hairstyle, clothing, and features as described above
- If a character wears glasses, they MUST wear glasses in EVERY scene
- If a character has a red sweater, they MUST have a red sweater in EVERY scene
- Do NOT change any character's appearance — refer to the character sheets above

CHARACTER NAME LABELS:
- Add a small name tag near each character's feet or below them
- Style: a small colored ribbon or wooden sign — NOT a speech bubble, NOT a dialogue box
- Example: a tiny curved ribbon with "Mr. Lorry" written on it, placed below the character
- Name labels must look DIFFERENT from dialogue and narration text
- Make sure the name spelling is CORRECT

TEXT STYLE GUIDE (3 distinct styles, must be visually different):
1. CHARACTER NAMES → small ribbons/signs BELOW characters (subtle, small font)
2. DIALOGUE → speech bubbles with tails pointing to the speaker
3. NARRATION → scrolls, banners, or cloud shapes at top/bottom of page

TEXT IN IMAGE:
- Embed the story text naturally into the illustration as part of the art
- Use creative placements: inside clouds, on scrolls, in speech bubbles, on banners, in open sky areas
- The text should feel like a natural part of the scene, not a separate overlay
- Make sure the text is LEGIBLE and CORRECTLY SPELLED
- Double-check every word for spelling accuracy

BACKGROUND AND SETTING:
- Draw a RICH, DETAILED background — NOT just white/empty space
- Include environmental details: furniture, plants, sky, weather, textures
- Use warm, inviting colors that set the mood of the scene
- The setting must be HISTORICALLY ACCURATE — buildings, streets, interiors should match the time period
- NO modern objects (no cars, no electricity, no phones, no plastic)

RULES:
- Characters MUST be HUMAN, matching reference sheets exactly
- Characters should be expressive — show emotions through face and body
- Fill the ENTIRE image with illustration — no empty white areas
- Clothing MUST be period-appropriate (match the era of the story, not modern clothes)
- Each character must wear the SAME outfit in EVERY illustration (consistency!)

Style: {DEFAULT_STYLE}
Do NOT include: {NEGATIVE_PROMPT}"""

    return prompt


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
) -> tuple[bool, str, str]:
    """Generate a single page illustration. Returns (success, image_path, prompt_used)."""
    prompt_text = _build_page_prompt(page, valid_sheets)

    contents = _build_reference_content(prompt_text, valid_sheets, style_ref_path)

    try:
        response = client.models.generate_content(
            model=GEMINI_IMAGE_MODEL,
            contents=contents,
            config=genai.types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
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


def generate_illustrations(
    page_prompts: list[dict],
    character_sheets: list[dict],
    book_id: str,
    style_ref_path: str | None = None,
    consistency_threshold: float = 0.55,
    max_consistency_retries: int = 2,
) -> list[dict]:
    """Generate illustrations with automatic consistency checking.

    For each page:
    1. Generate the illustration
    2. Check CLIP similarity against character sheets
    3. If below threshold, regenerate (up to max_consistency_retries times)
    4. Keep the best version

    Args:
        page_prompts: List of page dicts.
        character_sheets: Character sheet dicts with 'sheet_path'.
        book_id: Unique book identifier.
        style_ref_path: Optional style reference image.
        consistency_threshold: CLIP similarity threshold (0-1).
        max_consistency_retries: Max regeneration attempts per page.
    """
    from src.generation.consistency_check import check_consistency, CLIP_AVAILABLE

    client = _get_client()
    output_dir = GENERATED_DIR / book_id / "pages"
    output_dir.mkdir(parents=True, exist_ok=True)

    valid_sheets = [s for s in character_sheets if s.get("sheet_path") and Path(s["sheet_path"]).exists()]
    logger.info("Using %d character sheet references", len(valid_sheets))

    results: list[dict] = []

    for page in page_prompts:
        page_num = page.get("page_number", len(results) + 1)
        save_path = output_dir / f"page_{page_num:03d}"

        best_path = ""
        best_score = -1.0
        best_prompt = ""

        for attempt in range(max_consistency_retries + 1):
            # Generate
            if attempt > 0:
                save_path_retry = output_dir / f"page_{page_num:03d}_v{attempt}"
                logger.info("Page %d: regenerating (attempt %d, prev score=%.3f)",
                           page_num, attempt + 1, best_score)
            else:
                save_path_retry = save_path

            success, image_path, prompt = _generate_single_page(
                client, page, valid_sheets, save_path_retry, style_ref_path
            )

            if not success:
                continue

            # Check consistency with CLIP
            if CLIP_AVAILABLE and valid_sheets:
                result = check_consistency(
                    [{"page_number": page_num, "image_path": image_path}],
                    valid_sheets,
                )
                score = result.get("per_page_scores", [-1])[0]
            else:
                score = 1.0  # Skip check if CLIP not available

            logger.info("Page %d attempt %d: consistency score=%.3f (threshold=%.2f)",
                       page_num, attempt + 1, score, consistency_threshold)

            # Keep the best version
            if score > best_score:
                best_score = score
                best_path = image_path
                best_prompt = prompt

            # Good enough — stop retrying
            if score >= consistency_threshold:
                break

        # If best version is a retry file, copy it to the canonical path
        canonical = save_path.with_suffix(Path(best_path).suffix if best_path else ".png")
        if best_path and best_path != str(canonical):
            import shutil
            shutil.copy2(best_path, canonical)
            best_path = str(canonical)

        results.append({
            "page_number": page_num,
            "image_path": best_path,
            "prompt_used": best_prompt,
            "consistency_score": best_score,
        })

        if best_score < consistency_threshold and best_score >= 0:
            logger.warning("Page %d: best consistency score %.3f still below threshold %.2f",
                         page_num, best_score, consistency_threshold)

    # Summary
    scores = [r["consistency_score"] for r in results if r["consistency_score"] >= 0]
    avg = sum(scores) / len(scores) if scores else 0
    flagged = sum(1 for s in scores if s < consistency_threshold)
    logger.info("Consistency summary: avg=%.3f, %d/%d pages flagged", avg, flagged, len(scores))

    return results

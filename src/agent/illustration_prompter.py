"""Illustration prompt generator: creates detailed image generation prompts."""

import json
import logging

from src.config import DEFAULT_STYLE, NEGATIVE_PROMPT
from src.llm_client import generate_json

logger = logging.getLogger(__name__)

ILLUSTRATION_SYSTEM = """\
You are an expert art director for children's picture books. You create detailed, \
vivid illustration prompts that will be used with AI image generation models.

Your prompts must be visually specific, child-friendly, and maintain character \
consistency across pages. You must return valid JSON matching the requested schema exactly."""

CHARACTER_SHEET_PROMPT = """\
Generate character sheet prompts for the main characters of a children's picture book.

## Style
{style}

## Negative prompt (do NOT include these elements)
{negative_prompt}

## Characters
{characters_json}

## Output Format
Return a JSON object with a single key "character_sheets" containing a list of objects. \
Each object must have:
- "character_name": the character's name
- "prompt": a detailed prompt for generating a character reference sheet. The prompt \
should describe:
  - Front view and 3/4 view of the character
  - 3-4 facial expressions (happy, sad, surprised, determined)
  - The character's distinctive features (clothing, accessories, colors, body shape)
  - The art style to use
  - The prompt should start with the style prefix: "{style}"
"""

PAGE_ILLUSTRATION_PROMPT = """\
Generate detailed illustration prompts for each page of a children's picture book.

## Style
{style}

## Negative prompt (do NOT include these elements)
{negative_prompt}

## Character Visual Identities (MUST be included in EVERY prompt where that character appears)
{characters_json}

## Pages
{pages_json}

## Output Format
Return a JSON object with a single key "page_prompts" containing a list of objects. \
Each object must have:
- "page_number": integer matching the page
- "prompt": a detailed illustration prompt that includes:
  - The style prefix at the start
  - Scene description based on page_text and scene_summary
  - EXACT character visual identity for EACH character in the scene (copy their appearance description verbatim)
  - Emotional color palette (colors that match the emotional_tone)
  - Composition direction (e.g., "centered", "character on left looking right", \
"wide establishing shot", "close-up on face")
  - Environmental details and lighting
  - Embed the story text naturally into the illustration (clouds, scrolls, speech bubbles)
- "composition_notes": brief notes on layout, focal point, and visual storytelling

CRITICAL: Every prompt MUST describe each character's EXACT appearance (hair, outfit, features) \
so the image generator can maintain consistency. Do NOT just say "Nick" — say \
"Nick, a young man with short straight dark brown hair, round glasses, wearing a bright red sweater and blue jeans".

Each prompt must start with: "{style}"
"""


def generate_illustration_prompts(
    scenes: list[dict],
    character_profiles: list[dict],
    style: str | None = None,
) -> dict:
    """Generate illustration prompts for character sheets and each page.

    Args:
        scenes: List of scenes with page_text, scene_summary, emotional_tone, etc.
        character_profiles: List of character profile dicts with name, description,
                           appearance, personality, etc.
        style: Override the default illustration style prefix.

    Returns:
        Dict with:
            - character_sheet_prompts: list of {character_name, prompt}
            - page_prompts: list of {page_number, prompt, composition_notes}
    """
    active_style = style or DEFAULT_STYLE

    # Generate character sheet prompts
    character_sheets = _generate_character_sheets(character_profiles, active_style)

    # Generate page illustration prompts
    page_prompts = _generate_page_prompts(scenes, character_profiles, active_style)

    return {
        "character_sheet_prompts": character_sheets,
        "page_prompts": page_prompts,
    }


def _generate_character_sheets(
    character_profiles: list[dict],
    style: str,
) -> list[dict]:
    """Generate character reference sheet prompts."""
    if not character_profiles:
        logger.info("No character profiles provided. Skipping character sheets.")
        return []

    prompt = CHARACTER_SHEET_PROMPT.format(
        style=style,
        negative_prompt=NEGATIVE_PROMPT,
        characters_json=json.dumps(character_profiles, indent=2, default=str),
    )

    result = generate_json(prompt, system=ILLUSTRATION_SYSTEM)
    sheets = result.get("character_sheets", [])

    # Ensure each prompt starts with the style prefix
    for sheet in sheets:
        if not sheet.get("prompt", "").startswith(style):
            sheet["prompt"] = f"{style}, {sheet.get('prompt', '')}"

    return sheets


def _generate_page_prompts(
    scenes: list[dict],
    character_profiles: list[dict],
    style: str,
) -> list[dict]:
    """Generate per-page illustration prompts."""
    if not scenes:
        logger.info("No scenes provided. Skipping page prompts.")
        return []

    # Build character identity block with visual descriptions
    char_identity_list = []
    for cp in character_profiles:
        name = cp.get("name", "Unknown")
        vi = cp.get("visual_identity", cp.get("visual_description", ""))
        appearance = cp.get("appearance", cp.get("appearance_description", []))
        if isinstance(appearance, list):
            appearance = "; ".join(appearance[:3])
        entry = {"name": name, "visual_identity": vi, "appearance": appearance}
        char_identity_list.append(entry)

    prompt = PAGE_ILLUSTRATION_PROMPT.format(
        style=style,
        negative_prompt=NEGATIVE_PROMPT,
        characters_json=json.dumps(char_identity_list, indent=2, default=str),
        pages_json=json.dumps(scenes, indent=2, default=str),
    )

    result = generate_json(prompt, system=ILLUSTRATION_SYSTEM)
    page_prompts = result.get("page_prompts", [])

    # Ensure each prompt starts with the style prefix
    for pp in page_prompts:
        if not pp.get("prompt", "").startswith(style):
            pp["prompt"] = f"{style}, {pp.get('prompt', '')}"

    return page_prompts

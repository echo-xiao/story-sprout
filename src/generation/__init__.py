"""Image generation pipeline for picture books.

Orchestrates character sheet creation, page illustration, and
optional CLIP-based consistency checking.
"""

import logging

from src.generation.character_sheet import generate_character_sheets
from src.generation.consistency_check import check_consistency
from src.generation.illustration import generate_illustrations

logger = logging.getLogger(__name__)

__all__ = [
    "generate_character_sheets",
    "generate_illustrations",
    "check_consistency",
    "generate_all_images",
]


def generate_all_images(prompts: dict, book_id: str) -> dict:
    """Run the full image generation pipeline for a picture book.

    Steps:
        1. Generate character reference sheets.
        2. Generate page illustrations (referencing character sheets).
        3. Run CLIP consistency check (optional, best-effort).

    Args:
        prompts: Dictionary containing:
            - "characters": list[dict] — character prompt dicts for
              generate_character_sheets (each with 'name', 'description', etc.)
            - "pages": list[dict] — page prompt dicts for
              generate_illustrations (each with 'page_number',
              'scene_description', and optionally 'text')
        book_id: Unique identifier for the book.

    Returns:
        Dictionary with:
            - character_sheets: list[dict] from generate_character_sheets()
            - illustrations: list[dict] from generate_illustrations()
            - consistency: dict from check_consistency()
    """
    character_prompts: list[dict] = prompts.get("characters", [])
    page_prompts: list[dict] = prompts.get("pages", [])

    # Step 1: Character sheets
    logger.info(
        "Generating character sheets for %d characters...",
        len(character_prompts),
    )
    character_sheets = generate_character_sheets(character_prompts, book_id)

    # Step 2: Page illustrations
    logger.info(
        "Generating illustrations for %d pages...", len(page_prompts)
    )
    illustrations = generate_illustrations(page_prompts, character_sheets, book_id)

    # Step 3: Consistency check (best-effort)
    logger.info("Running consistency check...")
    try:
        consistency = check_consistency(illustrations, character_sheets)
    except Exception as e:
        logger.warning("Consistency check failed: %s", e)
        consistency = {
            "overall_score": -1.0,
            "per_page_scores": [],
            "flagged_pages": [],
        }

    return {
        "character_sheets": character_sheets,
        "illustrations": illustrations,
        "consistency": consistency,
    }

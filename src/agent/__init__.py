"""Agent layer: Gemini-powered creative decision-making pipeline."""

import logging

from src.agent.scene_selector import select_scenes
from src.agent.text_simplifier import simplify_text
from src.agent.illustration_prompter import generate_illustration_prompts

logger = logging.getLogger(__name__)


def run_agent_pipeline(
    analysis: dict,
    num_pages: int,
    age_group: str,
    template: str = "classic",
) -> dict:
    """Run the full agent pipeline: scene selection -> text simplification -> illustration prompts.

    Args:
        analysis: Full analysis result from Layer 2 containing segments,
                  characters, themes, original_text, etc.
        num_pages: Desired number of pages for the picture book.
        age_group: Target age group (e.g., "2-4", "4-6", "6-8").
        template: Story template to follow (e.g., "classic", "journey", "simple").

    Returns:
        Dict with:
            - scenes: list of scene dicts with page_text, word_count, reading_level
            - illustration_prompts: dict with character_sheet_prompts and page_prompts
            - metadata: dict with pipeline configuration
    """
    logger.info(
        "Starting agent pipeline: %d pages, age %s, template '%s'",
        num_pages,
        age_group,
        template,
    )

    # Step 1: Select scenes
    logger.info("Step 1/3: Selecting scenes...")
    scenes = select_scenes(analysis, num_pages, age_group, template)
    logger.info("Selected %d scenes.", len(scenes))

    # Step 2: Simplify text
    logger.info("Step 2/3: Simplifying text for age group %s...", age_group)
    original_text = analysis.get("original_text", "")
    scenes = simplify_text(scenes, age_group, original_text)
    logger.info("Text simplification complete.")

    # Step 3: Generate illustration prompts
    logger.info("Step 3/3: Generating illustration prompts...")
    character_profiles = analysis.get("characters", [])
    illustration_prompts = generate_illustration_prompts(scenes, character_profiles)
    logger.info(
        "Generated %d character sheets and %d page prompts.",
        len(illustration_prompts.get("character_sheet_prompts", [])),
        len(illustration_prompts.get("page_prompts", [])),
    )

    return {
        "scenes": scenes,
        "illustration_prompts": illustration_prompts,
        "metadata": {
            "num_pages": len(scenes),
            "age_group": age_group,
            "template": template,
        },
    }

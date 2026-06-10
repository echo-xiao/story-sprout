"""Writer Agent: text simplification for target age group.

Responsible for:
- Rewriting original text into children's picture book narration
- Maintaining story faithfulness while simplifying vocabulary
- Generating scene directions for the Artist Agent
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class WriterAgent:
    """Rewrites adult literature into age-appropriate picture book text."""

    def __init__(self, age_group: str = "4-6", language: str = "en"):
        self.age_group = age_group
        self.language = language

    def simplify(
        self,
        scenes: list[dict],
        characters: list[dict] | None = None,
        character_sheets: list[dict] | None = None,
    ) -> list[dict]:
        """Simplify scene text for children.

        Processes one scene at a time with previous page context
        for narrative continuity.

        Args:
            scenes: List of scene dicts with original_text, key_characters, etc.
            characters: Character profiles for richer dialogue.
            character_sheets: Visual identity info for scene_direction.

        Returns:
            List of simplified scene dicts with page_text and scene_direction.
        """
        from src.agent.text_simplifier import simplify_text

        print(f"\n[Writer Agent] Simplifying {len(scenes)} pages (age {self.age_group})...")
        t0 = time.time()
        simplified = simplify_text(
            scenes, self.age_group,
            language=self.language,
            characters=characters,
            character_sheets=character_sheets,
        )
        dt = time.time() - t0
        print(f"  Done in {dt:.1f}s")

        return simplified

    def build_prompts(self, simplified: list[dict]) -> list[dict]:
        """Build illustration prompt data from simplified scenes.

        Template-based, no LLM call. Prepares structured data
        for the Artist Agent.
        """
        print(f"\n[Writer Agent] Building illustration prompts ({len(simplified)} pages)...")
        page_prompts = []
        for s in simplified:
            page_prompts.append({
                "page_number": s.get("page_number", 0),
                "text": s.get("page_text", s.get("text", "")),
                "scene_description": s.get("scene_direction", s.get("scene_summary", "")),
                "scene_direction": s.get("scene_direction", ""),
                "scene_background": s.get("scene_background", ""),
                "key_characters": s.get("key_characters", []),
                "character_actions": s.get("character_actions", []),
            })
        return page_prompts

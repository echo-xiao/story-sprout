"""Analyzer Agent: text extraction, NLP analysis, and scene selection.

Responsible for:
- Extracting text from source files (PDF, EPUB, TXT)
- Running NLP analysis (segmentation, character extraction, sentiment, complexity)
- Building scene list from analyzed segments
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.config import GENERATED_DIR

logger = logging.getLogger(__name__)


class AnalyzerAgent:
    """Analyzes source text and prepares scenes for the picture book."""

    def __init__(self, book_id: str):
        self.book_id = book_id
        self.preprocess_dir = GENERATED_DIR / book_id / "preprocess"

    def load_preprocess(self) -> dict:
        """Load all preprocessed data for a book."""
        if not self.preprocess_dir.exists():
            print(f"Error: No preprocessed data found at {self.preprocess_dir}")
            print(f"Run: python scripts/preprocess_book.py --input <book_file>")
            sys.exit(1)

        data = {}
        for name in ["meta", "chapters", "full_text", "analysis", "chapter_segments"]:
            path = self.preprocess_dir / f"{name}.json"
            if path.exists():
                data[name] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def get_chapter_segments(self, data: dict, chapter_idx: int) -> tuple[list[dict], str]:
        """Get segments for a specific chapter. Returns (segments, chapter_title)."""
        analysis = data.get("analysis", {})
        all_segments = analysis.get("segments", [])
        chapter_segments_map = data.get("chapter_segments", {})

        ch_info = chapter_segments_map.get(str(chapter_idx), {})
        ch_title = ch_info.get("chapter_title", f"Chapter {chapter_idx + 1}")
        seg_ids = set(ch_info.get("segment_ids", []))

        if seg_ids:
            segments = [s for s in all_segments if s.get("id") in seg_ids]
        else:
            segments = all_segments

        return segments, ch_title

    def build_scenes(self, segments: list[dict], characters: list[dict]) -> list[dict]:
        """Build scene list from analyzed segments.

        Each segment becomes a page with characters, summary, and original text.
        Uses precomputed characters_in_scene from coreference resolution when available.
        """
        scenes = []
        char_names = [c["name"] for c in characters[:10]]

        for i, seg in enumerate(segments):
            seg_text = seg.get("text", "")
            if len(seg_text.split()) < 10:
                continue

            # Prefer precomputed coreference results
            present_chars = seg.get("characters_in_scene")
            if present_chars is None:
                text_lower = seg_text.lower()
                present_chars = [n for n in char_names if n.lower() in text_lower]

            sentences = [s.strip() for s in seg_text.replace("\n", " ").split(".") if s.strip()]
            summary = ". ".join(sentences[:2]) + "." if sentences else seg_text[:200]

            scenes.append({
                "page_number": len(scenes) + 1,
                "source_segment_id": seg.get("id", i),
                "scene_summary": seg.get("scene_summary", summary),
                "scene_background": seg.get("scene_background", ""),
                "key_characters": present_chars[:5],
                "character_actions": seg.get("character_actions", []),
                "original_text": seg_text,
            })

        return scenes

    def get_chapter_characters(
        self, data: dict, segments: list[dict]
    ) -> tuple[set[str], list[dict]]:
        """Find which characters appear in the given segments.

        Returns (character_name_set, matching_profiles).
        """
        analysis = data.get("analysis", {})
        characters = analysis.get("characters", [])
        profiles = analysis.get("character_profiles", [])

        char_names = [c["name"] for c in characters[:10]]
        chapter_char_names: set[str] = set()

        for seg in segments:
            seg_text = seg.get("text", "")
            if len(seg_text.split()) < 10:
                continue
            present = seg.get("characters_in_scene")
            if present is None:
                text_lower = seg_text.lower()
                present = [n for n in char_names if n.lower() in text_lower]
            for name in (present or [])[:5]:
                chapter_char_names.add(name)

        chapter_profiles = [p for p in profiles if p.get("name") in chapter_char_names]
        return chapter_char_names, chapter_profiles

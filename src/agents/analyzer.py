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
        """Load all preprocessed data for a book.

        Read path priority (each best-effort, falling through on failure):
          1. MongoDB MCP server (Model Context Protocol) — the partner
             integration: data is fetched via the official mongodb-mcp-server
             over stdio.
          2. Direct pymongo read of the same preprocess_files documents.
          3. Local JSON files on disk.
        Preprocess writes each JSON doc to MongoDB and disk, so all three
        return the identical structure — no field mapping needed.
        """
        names = ["meta", "chapters", "full_text", "analysis", "chapter_segments"]
        data: dict = {}

        # 1) MongoDB MCP server (partner integration).
        try:
            from src.core.mcp_client import load_preprocess_files_via_mcp
            mcp_data = load_preprocess_files_via_mcp(self.book_id, names)
            if mcp_data:
                data.update(mcp_data)
                logger.info("load_preprocess: %d/%d docs via MongoDB MCP server for %s",
                            len(mcp_data), len(names), self.book_id)
        except Exception as e:
            logger.warning("load_preprocess: MCP path unavailable (%s)", e)

        # 2) Direct pymongo fallback for anything MCP didn't return.
        if len(data) < len(names):
            try:
                from src.core.db import load_preprocess_file, is_available
                if is_available():
                    for name in names:
                        if name in data:
                            continue
                        doc = load_preprocess_file(self.book_id, f"{name}.json")
                        if doc is not None:
                            data[name] = doc
            except Exception as e:
                logger.warning("load_preprocess: pymongo fallback failed (%s)", e)

        # 3) Local file fallback.
        for name in names:
            if name in data:
                continue
            path = self.preprocess_dir / f"{name}.json"
            if path.exists():
                data[name] = json.loads(path.read_text(encoding="utf-8"))

        if not data:
            # This is a library method (also imported into the server process),
            # so raise instead of sys.exit(1) — a bare exit would kill the whole
            # API if this is ever called outside the generate_chapter subprocess.
            raise FileNotFoundError(
                f"No preprocessed data found for '{self.book_id}' "
                f"(checked MongoDB MCP, MongoDB, and {self.preprocess_dir}). "
                f"Run: python scripts/preprocess_book.py --input <book_file>"
            )

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
                "page_number": i + 1,
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
        # Reference sheets are only generated for recurring characters: one-off
        # "minor" names (e.g. Gatsby's chapter-4 guest list) would each cost a
        # portrait + sheet image call while appearing on a single page. They are
        # rendered from their text description instead (same policy as the
        # preprocess path, preprocessing/pipeline.py). Missing role defaults to
        # "supporting" so books preprocessed before roles existed keep working.
        chapter_profiles = [
            p for p in chapter_profiles
            if p.get("role", "supporting") in ("main", "supporting")
        ]
        return chapter_char_names, chapter_profiles

"""Analyzer Agent: text extraction, NLP analysis, and scene selection.

Responsible for:
- Extracting text from source files (PDF, EPUB, TXT)
- Running NLP analysis (segmentation, character extraction, sentiment, complexity)
- Building scene list from analyzed segments
"""

from __future__ import annotations

import logging

from src.config import GENERATED_DIR
from src.core.provenance import effective_source

logger = logging.getLogger(__name__)


class AnalyzerAgent:
    """Analyzes source text and prepares scenes for the picture book."""

    def __init__(self, book_id: str):
        self.book_id = book_id
        self.preprocess_dir = GENERATED_DIR / book_id / "preprocess"

    def load_preprocess(self) -> dict:
        """Load all preprocessed data for a book.

        Every file is resolved through the ONE shared accessor
        helpers._load_json (Mongo-authoritative + freshness heal + file
        fallback) — the exact same read strategy the web routes use, so the
        subprocess and the editor can never resolve to different versions.

        The MongoDB-MCP server (the partner integration) is read once as a batch
        prefetch and handed to the accessor as a same-Mongo fallback candidate;
        it is NOT a separate read strategy — the accessor still decides authority
        and healing in one place.
        """
        names = ["meta", "chapters", "full_text", "analysis", "chapter_segments"]

        # MongoDB MCP batch prefetch (partner integration) — best-effort; an
        # alternate transport to the SAME preprocess_files docs. Passed to the
        # accessor below as a fallback, never as an override.
        mcp_data: dict = {}
        try:
            from src.core.mcp_client import load_preprocess_files_via_mcp
            mcp_data = load_preprocess_files_via_mcp(self.book_id, names) or {}
            if mcp_data:
                logger.info("load_preprocess: %d/%d docs prefetched via MongoDB MCP server for %s",
                            len(mcp_data), len(names), self.book_id)
        except Exception as e:
            logger.warning("load_preprocess: MCP path unavailable (%s)", e)

        from src.routes.helpers import _load_json
        data: dict = {}
        for name in names:
            val = _load_json(self.book_id, f"{name}.json", prefetched=mcp_data.get(name))
            if val is not None:
                data[name] = val

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
                # Carry existing page text (user-edited or from a previous run)
                # so the Writer stage can keep it instead of re-simplifying —
                # re-running a chapter used to silently overwrite edits and
                # diverge from the text already painted into cached page images.
                "simplified_text": seg.get("simplified_text", ""),
                "scene_direction": seg.get("scene_direction", ""),
                # Provenance rides along so the Writer split keeps user/Writer
                # text but still re-simplifies robotic preprocess text.
                "text_source": effective_source(seg),
            })

        return scenes

    def get_chapter_characters(
        self, data: dict, segments: list[dict]
    ) -> tuple[set[str], list[dict]]:
        """Find which characters appear in the given segments.

        Returns (character_name_set, matching_profiles).
        """
        # Single source: the consistency hub (not analysis.json's stale
        # character_profiles copy), so an edited appearance is honoured here.
        from src.routes.helpers import load_character_profiles
        profiles = load_character_profiles(self.book_id)

        char_names = [p["name"] for p in profiles[:10]]
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

#!/usr/bin/env python3
"""Generate a picture book chapter using the multi-agent pipeline.

Agents:
- AnalyzerAgent: loads preprocessed data, builds scenes
- WriterAgent: simplifies text for target age group
- ArtistAgent: generates character sheets + illustrations
- QAAgent: per-page quality checks + chapter summary

Usage:
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0
    python scripts/generate_chapter.py --book The_Great_Gatsby --chapter 0 --pages 1,2,3
    python scripts/generate_chapter.py --book The_Great_Gatsby --cover-only
    python scripts/generate_chapter.py --book The_Great_Gatsby --pdf-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.config import GENERATED_DIR

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
)


def _update_progress(book_id: str, chapter_idx: int, **kwargs) -> None:
    """Write progress.json for frontend polling."""
    progress_file = GENERATED_DIR / book_id / "chapters" / f"ch{chapter_idx:02d}" / "progress.json"
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if progress_file.exists():
        try:
            existing = json.loads(progress_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(kwargs)
    progress_file.write_text(json.dumps(existing))


def generate_chapter(
    book_id: str,
    data: dict,
    chapter_idx: int,
    page_filter: list[int] | None = None,
    age_group: str = "4-6",
    self_correct: bool = False,
) -> dict | None:
    """Generate a chapter by running the ADK SequentialAgent pipeline.

    The four agents (Analyzer -> Artist setup -> Writer -> Artist + QA) are
    orchestrated by Google's Agent Development Kit; see src/agents/adk_pipeline.py.
    """
    from src.agents.adk_pipeline import run_adk_pipeline
    return run_adk_pipeline(
        book_id, data, chapter_idx,
        page_filter=page_filter, age_group=age_group, self_correct=self_correct,
    )


def build_combined_pdf(book_id: str, data: dict, chapter_indices: list[int] | None = None):
    """Build combined PDF from all generated chapters."""
    from src.renderer.pdf_export import export_pdf

    title = data.get("meta", {}).get("title", "Untitled")
    chapters_root = GENERATED_DIR / book_id / "chapters"
    special_dir = str(GENERATED_DIR / book_id / "special")

    chapter_dirs = sorted(chapters_root.glob("ch*"))
    if not chapter_dirs:
        print("No chapters found to combine.")
        return

    all_chapters = []
    for ch_dir in chapter_dirs:
        data_file = ch_dir / "chapter_data.json"
        if data_file.exists():
            ch_data = json.loads(data_file.read_text(encoding="utf-8"))
            ch_idx = ch_data.get("chapter_idx", 0)
            if chapter_indices and ch_idx not in chapter_indices:
                continue
            all_chapters.append(ch_data)

    if not all_chapters:
        print("No matching chapters found.")
        return

    all_chapters.sort(key=lambda c: c.get("chapter_idx", 0))

    combined_pages = []
    chapter_nums = []
    for ch in all_chapters:
        ch_num = ch.get("chapter_idx", 0) + 1
        chapter_nums.append(ch_num)
        for p in ch.get("pages", []):
            p["_chapter_num"] = ch_num
            combined_pages.append(p)

    pdf_path = str(GENERATED_DIR / book_id / "book.pdf")
    export_pdf(combined_pages, title, pdf_path, special_dir=special_dir, chapter_nums=chapter_nums)

    print(f"\n=== Combined PDF ===")
    print(f"  Chapters: {[c.get('chapter_title', '?') for c in all_chapters]}")
    print(f"  Total pages: {len(combined_pages)}")
    print(f"  PDF: {pdf_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a picture book chapter.")
    parser.add_argument("--book", required=True, help="Book ID (folder name in data/generated/)")
    parser.add_argument("--chapter", type=str, default=None,
                        help="Chapter index (0-based). Comma-separated: 0,4")
    parser.add_argument("--pages", type=str, default=None, help="Comma-separated page numbers")
    parser.add_argument("--age", type=str, default="4-6", help="Target age group")
    parser.add_argument("--special-only", action="store_true", help="Only generate special pages")
    parser.add_argument("--cover-only", action="store_true", help="Only generate book cover")
    parser.add_argument("--pdf-only", action="store_true", help="Only rebuild PDF")
    parser.add_argument("--self-correct", action="store_true",
                        help="Auto-regenerate pages whose QA score is below 50 (max 1 retry per page)")
    args = parser.parse_args()

    from src.agents.analyzer import AnalyzerAgent
    analyzer = AnalyzerAgent(args.book)
    data = analyzer.load_preprocess()

    if args.cover_only:
        from src.agents.artist import ArtistAgent
        profiles = data.get("analysis", {}).get("character_profiles", [])
        main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
        if not main_chars:
            main_chars = profiles[:5]
        title = data.get("meta", {}).get("title", "Untitled")
        artist = ArtistAgent(args.book)
        artist.generate_book_cover(title, main_chars)
        return

    if args.special_only:
        from src.agents.artist import ArtistAgent
        artist = ArtistAgent(args.book)
        ch = int(args.chapter) if args.chapter else None
        if ch is not None:
            segments, _ = analyzer.get_chapter_segments(data, ch)
            artist.ensure_special_pages(data, ch, segments)
            artist.ensure_ending_pages(data, ch, segments)
        else:
            # Generate book cover + back cover only
            profiles = data.get("analysis", {}).get("character_profiles", [])
            main_chars = [p for p in profiles if p.get("role") in ("main", "supporting")][:5]
            if not main_chars:
                main_chars = profiles[:5]
            title = data.get("meta", {}).get("title", "Untitled")
            artist.generate_book_cover(title, main_chars)
            artist.generate_back_cover(title)
        return

    if args.pdf_only:
        ch_list = [int(c.strip()) for c in args.chapter.split(",")] if args.chapter else None
        build_combined_pdf(args.book, data, ch_list)
        return

    if args.chapter is None:
        print("Error: --chapter is required (unless using --special-only, --cover-only, or --pdf-only)")
        sys.exit(1)

    chapter_indices = [int(c.strip()) for c in args.chapter.split(",")]
    page_filter = [int(p.strip()) for p in args.pages.split(",")] if args.pages else None

    for ch_idx in chapter_indices:
        generate_chapter(args.book, data, ch_idx, page_filter=page_filter, age_group=args.age,
                         self_correct=args.self_correct)

    # Rebuild the PDF from ALL generated chapters, not just the one(s) just
    # generated — otherwise regenerating one chapter clobbers the full-book PDF.
    build_combined_pdf(args.book, data)


if __name__ == "__main__":
    main()
